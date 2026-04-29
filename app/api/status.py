from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ApiCallLog, Search
from app.schemas import StatusOut
from app.services.ebay import DAILY_API_CALL_LIMIT
from app.services.scheduler import get_scheduler

router = APIRouter(prefix="/api", tags=["status"])


def _start_of_utc_day() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime.combine(now.date(), time.min, tzinfo=timezone.utc)


@router.get("/status", response_model=StatusOut)
def status(db: Session = Depends(get_db)):
    api_calls_today = db.scalar(
        select(func.count(ApiCallLog.id)).where(ApiCallLog.called_at >= _start_of_utc_day())
    ) or 0
    total = db.scalar(select(func.count(Search.id))) or 0
    enabled = db.scalar(select(func.count(Search.id)).where(Search.enabled.is_(True))) or 0
    sched = get_scheduler()
    return StatusOut(
        status="ok",
        api_calls_today=int(api_calls_today),
        api_call_limit=DAILY_API_CALL_LIMIT,
        scheduler_running=sched.running,
        searches_count=int(total),
        enabled_searches_count=int(enabled),
    )
