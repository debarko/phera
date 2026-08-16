"""OpenTelemetry setup — traces, metrics, slow SQL. Must run before heavy imports."""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.metrics import Counter, Histogram, Meter
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from sqlalchemy import event
from sqlalchemy.engine import Engine

from phera.settings import get_settings

logger = logging.getLogger(__name__)

_initialized = False
_meter: Meter | None = None
_mutate_duration: Histogram | None = None
_mutate_total: Counter | None = None
_slow_query_total: Counter | None = None
_db_op_duration: Histogram | None = None
_worker_job_duration: Histogram | None = None
_worker_job_errors: Counter | None = None


def _is_otel_enabled() -> bool:
    explicit = os.getenv("OTEL_ENABLED")
    if explicit is not None:
        return explicit.lower() not in ("0", "false", "no", "off")
    return get_settings().otel_enabled


def _sanitize_sql(statement: str, max_len: int = 500) -> str:
    s = re.sub(r"\s+", " ", statement.strip())
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


def _register_slow_query_listener(engine: Engine, slow_ms: int) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        conn.info.setdefault("query_start_time", []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        start_stack = conn.info.get("query_start_time")
        if not start_stack:
            return
        start = start_stack.pop()
        duration_ms = (time.perf_counter() - start) * 1000

        span = trace.get_current_span()
        if span.is_recording():
            span.set_attribute("db.statement", _sanitize_sql(statement))
            span.set_attribute("db.duration_ms", duration_ms)

        if _db_op_duration:
            op = statement.split()[0].upper() if statement else "UNKNOWN"
            _db_op_duration.record(duration_ms / 1000, {"db.operation": op})

        if duration_ms >= slow_ms:
            if _slow_query_total:
                _slow_query_total.add(1)
            if span.is_recording():
                span.add_event(
                    "db.slow_query",
                    {
                        "db.statement": _sanitize_sql(statement),
                        "db.duration_ms": duration_ms,
                    },
                )
            logger.warning("slow query (%.1fms): %s", duration_ms, _sanitize_sql(statement, 200))


def init_otel(*, role: str | None = None, sqlalchemy_engine: Engine | None = None) -> bool:
    global _initialized, _meter
    global _mutate_duration, _mutate_total, _slow_query_total, _db_op_duration
    global _worker_job_duration, _worker_job_errors

    if _initialized:
        return True

    if not _is_otel_enabled():
        logger.info("[OTEL] disabled")
        _initialized = True
        return False

    settings = get_settings()
    endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/")
    if not endpoint:
        logger.warning("[OTEL] no endpoint; disabled")
        _initialized = True
        return False

    try:
        set_global_textmap(
            CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
        )

        resource = Resource.create(
            {
                "service.name": settings.otel_service_name,
                "service.version": "0.1.0",
                "deployment.environment": settings.deployment_environment,
                "phera.role": role or settings.phera_role,
            }
        )

        trace_provider = TracerProvider(resource=resource)
        trace_exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", timeout=5)
        trace_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
        trace.set_tracer_provider(trace_provider)

        metric_exporter = OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", timeout=5)
        reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=60000)
        meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(meter_provider)

        _meter = metrics.get_meter("phera", "0.1.0")
        _mutate_duration = _meter.create_histogram("phera.mutate.duration", unit="s")
        _mutate_total = _meter.create_counter("phera.mutate.total")
        _slow_query_total = _meter.create_counter("phera.db.slow_query.total")
        _db_op_duration = _meter.create_histogram(
            "phera.db.client.operation.duration", unit="s"
        )
        _worker_job_duration = _meter.create_histogram("phera.worker.job.duration", unit="s")
        _worker_job_errors = _meter.create_counter("phera.worker.job.errors")

        HTTPXClientInstrumentor().instrument()
        RedisInstrumentor().instrument()
        if sqlalchemy_engine is not None:
            SQLAlchemyInstrumentor().instrument(engine=sqlalchemy_engine)
            _register_slow_query_listener(sqlalchemy_engine, settings.phera_db_slow_ms)

        _initialized = True
        logger.info("[OTEL] initialized endpoint=%s role=%s", endpoint, role)
        return True
    except Exception:
        logger.exception("[OTEL] init failed; continuing without telemetry")
        _initialized = True
        return False


def instrument_fastapi(app: Any) -> None:
    if _is_otel_enabled():
        FastAPIInstrumentor.instrument_app(app)


def record_mutate(entity_type: str, action: str, duration_s: float) -> None:
    if _mutate_duration:
        _mutate_duration.record(duration_s, {"entity_type": entity_type, "action": action})
    if _mutate_total:
        _mutate_total.add(1, {"entity_type": entity_type, "action": action})


def record_worker_job(queue: str, job_type: str, duration_s: float, error: bool = False) -> None:
    attrs = {"queue": queue, "job_type": job_type}
    if _worker_job_duration:
        _worker_job_duration.record(duration_s, attrs)
    if error and _worker_job_errors:
        _worker_job_errors.add(1, attrs)


def get_tracer(name: str = "phera"):
    return trace.get_tracer(name, "0.1.0")


def start_span(name: str, **attrs: Any) -> Span:
    tracer = get_tracer()
    span = tracer.start_span(name)
    for k, v in attrs.items():
        if v is not None:
            span.set_attribute(k, v)
    return span
