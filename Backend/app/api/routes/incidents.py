import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import IncidentStatus
from app.core.errors import NotFoundError
from app.models import Incident
from app.schemas.incident import (
    AuditLogOut,
    IncidentDetail,
    IncidentListResponse,
    InvestigateResponse,
    TimelineItem,
)
from app.services import query_service
from app.services.orchestrator import run_incident_pipeline
from app.workers.dispatcher import dispatch_incident_pipeline

router = APIRouter(prefix="/incidents", tags=["incidents"])


async def resolve_incident_id(db: AsyncSession, ref: str) -> uuid.UUID:
    """Accept either a UUID or a human incident number (INC-1042)."""
    try:
        return uuid.UUID(ref)
    except ValueError:
        found = await db.scalar(select(Incident.id).where(Incident.incident_number == ref.upper()))
        if found is None:
            raise NotFoundError(f"Incident {ref} not found") from None
        return found


@router.get("", response_model=IncidentListResponse)
async def list_incidents(
    status: str | None = None,
    active: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> IncidentListResponse:
    items, total = await query_service.list_incident_summaries(db, limit=limit, offset=offset, status=status, active=active)
    return IncidentListResponse(items=items, total=total)


@router.get("/{incident_ref}", response_model=IncidentDetail)
async def get_incident(incident_ref: str, db: AsyncSession = Depends(get_db)) -> IncidentDetail:
    return await query_service.incident_detail(db, await resolve_incident_id(db, incident_ref))


@router.post("/{incident_ref}/investigate", response_model=InvestigateResponse, status_code=202)
async def investigate(
    incident_ref: str,
    background_tasks: BackgroundTasks,
    sync: bool = False,
    db: AsyncSession = Depends(get_db),
) -> InvestigateResponse:
    incident_id = await resolve_incident_id(db, incident_ref)
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise NotFoundError(f"Incident {incident_ref} not found")
    if incident.status == IncidentStatus.RESOLVED:
        return InvestigateResponse(incident_id=incident_id, status=incident.status, pipeline_mode="none",
                                   message="Incident already resolved; nothing to do")
    if sync:
        status = await run_incident_pipeline(incident_id, delay=0)
        return InvestigateResponse(incident_id=incident_id, status=status or "ESCALATED", pipeline_mode="sync",
                                   message="Investigation pipeline completed")
    mode = await dispatch_incident_pipeline(incident_id, background_tasks)
    return InvestigateResponse(incident_id=incident_id, status=incident.status, pipeline_mode=mode,
                               message="Investigation queued")


@router.get("/{incident_ref}/timeline", response_model=list[TimelineItem])
async def timeline(incident_ref: str, db: AsyncSession = Depends(get_db)) -> list[TimelineItem]:
    return await query_service.incident_timeline(db, await resolve_incident_id(db, incident_ref))


@router.get("/{incident_ref}/audit", response_model=list[AuditLogOut])
async def audit(incident_ref: str, db: AsyncSession = Depends(get_db)) -> list[AuditLogOut]:
    return await query_service.incident_audit(db, await resolve_incident_id(db, incident_ref))
