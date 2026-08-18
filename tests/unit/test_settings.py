from __future__ import annotations

from phera.settings import Settings, get_settings


def test_worker_queue_list_parsing():
    settings = Settings(worker_queues="workflow, delayed ,lifecycle")
    assert settings.worker_queue_list == ["workflow", "delayed", "lifecycle"]


def test_phera_host_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("PHERA_HOST", raising=False)
    monkeypatch.delenv("PHERA_PORT", raising=False)
    settings = Settings(_env_file=None)
    assert settings.phera_host == "127.0.0.1"
    assert settings.phera_port == 8010


def test_get_settings_is_cached():
    get_settings.cache_clear()
    a = get_settings()
    b = get_settings()
    assert a is b
    get_settings.cache_clear()
