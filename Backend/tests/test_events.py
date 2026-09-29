"""Real-time event bus: publish-after-commit semantics and SSE framing."""

import asyncio
import json

from app.core.database import SessionLocal
from app.events import bus
from app.events.bus import emit, local, recent_events
from app.events.stream import _local_events, _sse
from app.services.audit_service import AuditService


async def test_events_publish_only_after_commit(db):
    queue = local.subscribe()
    try:
        async with SessionLocal() as session:
            emit(session, "PAYMENT_CREATED", transaction_id="TXN-EVT-1", data={"x": 1})
            assert queue.empty()  # nothing leaks before commit
            await session.commit()
        event = queue.get_nowait()
        assert event["event"] == "PAYMENT_CREATED" and event["transactionId"] == "TXN-EVT-1"
        assert set(event) >= {"id", "event", "transactionId", "incidentId", "timestamp", "data"}
    finally:
        local.unsubscribe(queue)


async def test_rolled_back_events_are_discarded(db):
    queue = local.subscribe()
    try:
        async with SessionLocal() as session:
            await AuditService(session).record(transaction_id="TXN-EVT-2", event="INCIDENT_CREATED",
                                               actor="test", reason="will be rolled back")
            await session.rollback()
            await session.commit()
        assert queue.empty()
    finally:
        local.unsubscribe(queue)


async def test_audit_records_stream_with_mapped_names(db):
    queue = local.subscribe()
    try:
        async with SessionLocal() as session:
            await AuditService(session).record(transaction_id="TXN-EVT-3", event="AI_RECOMMENDATION_CREATED",
                                               actor="ai", reason="root cause found")
            await AuditService(session).record(transaction_id="TXN-EVT-3", event="VERIFICATION_PASSED",
                                               actor="verifier", reason="ok")
            await session.commit()
        names = [queue.get_nowait()["event"] for _ in range(2)]
        assert names == ["INVESTIGATION_COMPLETED", "VERIFICATION_COMPLETED"]
    finally:
        local.unsubscribe(queue)


async def test_redis_outage_degrades_to_in_process(db):
    # conftest points REDIS_URL at an unused port: publishing must not raise.
    bus.publish_now(bus.make_event("WEBHOOK_RECEIVED", transaction_id="TXN-EVT-4"))
    backlog = await recent_events(10)
    assert any(e["transactionId"] == "TXN-EVT-4" for e in backlog)


async def test_sse_stream_frames_local_events():
    class FakeRequest:
        def __init__(self):
            self.calls = 0

        async def is_disconnected(self) -> bool:
            self.calls += 1
            return self.calls > 1

    stream = _local_events(FakeRequest())
    reader = asyncio.ensure_future(stream.__anext__())
    await asyncio.sleep(0)
    bus.publish_now(bus.make_event("INCIDENT_RESOLVED", transaction_id="TXN-EVT-5"))
    event = await asyncio.wait_for(reader, timeout=2)
    frame = _sse(event)
    assert frame.startswith("id: ") and "event: payflow" in frame
    assert json.loads(frame.split("data: ", 1)[1])["event"] == "INCIDENT_RESOLVED"
    await stream.aclose()
