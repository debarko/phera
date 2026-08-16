from __future__ import annotations

import pytest

from phera.settings import Settings, get_settings


def test_worker_queue_list_parsing():
    settings = Settings(worker_queues="workflow, delayed ,lifecycle")
    assert settings.worker_queue_list == ["workflow", "delayed", "lifecycle"]


def test_get_settings_is_cached():
    get_settings.cache_clear()
    a = get_settings()
    b = get_settings()
    assert a is b
    get_settings.cache_clear()
