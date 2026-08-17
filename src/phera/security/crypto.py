"""Envelope encryption for connector secrets — Fernet, keyed by CREDENTIALS_ENCRYPTION_KEY."""

from __future__ import annotations

import json

from cryptography.fernet import Fernet, InvalidToken

from phera.settings import get_settings


class CredentialDecryptionError(Exception):
    """Raised when a stored secret cannot be decrypted (wrong/rotated key, corruption)."""


def _fernet() -> Fernet:
    key = get_settings().credentials_encryption_key
    if not key:
        raise RuntimeError(
            "CREDENTIALS_ENCRYPTION_KEY is not set — required to store/read connector secrets. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; "
            'print(Fernet.generate_key().decode())"'
        )
    return Fernet(key.encode())


def encrypt_secrets(payload: dict) -> str:
    token = _fernet().encrypt(json.dumps(payload).encode())
    return token.decode()


def decrypt_secrets(token: str) -> dict:
    try:
        raw = _fernet().decrypt(token.encode())
    except InvalidToken as exc:
        raise CredentialDecryptionError("Stored secret could not be decrypted") from exc
    return json.loads(raw)
