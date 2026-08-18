from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

PheraRole = Literal["api", "worker", "all"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://phera:phera@localhost:5432/phera"
    redis_url: str | None = "redis://localhost:6379/0"
    log_level: str = "INFO"

    otel_enabled: bool = True
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    otel_service_name: str = "phera"
    deployment_environment: str = "development"

    phera_role: PheraRole = "all"
    phera_host: str = "127.0.0.1"
    phera_port: int = 8010
    phera_db_slow_ms: int = 200
    worker_queues: str = "workflow,delayed,lifecycle,communication,maintenance"

    gallabox_api_key: str = ""
    gallabox_api_secret: str = ""
    gallabox_api_endpoint: str = "https://server.gallabox.com/devapi/messages/whatsapp"
    gallabox_account_id: str = ""
    gallabox_channel_id: str = ""
    gallabox_webhook_secret: str = ""
    gallabox_whatsapp_number: str = ""

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True
    email_webhook_secret: str = ""
    email_inbound_address: str = ""

    credentials_encryption_key: str = ""

    @property
    def worker_queue_list(self) -> list[str]:
        return [q.strip() for q in self.worker_queues.split(",") if q.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
