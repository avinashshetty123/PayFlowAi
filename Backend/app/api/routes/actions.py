import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import ActionStatus
from app.schemas.action import ActionDecisionResponse, ActionOut, ApprovalQueueItem, ApproveRequest, RejectRequest
from app.services import query_service
from app.services.orchestrator import ActionStageResult, approve_action, reject_action

router = APIRouter(prefix="/actions", tags=["actions"])


def _response(result: ActionStageResult) -> ActionDecisionResponse:
    return ActionDecisionResponse(
        action=ActionOut.model_validate(result.action),
        incident_status=result.incident_status,
        verification=result.verification,
        deduplicated=result.deduplicated,
        message=result.message,
    )


@router.get("", response_model=list[ApprovalQueueItem])
async def list_actions(
    view: Literal["pending", "decided"] = "pending", db: AsyncSession = Depends(get_db)
) -> list[ApprovalQueueItem]:
    return await query_service.approval_queue(db, ActionStatus.PENDING_APPROVAL if view == "pending" else None)


@router.post("/{action_id}/approve", response_model=ActionDecisionResponse)
async def approve(
    action_id: uuid.UUID, body: ApproveRequest | None = None, db: AsyncSession = Depends(get_db)
) -> ActionDecisionResponse:
    body = body or ApproveRequest()
    return _response(await approve_action(db, action_id, approver=body.approver, note=body.note))


@router.post("/{action_id}/reject", response_model=ActionDecisionResponse)
async def reject(
    action_id: uuid.UUID, body: RejectRequest | None = None, db: AsyncSession = Depends(get_db)
) -> ActionDecisionResponse:
    body = body or RejectRequest()
    return _response(await reject_action(db, action_id, approver=body.approver, reason=body.reason))
