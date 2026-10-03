from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import ACTIVE_INCIDENT_STATUSES
from app.models import Incident
from app.schemas.payment import (
    CaptureRequest,
    CaptureResponse,
    CreatePayPalOrderRequest,
    CreatePayPalOrderResponse,
    PaymentOut,
    PaymentStatusOut,
    RefundRequest,
)
from app.services import paypal_service
from app.services.payment_service import PaymentService
from app.workers.dispatcher import dispatch_job

router = APIRouter(prefix="/payments", tags=["paypal"])


@router.post("/paypal/create-order", response_model=CreatePayPalOrderResponse, status_code=201)
async def create_order(body: CreatePayPalOrderRequest, request: Request,
                       db: AsyncSession = Depends(get_db)) -> CreatePayPalOrderResponse:
    created = await paypal_service.create_paypal_payment(
        db, amount=body.amount, demo=body.demo, failure_scenarios=body.failure_scenarios,
        negative_test=body.negative_test, description=body.description, return_origin=request.headers.get("origin"),
    )
    return CreatePayPalOrderResponse(
        payment=PaymentOut.model_validate(created.payment), order_id=created.order_id,
        approve_url=created.approve_url,
        armed_failures=[s for s in body.failure_scenarios if s and s != "NONE"],
    )


@router.post("/paypal/capture", response_model=CaptureResponse)
async def capture(body: CaptureRequest, db: AsyncSession = Depends(get_db)) -> CaptureResponse:
    result = await paypal_service.capture_paypal_payment(
        db, transaction_id=body.transaction_id, order_id=body.order_id, trigger="return-url"
    )
    mode = None
    if result.status == "CAPTURED":
        mode = await dispatch_job("post_capture", (result.payment.transaction_id,))
    elif result.status == "PROVIDER_FAILED":
        mode = await dispatch_job("reconcile", (result.payment.transaction_id, "capture-failed"))
    return CaptureResponse(payment=PaymentOut.model_validate(result.payment), status=result.status,
                           message=result.message, pipeline_mode=mode)


@router.get("/{transaction_id}/status", response_model=PaymentStatusOut)
async def status(transaction_id: str, db: AsyncSession = Depends(get_db)) -> PaymentStatusOut:
    payment = await PaymentService(db).get_by_transaction_id(transaction_id)
    open_incident = await db.scalar(
        select(Incident.id).where(Incident.payment_id == payment.id,
                                  Incident.status.in_([str(s) for s in ACTIVE_INCIDENT_STATUSES]))
        .order_by(Incident.created_at.desc()).limit(1)
    )
    return PaymentStatusOut(
        transaction_id=payment.transaction_id, provider=payment.provider, provider_status=payment.provider_status,
        overall_status=payment.overall_status, gateway_status=payment.gateway_status, bank_status=payment.bank_status,
        merchant_status=payment.merchant_status, ledger_status=payment.ledger_status,
        webhook_status=payment.webhook_status, reconciliation_status=payment.reconciliation_status,
        captured=bool(payment.provider_capture_id), open_incident_id=open_incident,
    )


@router.post("/{transaction_id}/refund-request")
async def refund_request(transaction_id: str, body: RefundRequest | None = None, db: AsyncSession = Depends(get_db)) -> dict:
    body = body or RefundRequest()
    payment = await PaymentService(db).get_by_transaction_id(transaction_id)
    created = await paypal_service.request_refund(db, payment, reason=body.reason, by=body.requested_by)
    await db.commit()
    mode = await dispatch_job("reconcile", (transaction_id, "refund-requested")) if created else None
    return {"ok": True, "transaction_id": transaction_id, "requested": created, "pipeline_mode": mode,
            "message": "Refund request recorded; PayFlow will investigate and apply policy"
            if created else "Refund already requested"}
