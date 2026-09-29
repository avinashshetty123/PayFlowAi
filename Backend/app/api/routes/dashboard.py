from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.dashboard import DashboardStats
from app.services.dashboard_service import dashboard_stats

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStats)
async def stats(db: AsyncSession = Depends(get_db)) -> dict:
    return await dashboard_stats(db)
