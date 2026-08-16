"""Provider interfaces — core never imports vendor SDKs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class EmailProvider(ABC):
    @abstractmethod
    async def send(self, to: str, subject: str, body: str, **kwargs: Any) -> str:
        ...


class MessagingProvider(ABC):
    @abstractmethod
    async def send(self, to: str, body: str, template: str | None = None, **kwargs: Any) -> str:
        ...


class TelephonyProvider(ABC):
    @abstractmethod
    async def click_to_call(self, from_user: str, to_number: str, **kwargs: Any) -> dict:
        ...


class SmsProvider(ABC):
    @abstractmethod
    async def send(self, to: str, body: str, **kwargs: Any) -> str:
        ...


class TranscriptionProvider(ABC):
    @abstractmethod
    async def transcribe(self, recording_url: str, **kwargs: Any) -> dict:
        ...


class LifecycleProvider(ABC):
    @abstractmethod
    async def identify(self, contact: dict) -> None:
        ...

    @abstractmethod
    async def track(self, event_type: str, contact: dict, payload: dict) -> None:
        ...
