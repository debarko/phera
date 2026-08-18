from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from phera.security import crypto
from phera.settings import Settings


def _use_key(monkeypatch, key: str | None) -> None:
    settings = Settings(credentials_encryption_key=key or "")
    monkeypatch.setattr(crypto, "get_settings", lambda: settings)


def test_encrypt_decrypt_round_trip(monkeypatch):
    _use_key(monkeypatch, Fernet.generate_key().decode())
    token = crypto.encrypt_secrets({"password": "hunter2"})
    assert token != "hunter2"
    assert crypto.decrypt_secrets(token) == {"password": "hunter2"}


def test_decrypt_with_wrong_key_raises(monkeypatch):
    _use_key(monkeypatch, Fernet.generate_key().decode())
    token = crypto.encrypt_secrets({"password": "hunter2"})

    _use_key(monkeypatch, Fernet.generate_key().decode())
    with pytest.raises(crypto.CredentialDecryptionError):
        crypto.decrypt_secrets(token)


def test_missing_key_raises(monkeypatch):
    _use_key(monkeypatch, None)
    with pytest.raises(RuntimeError):
        crypto.encrypt_secrets({"password": "x"})


def test_encrypt_empty_dict_round_trips(monkeypatch):
    _use_key(monkeypatch, Fernet.generate_key().decode())
    token = crypto.encrypt_secrets({})
    assert crypto.decrypt_secrets(token) == {}
