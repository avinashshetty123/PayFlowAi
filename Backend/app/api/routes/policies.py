from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.incident_graph import describe
from app.core.config import settings
from app.core.database import get_db
from app.core.errors import NotFoundError
from app.models import Incident
from app.services import control_service, orchestrator
from app.services.payment_service import PaymentService
from app.services.playbook import ALLOWED_ACTIONS, playbook_action
from app.services.policy_service import POLICY_VERSION, RULES, PolicyEngine
from app.services.risk_service import HUMAN_THRESHOLD

router = APIRouter(tags=["policies"])


class KillSwitchRequest(BaseModel):
    enabled: bool
    reason: str = Field(min_length=3, max_length=300)
    by: str = Field(default="ops.manager", min_length=2, max_length=64)


class SimulateRequest(BaseModel):
    transaction_id: str
    action: str | None = None
    human_approved: bool = False


@router.get("/policies")
async def policies(db: AsyncSession = Depends(get_db)) -> dict:
    return {
        "version": POLICY_VERSION,
        "rules": RULES,
        "playbook": {kind: [a.value for a in actions] for kind, actions in ALLOWED_ACTIONS.items()},
        "thresholds": {
            "refund_auto_approve_usd": settings.REFUND_AUTO_APPROVE_LIMIT_USD,
            "refund_auto_approve_inr": settings.REFUND_AUTO_APPROVE_LIMIT,
            "min_ai_confidence": settings.MIN_AUTOMATION_CONFIDENCE,
            "risk_score_human_threshold": HUMAN_THRESHOLD,
        },
        "kill_switch": await control_service.kill_switch(db),
        "circuit_breaker": await control_service.automation_rate(db),
    }


@router.post("/policies/kill-switch")
async def set_kill_switch(body: KillSwitchRequest, db: AsyncSession = Depends(get_db)) -> dict:
    return await control_service.set_kill_switch(db, enabled=body.enabled, reason=body.reason, by=body.by)


@router.post("/policies/simulate")
async def simulate(body: SimulateRequest, db: AsyncSession = Depends(get_db)) -> dict:
    """What-if: evaluate policy for a payment without changing anything."""
    payment = await PaymentService(db).get_by_transaction_id(body.transaction_id)
    incident = await db.scalar(select(Incident).where(Incident.payment_id == payment.id)
                               .order_by(Incident.created_at.desc()).limit(1))
    if incident is None:
        raise NotFoundError(f"No incident for {body.transaction_id}; nothing to decide")
    action = body.action or incident.recommended_action or str(playbook_action(incident.type))
    ctx, risk = await orchestrator._policy_context(db, incident, payment, human_approved=body.human_approved, action=action)
    evaluation = PolicyEngine().evaluate(action, ctx)
    response = {"transaction_id": payment.transaction_id, "incident_number": incident.incident_number,
                "incident_type": incident.type, "evaluation": evaluation.model_dump(), "risk": risk.as_dict()}
    await db.rollback()  # strictly read-only
    return response


@router.get("/agent/graph")
async def agent_graph() -> dict:
    return describe()
