from __future__ import annotations

import pytest

from phera.modules.connectors.stubs import (
    MoEngageLifecycleProvider,
    StubEmailProvider,
    StubMessagingProvider,
    StubTelephonyProvider,
    StubTranscriptionProvider,
)


@pytest.mark.asyncio
class TestConnectorStubs:
    async def test_messaging_stub(self):
        msg_id = await StubMessagingProvider().send("+911234567890", "hello", "tpl-1")
        assert msg_id.startswith("stub-msg-")

    async def test_email_stub(self):
        msg_id = await StubEmailProvider().send("a@b.com", "Subject", "Body")
        assert msg_id.startswith("stub-email-")

    async def test_telephony_stub(self):
        result = await StubTelephonyProvider().click_to_call("staff-1", "+911234567890")
        assert "call_id" in result

    async def test_transcription_stub(self):
        result = await StubTranscriptionProvider().transcribe("https://example.com/rec.wav")
        assert result["status"] == "completed"

    async def test_moengage_lifecycle_stub(self):
        provider = MoEngageLifecycleProvider("app", "key")
        await provider.identify({"id": "c-1"})
        await provider.track("deal.created", {"id": "c-1"}, {"deal_id": "d-1"})
