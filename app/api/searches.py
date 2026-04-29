from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Search
from app.schemas import SearchCreate, SearchOut, SearchUpdate
from app.services.scheduler import (
    run_search_job,
    schedule_search,
    unschedule_search,
)

router = APIRouter(prefix="/api/searches", tags=["searches"])


@router.get("", response_model=list[SearchOut])
def list_searches(db: Session = Depends(get_db)):
    return db.scalars(select(Search).order_by(Search.created_at.desc())).all()


@router.post("", response_model=SearchOut, status_code=201)
def create_search(payload: SearchCreate, db: Session = Depends(get_db)):
    search = Search(
        name=payload.name,
        query=payload.query,
        filters=payload.filters.model_dump(),
        interval_minutes=payload.interval_minutes,
        enabled=payload.enabled,
    )
    db.add(search)
    db.commit()
    db.refresh(search)
    if search.enabled:
        schedule_search(search)
    return search


@router.get("/{search_id}", response_model=SearchOut)
def get_search(search_id: int, db: Session = Depends(get_db)):
    search = db.get(Search, search_id)
    if search is None:
        raise HTTPException(404, "Search not found")
    return search


@router.put("/{search_id}", response_model=SearchOut)
def update_search(search_id: int, payload: SearchUpdate, db: Session = Depends(get_db)):
    search = db.get(Search, search_id)
    if search is None:
        raise HTTPException(404, "Search not found")
    if payload.name is not None:
        search.name = payload.name
    if payload.query is not None:
        search.query = payload.query
    if payload.filters is not None:
        search.filters = payload.filters.model_dump()
    if payload.interval_minutes is not None:
        search.interval_minutes = payload.interval_minutes
    if payload.enabled is not None:
        search.enabled = payload.enabled
    db.commit()
    db.refresh(search)
    if search.enabled:
        schedule_search(search)
    else:
        unschedule_search(search.id)
    return search


@router.delete("/{search_id}", status_code=204)
def delete_search(search_id: int, db: Session = Depends(get_db)):
    search = db.get(Search, search_id)
    if search is None:
        raise HTTPException(404, "Search not found")
    unschedule_search(search.id)
    db.delete(search)
    db.commit()
    return None


@router.post("/{search_id}/run", status_code=202)
async def trigger_run(search_id: int, db: Session = Depends(get_db)):
    search = db.get(Search, search_id)
    if search is None:
        raise HTTPException(404, "Search not found")
    await run_search_job(search_id)
    return {"status": "ok"}


@router.post("/{search_id}/enable", response_model=SearchOut)
def enable_search(search_id: int, db: Session = Depends(get_db)):
    search = db.get(Search, search_id)
    if search is None:
        raise HTTPException(404, "Search not found")
    search.enabled = True
    db.commit()
    db.refresh(search)
    schedule_search(search)
    return search


@router.post("/{search_id}/disable", response_model=SearchOut)
def disable_search(search_id: int, db: Session = Depends(get_db)):
    search = db.get(Search, search_id)
    if search is None:
        raise HTTPException(404, "Search not found")
    search.enabled = False
    search.last_run_at = search.last_run_at  # no-op, keep field stable
    db.commit()
    db.refresh(search)
    unschedule_search(search.id)
    return search
