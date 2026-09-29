import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services import paypal_service
from app.workers.dispatcher import dispatch_job

logger = logging.getLogger(__name__)
router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/paypal")
async def paypal_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """PayPal webhook intake.

    Stores the exact raw body and transmission headers, returns 2xx fast, and
    verifies + processes asynchronously. Redeliveries of the same event id are
    detected and never trigger a second financial action.
    """
    raw = (await request.body()).decode("utf-8")
    row, duplicate = await paypal_service.ingest_webhook(db, headers=dict(request.headers), raw_body=raw)
    if duplicate:
        return {"ok": True, "duplicate": True, "event_id": row.provider_event_id}
    mode = await dispatch_job("process_webhook", (str(row.id),))
    return {"ok": True, "duplicate": False, "event_id": row.provider_event_id, "queued": mode}
