import logging
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import ApiCallLog, PriceHistory, Result, Search
from app.schemas import SearchFilters
from app.services.ebay import EbayAPIError, ebay_client, parse_item_summary

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


def _job_id(search_id: int) -> str:
    return f"search-{search_id}"


async def run_search_job(search_id: int) -> None:
    """Execute a single search: fetch results from eBay and upsert into the DB."""
    db: Session = SessionLocal()
    try:
        search = db.get(Search, search_id)
        if search is None:
            logger.warning("run_search_job: search %s not found", search_id)
            return

        logger.info("Running search '%s' (id=%s)", search.name, search.id)
        filters = SearchFilters.model_validate(search.filters or {})

        try:
            payload = await ebay_client.search(search.query, filters)
        except EbayAPIError as e:
            logger.error("Search %s failed: %s", search.id, e)
            search.last_error = str(e)[:2000]
            search.last_run_at = datetime.now(timezone.utc)
            db.commit()
            return

        db.add(ApiCallLog(endpoint="search", called_at=datetime.now(timezone.utc)))

        items = payload.get("itemSummaries") or []
        now = datetime.now(timezone.utc)
        new_count = 0
        updated_count = 0

        existing = {
            r.ebay_item_id: r
            for r in db.scalars(select(Result).where(Result.search_id == search.id)).all()
        }

        for raw in items:
            parsed = parse_item_summary(raw)
            item_id = parsed.get("ebay_item_id")
            if not item_id:
                continue

            existing_result = existing.get(item_id)
            if existing_result is None:
                result = Result(
                    search_id=search.id,
                    first_seen_at=now,
                    last_seen_at=now,
                    **parsed,
                )
                db.add(result)
                db.flush()
                if result.price is not None:
                    db.add(PriceHistory(
                        result_id=result.id,
                        price=result.price,
                        currency=result.currency,
                        recorded_at=now,
                    ))
                new_count += 1
            else:
                if existing_result.discarded:
                    continue
                existing_result.last_seen_at = now
                new_price = parsed.get("price")
                if new_price is not None and new_price != existing_result.price:
                    db.add(PriceHistory(
                        result_id=existing_result.id,
                        price=new_price,
                        currency=parsed.get("currency"),
                        recorded_at=now,
                    ))
                    existing_result.price = new_price
                existing_result.current_bid = parsed.get("current_bid")
                existing_result.bid_count = parsed.get("bid_count")
                existing_result.end_date = parsed.get("end_date")
                existing_result.shipping_cost = parsed.get("shipping_cost")
                updated_count += 1

        search.last_run_at = now
        search.last_error = None
        db.commit()
        logger.info(
            "Search %s complete: %s new, %s updated, %s total returned",
            search.id, new_count, updated_count, len(items),
        )
    except Exception as e:
        logger.exception("run_search_job error: %s", e)
        db.rollback()
        try:
            search = db.get(Search, search_id)
            if search is not None:
                search.last_error = str(e)[:2000]
                search.last_run_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()


def schedule_search(search: Search) -> None:
    """Add or replace a scheduled job for a search."""
    scheduler = get_scheduler()
    job_id = _job_id(search.id)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    if not search.enabled:
        return
    scheduler.add_job(
        run_search_job,
        trigger=IntervalTrigger(minutes=max(search.interval_minutes, 1)),
        id=job_id,
        args=[search.id],
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),
    )


def unschedule_search(search_id: int) -> None:
    scheduler = get_scheduler()
    job_id = _job_id(search_id)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


def load_all_searches() -> None:
    db: Session = SessionLocal()
    try:
        searches = db.scalars(select(Search).where(Search.enabled.is_(True))).all()
        for s in searches:
            schedule_search(s)
    finally:
        db.close()


def next_run_for(search_id: int) -> Optional[datetime]:
    scheduler = get_scheduler()
    job = scheduler.get_job(_job_id(search_id))
    return job.next_run_time if job else None
