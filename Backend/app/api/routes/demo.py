from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.payment import CreatePayPalOrderResponse, PaymentOut
from app.services import paypal_service
from app.services.demo_service import seed_demo_data, truncate_all

router = APIRouter(prefix="/demo", tags=["demo"])


class LiveDemoRequest(BaseModel):
    demo: str = Field(default="LEDGER_MISMATCH", pattern="^(PAYMENT_ONLY|LEDGER_MISMATCH|REFUND_REQUIRES_APPROVAL)$")
    amount: Decimal = Field(default=Decimal("50.00"), gt=0, le=1000)
    failure_scenario: str | None = None
    verification_timeout: bool = False
    negative_test: str | None = None


async def _start(db: AsyncSession, body: LiveDemoRequest) -> CreatePayPalOrderResponse:
    failures: list[str] = []
    if body.failure_scenario and body.failure_scenario != "NONE":
        failures.append(body.failure_scenario)
    elif body.demo == "LEDGER_MISMATCH":
        failures.append("LEDGER_WRITE_FAILURE")
    if body.verification_timeout:
        failures.append("VERIFICATION_TIMEOUT")
    created = await paypal_service.create_paypal_payment(
        db, amount=body.amount, demo=body.demo, failure_scenarios=failures, negative_test=body.negative_test,
    )
    return CreatePayPalOrderResponse(payment=PaymentOut.model_validate(created.payment), order_id=created.order_id,
                                     approve_url=created.approve_url, armed_failures=failures)


@router.post("/live", response_model=CreatePayPalOrderResponse, status_code=201)
async def live_demo(body: LiveDemoRequest, db: AsyncSession = Depends(get_db)) -> CreatePayPalOrderResponse:
    """Start a live demo: real PayPal Sandbox order + (optional) armed PayFlow failure injection."""
    return await _start(db, body)


@router.post("/real-time-ledger-mismatch", response_model=CreatePayPalOrderResponse, status_code=201)
async def ledger_mismatch_demo(body: LiveDemoRequest | None = None, db: AsyncSession = Depends(get_db)) -> CreatePayPalOrderResponse:
    body = body or LiveDemoRequest()
    body.demo = "LEDGER_MISMATCH"
    return await _start(db, body)


@router.post("/reset")
async def reset_demo() -> dict:
    counts = await seed_demo_data()
    return {"ok": True, "message": "Demo data reset: historical PayFlow incidents reseeded", **counts}


@router.post("/clear")
async def clear_demo(db: AsyncSession = Depends(get_db)) -> dict:
    """Wipe all data without reseeding. Leaves a completely fresh database."""
    await truncate_all(db)
    return {"ok": True, "message": "All data cleared. Database is now empty."}
