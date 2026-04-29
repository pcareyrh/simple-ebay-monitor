from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import PriceHistory, Result
from app.schemas import PriceHistoryOut, ResultOut

router = APIRouter(tags=["results"])


@router.get("/api/searches/{search_id}/results", response_model=list[ResultOut])
def list_results(search_id: int, include_discarded: bool = False, db: Session = Depends(get_db)):
    stmt = select(Result).where(Result.search_id == search_id)
    if not include_discarded:
        stmt = stmt.where(Result.discarded.is_(False))
    stmt = stmt.order_by(Result.first_seen_at.desc())
    return db.scalars(stmt).all()


@router.post("/api/results/{result_id}/discard", response_model=ResultOut)
def discard_result(result_id: int, db: Session = Depends(get_db)):
    result = db.get(Result, result_id)
    if result is None:
        raise HTTPException(404, "Result not found")
    result.discarded = True
    result.discarded_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(result)
    return result


@router.get("/api/results/{result_id}/price-history", response_model=list[PriceHistoryOut])
def price_history(result_id: int, db: Session = Depends(get_db)):
    if db.get(Result, result_id) is None:
        raise HTTPException(404, "Result not found")
    return db.scalars(
        select(PriceHistory)
        .where(PriceHistory.result_id == result_id)
        .order_by(PriceHistory.recorded_at)
    ).all()
