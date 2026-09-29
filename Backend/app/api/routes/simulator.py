from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import Scenario
from app.models import Incident
from app.schemas.payment import PaymentOut, ReconciliationOut, SimulatePaymentRequest, SimulatePaymentResponse
from app.services.orchestrator import ingest_simulated_payment, run_incident_pipeline
from app.simulator.scenarios import SCENARIOS
from app.workers.dispatcher import dispatch_incident_pipeline

router = APIRouter(prefix="/simulator", tags=["simulator"])


@router.get("/scenarios")
async def list_scenarios() -> list[dict]:
    return [
        {
            "scenario": str(s),
            "description": spec.description,
            "expects_incident": spec.expects_incident,
            "systems": {"gateway": spec.gateway, "bank": spec.bank, "merchant": spec.merchant,
                        "ledger": spec.ledger, "webhook": spec.webhook},
        }
        for s, spec in SCENARIOS.items()
    ]


@router.post("/payments", response_model=SimulatePaymentResponse, status_code=status.HTTP_201_CREATED)
async def simulate_payment(
    body: SimulatePaymentRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)
) -> SimulatePaymentResponse:
    result = await ingest_simulated_payment(
        db, amount=body.amount, scenario=Scenario(body.scenario),
        transaction_id=body.transaction_id, customer_id=body.customer_id,
    )
    incident = result.incident
    mode = None
    if incident is not None:
        if body.sync:
            await run_incident_pipeline(incident.id, delay=0)
            incident = await db.get(Incident, incident.id, populate_existing=True)
            mode = "sync"
        else:
            mode = await dispatch_incident_pipeline(incident.id, background_tasks)

    payment = PaymentOut.model_validate(result.payment)
    if body.sync and incident is not None:
        await db.refresh(result.payment)
        payment = PaymentOut.model_validate(result.payment)
    return SimulatePaymentResponse(
        payment=payment,
        related_payment=PaymentOut.model_validate(result.related_payment) if result.related_payment else None,
        reconciliation=ReconciliationOut(**result.reconciliation.as_dict()),
        incident_id=incident.id if incident else None,
        incident_number=incident.incident_number if incident else None,
        incident_status=incident.status if incident else None,
        pipeline_mode=mode,
    )
