from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.incident import AuditListResponse, AuditLogOut
from app.services.audit_service import AuditService

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=AuditListResponse)
async def list_audit(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    event: str | None = None,
    search: str | None = Query(None, max_length=64),
    db: AsyncSession = Depends(get_db),
) -> AuditListResponse:
    rows, total = await AuditService(db).list_logs(limit=limit, offset=offset, event=event, search=search)
    return AuditListResponse(items=[AuditLogOut.model_validate(r) for r in rows], total=total)
