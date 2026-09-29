from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services import query_service
from app.services.orchestrator import detect
from app.services.payment_service import PaymentService
from app.workers.dispatcher import dispatch_incident_pipeline

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


@router.get("")
async def matrix(limit: int = Query(60, ge=1, le=200), db: AsyncSession = Depends(get_db)) -> dict:
    return await query_service.reconciliation_matrix(db, limit=limit)


@router.post("/run")
async def run(background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)) -> dict:
    """Deterministic sweep over all payments; opens incidents for new mismatches."""
    payments, total = await PaymentService(db).list_payments(limit=500)
    opened = []
    for payment in payments:
        result, incident = await detect(db, payment)
        if incident is not None and incident.status == "OPEN":
            opened.append(incident)
    await db.commit()
    modes = [await dispatch_incident_pipeline(incident.id, background_tasks) for incident in opened]
    return {
        "checked": len(payments),
        "total_payments": total,
        "new_incidents": [{"id": str(i.id), "incident_number": i.incident_number, "type": i.type} for i in opened],
        "pipeline_mode": modes[0] if modes else None,
    }
