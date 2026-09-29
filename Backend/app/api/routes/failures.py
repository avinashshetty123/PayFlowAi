from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.failure_injection.scenarios import SCENARIOS
from app.schemas.payment import InjectFailureRequest
from app.services import paypal_service
from app.services.payment_service import PaymentService
from app.workers.dispatcher import dispatch_job

router = APIRouter(prefix="/failures", tags=["failure-injection"])


@router.get("/scenarios")
async def scenarios() -> dict:
    webhooks = settings.paypal_webhooks_enabled
    return {
        "enabled": settings.ENABLE_FAILURE_INJECTION,
        "label": "DEMO FAILURE INJECTION",
        "note": "Failures are injected into PayFlow's own infrastructure. PayPal Sandbox results are never altered.",
        "webhooks_configured": webhooks,
        "scenarios": [
            {
                "scenario": str(info.scenario),
                "label": info.label,
                "stage": info.stage,
                "description": info.description,
                "requires_webhooks": info.requires_webhooks,
                "before_capture_only": info.before_capture_only,
                "expected_incident": info.expected_incident,
                "available": settings.ENABLE_FAILURE_INJECTION and (webhooks or not info.requires_webhooks),
            }
            for info in SCENARIOS.values()
        ],
    }


@router.post("/inject")
async def inject(body: InjectFailureRequest, db: AsyncSession = Depends(get_db)) -> dict:
    payment = await PaymentService(db).get_by_transaction_id(body.transaction_id)
    outcome = await paypal_service.inject_failure_now(db, payment, body.scenario, by=body.injected_by)
    await db.commit()
    mode = None
    if outcome.get("reconcile"):
        mode = await dispatch_job("reconcile", (payment.transaction_id, f"failure-injection:{body.scenario}"))
    return {"ok": True, "transaction_id": payment.transaction_id, "scenario": body.scenario,
            "source": "PAYFLOW_DEMO_ENVIRONMENT", "pipeline_mode": mode, **outcome}
