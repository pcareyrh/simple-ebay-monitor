from datetime import datetime, time, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ApiCallLog, PriceHistory, Result, Search
from app.schemas import SearchFilters
from app.services.ebay import DAILY_API_CALL_LIMIT, EbayAPIError, ebay_client, parse_item_summary
from app.services.scheduler import (
    next_run_for,
    run_search_job,
    schedule_search,
    unschedule_search,
)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _format_dt(value: Optional[datetime]) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _time_left(end: Optional[datetime]) -> str:
    if end is None:
        return ""
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    delta = end - datetime.now(timezone.utc)
    secs = int(delta.total_seconds())
    if secs <= 0:
        return "ended"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


templates.env.filters["fmtdt"] = _format_dt
templates.env.filters["timeleft"] = _time_left

router = APIRouter(tags=["web"])


def _api_calls_today(db: Session) -> int:
    start = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)
    return int(db.scalar(
        select(func.count(ApiCallLog.id)).where(ApiCallLog.called_at >= start)
    ) or 0)


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    searches = db.scalars(select(Search).order_by(Search.created_at.desc())).all()

    rows = []
    for s in searches:
        new_count = db.scalar(
            select(func.count(Result.id)).where(
                Result.search_id == s.id,
                Result.discarded.is_(False),
            )
        ) or 0
        rows.append({
            "search": s,
            "new_count": int(new_count),
            "next_run": next_run_for(s.id),
        })

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "rows": rows,
            "api_calls_today": _api_calls_today(db),
            "api_call_limit": DAILY_API_CALL_LIMIT,
        },
    )


@router.get("/searches/new", response_class=HTMLResponse)
def new_search_form(request: Request):
    return templates.TemplateResponse(
        request,
        "search_form.html",
        {"search": None, "form": _empty_form(), "errors": {}},
    )


@router.get("/searches/{search_id}/edit", response_class=HTMLResponse)
def edit_search_form(search_id: int, request: Request, db: Session = Depends(get_db)):
    search = db.get(Search, search_id)
    if search is None:
        raise HTTPException(404, "Search not found")
    filters = SearchFilters.model_validate(search.filters or {})
    form = {
        "name": search.name,
        "query": search.query,
        "interval_minutes": search.interval_minutes,
        "enabled": search.enabled,
        "price_min": filters.price_min,
        "price_max": filters.price_max,
        "price_currency": filters.price_currency,
        "conditions": filters.conditions,
        "buying_options": filters.buying_options,
        "free_shipping": filters.free_shipping,
        "item_location_country": filters.item_location_country or "",
        "delivery_country": filters.delivery_country or "",
        "category_ids": ",".join(filters.category_ids),
    }
    return templates.TemplateResponse(
        request,
        "search_form.html",
        {"search": search, "form": form, "errors": {}},
    )


def _empty_form() -> dict:
    return {
        "name": "",
        "query": "",
        "interval_minutes": 60,
        "enabled": True,
        "price_min": None,
        "price_max": None,
        "price_currency": "USD",
        "conditions": [],
        "buying_options": [],
        "free_shipping": False,
        "item_location_country": "",
        "delivery_country": "",
        "category_ids": "",
    }


def _parse_float(value: Optional[str]) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _build_filters_from_form(
    price_min: Optional[str],
    price_max: Optional[str],
    price_currency: str,
    conditions: list[str],
    buying_options: list[str],
    free_shipping: bool,
    item_location_country: str,
    delivery_country: str,
    category_ids: str,
) -> SearchFilters:
    return SearchFilters(
        price_min=_parse_float(price_min),
        price_max=_parse_float(price_max),
        price_currency=(price_currency or "USD").upper(),
        conditions=[c for c in conditions if c],
        buying_options=[b for b in buying_options if b],
        free_shipping=free_shipping,
        item_location_country=(item_location_country or "").strip().upper() or None,
        delivery_country=(delivery_country or "").strip().upper() or None,
        category_ids=[c.strip() for c in category_ids.split(",") if c.strip()],
    )


@router.post("/searches")
async def create_search_web(
    request: Request,
    name: str = Form(...),
    query: str = Form(...),
    interval_minutes: int = Form(60),
    enabled: Optional[str] = Form(None),
    price_min: Optional[str] = Form(None),
    price_max: Optional[str] = Form(None),
    price_currency: str = Form("USD"),
    conditions: list[str] = Form(default=[]),
    buying_options: list[str] = Form(default=[]),
    free_shipping: Optional[str] = Form(None),
    item_location_country: str = Form(""),
    delivery_country: str = Form(""),
    category_ids: str = Form(""),
    db: Session = Depends(get_db),
):
    filters = _build_filters_from_form(
        price_min, price_max, price_currency, conditions, buying_options,
        bool(free_shipping), item_location_country, delivery_country, category_ids,
    )
    search = Search(
        name=name.strip(),
        query=query.strip(),
        filters=filters.model_dump(),
        interval_minutes=max(1, int(interval_minutes)),
        enabled=bool(enabled),
    )
    db.add(search)
    db.commit()
    db.refresh(search)
    if search.enabled:
        schedule_search(search)
    return RedirectResponse(url=f"/searches/{search.id}", status_code=303)


@router.post("/searches/{search_id}/edit")
async def update_search_web(
    search_id: int,
    request: Request,
    name: str = Form(...),
    query: str = Form(...),
    interval_minutes: int = Form(60),
    enabled: Optional[str] = Form(None),
    price_min: Optional[str] = Form(None),
    price_max: Optional[str] = Form(None),
    price_currency: str = Form("USD"),
    conditions: list[str] = Form(default=[]),
    buying_options: list[str] = Form(default=[]),
    free_shipping: Optional[str] = Form(None),
    item_location_country: str = Form(""),
    delivery_country: str = Form(""),
    category_ids: str = Form(""),
    db: Session = Depends(get_db),
):
    search = db.get(Search, search_id)
    if search is None:
        raise HTTPException(404, "Search not found")
    filters = _build_filters_from_form(
        price_min, price_max, price_currency, conditions, buying_options,
        bool(free_shipping), item_location_country, delivery_country, category_ids,
    )
    search.name = name.strip()
    search.query = query.strip()
    search.filters = filters.model_dump()
    search.interval_minutes = max(1, int(interval_minutes))
    search.enabled = bool(enabled)
    db.commit()
    db.refresh(search)
    if search.enabled:
        schedule_search(search)
    else:
        unschedule_search(search.id)
    return RedirectResponse(url=f"/searches/{search.id}", status_code=303)


@router.get("/searches/{search_id}", response_class=HTMLResponse)
def search_detail(search_id: int, request: Request, db: Session = Depends(get_db)):
    search = db.get(Search, search_id)
    if search is None:
        raise HTTPException(404, "Search not found")
    results = db.scalars(
        select(Result)
        .where(Result.search_id == search.id, Result.discarded.is_(False))
        .order_by(Result.first_seen_at.desc())
    ).all()

    enriched = []
    for r in results:
        first_price: Optional[float] = None
        history = db.scalars(
            select(PriceHistory)
            .where(PriceHistory.result_id == r.id)
            .order_by(PriceHistory.recorded_at)
        ).all()
        if history:
            first_price = history[0].price
        enriched.append({"r": r, "first_price": first_price})

    return templates.TemplateResponse(
        request,
        "search_results.html",
        {
            "search": search,
            "results": enriched,
            "next_run": next_run_for(search.id),
        },
    )


@router.post("/searches/{search_id}/delete")
def delete_search_web(search_id: int, db: Session = Depends(get_db)):
    search = db.get(Search, search_id)
    if search is None:
        raise HTTPException(404, "Search not found")
    unschedule_search(search.id)
    db.delete(search)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/searches/{search_id}/run")
async def run_search_web(search_id: int, db: Session = Depends(get_db)):
    search = db.get(Search, search_id)
    if search is None:
        raise HTTPException(404, "Search not found")
    await run_search_job(search.id)
    return RedirectResponse(url=f"/searches/{search.id}", status_code=303)


@router.post("/searches/{search_id}/toggle")
def toggle_search_web(search_id: int, db: Session = Depends(get_db)):
    search = db.get(Search, search_id)
    if search is None:
        raise HTTPException(404, "Search not found")
    search.enabled = not search.enabled
    db.commit()
    db.refresh(search)
    if search.enabled:
        schedule_search(search)
    else:
        unschedule_search(search.id)
    return RedirectResponse(url="/", status_code=303)


@router.post("/results/{result_id}/discard", response_class=HTMLResponse)
def discard_result_web(result_id: int, db: Session = Depends(get_db)):
    """HTMX endpoint — returns empty response, HTMX removes the row."""
    result = db.get(Result, result_id)
    if result is None:
        raise HTTPException(404, "Result not found")
    result.discarded = True
    result.discarded_at = datetime.now(timezone.utc)
    db.commit()
    return Response(status_code=200, content="")


@router.get("/results/{result_id}/history", response_class=HTMLResponse)
def result_history(result_id: int, request: Request, db: Session = Depends(get_db)):
    result = db.get(Result, result_id)
    if result is None:
        raise HTTPException(404, "Result not found")
    history = db.scalars(
        select(PriceHistory)
        .where(PriceHistory.result_id == result_id)
        .order_by(PriceHistory.recorded_at)
    ).all()
    return templates.TemplateResponse(
        request,
        "price_history.html",
        {"result": result, "history": history},
    )


@router.post("/searches/preview", response_class=HTMLResponse)
async def preview_search(
    request: Request,
    query: str = Form(...),
    price_min: Optional[str] = Form(None),
    price_max: Optional[str] = Form(None),
    price_currency: str = Form("USD"),
    conditions: list[str] = Form(default=[]),
    buying_options: list[str] = Form(default=[]),
    free_shipping: Optional[str] = Form(None),
    item_location_country: str = Form(""),
    delivery_country: str = Form(""),
    category_ids: str = Form(""),
):
    filters = _build_filters_from_form(
        price_min, price_max, price_currency, conditions, buying_options,
        bool(free_shipping), item_location_country, delivery_country, category_ids,
    )
    try:
        payload = await ebay_client.search(query.strip(), filters, limit=10)
    except EbayAPIError as e:
        return templates.TemplateResponse(
            request, "preview.html", {"error": str(e), "items": []}
        )
    items = [parse_item_summary(it) for it in (payload.get("itemSummaries") or [])]
    return templates.TemplateResponse(
        request, "preview.html", {"error": None, "items": items[:10]}
    )
