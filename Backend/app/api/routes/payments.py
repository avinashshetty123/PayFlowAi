from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.payment import (
    BankTransactionOut,
    LedgerEntryOut,
    MerchantTransactionOut,
    PaymentDetail,
    PaymentEventOut,
    PaymentIncidentRef,
    PaymentListResponse,
    PaymentOut,
)
from app.services import query_service
from app.services.payment_service import PaymentService

router = APIRouter(prefix="/payments", tags=["payments"])


@router.get("", response_model=PaymentListResponse)
async def list_payments(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    search: str | None = Query(None, max_length=64),
    provider: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> PaymentListResponse:
    items, total = await PaymentService(db).list_payments(limit=limit, offset=offset, status=status, search=search,
                                                          provider=provider)
    return PaymentListResponse(items=[PaymentOut.model_validate(p) for p in items], total=total)


@router.get("/{transaction_id}", response_model=PaymentDetail)
async def get_payment(transaction_id: str, db: AsyncSession = Depends(get_db)) -> PaymentDetail:
    service = PaymentService(db)
    payment = await service.get_by_transaction_id(transaction_id)
    return PaymentDetail(
        payment=PaymentOut.model_validate(payment),
        events=[PaymentEventOut.model_validate(e) for e in await service.events(payment.id)],
        bank_transactions=[BankTransactionOut.model_validate(b) for b in await service.bank_transactions(payment.id)],
        merchant_transactions=[
            MerchantTransactionOut.model_validate(m) for m in await service.merchant_transactions(payment.id)
        ],
        ledger_entries=[LedgerEntryOut.model_validate(entry) for entry in await service.ledger_entries(payment.id)],
        incidents=[
            PaymentIncidentRef(id=i.id, incident_number=i.incident_number, type=i.type, status=i.status, severity=i.severity)
            for i in await service.incidents(payment.id)
        ],
        provider_transactions=await query_service.provider_transactions_out(db, payment.id),
        webhook_events=await query_service.webhook_events_out(db, payment.transaction_id),
        failure_injections=await query_service.failure_injections_out(db, payment.id),
        approve_url=(payment.provider_metadata or {}).get("approve_url") if not payment.provider_capture_id else None,
    )
