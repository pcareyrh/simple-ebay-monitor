# eBay Monitor

Self-hosted web app that runs scheduled searches against the eBay Browse API, tracks new listings, and lets you discard items you don't care about so each run surfaces only fresh results.

## Features

- Per-search schedules (APScheduler, runs inside the FastAPI process)
- Dashboard with saved searches, run counts, pause/resume, run-now
- Results page with thumbnails, condition, Buy Now / Auction tag, time-left for auctions, seller info
- Discard results inline (HTMX) — discarded items never reappear
- Price history tracking — every price change gets a row
- Live preview of a search before saving
- API call counter (daily) against the 5,000/day Browse API quota
- SQLite persistence on a mounted volume
- JSON REST API alongside the HTML UI

## Quick start (Docker)

```bash
cp .env.example .env
# edit .env with your eBay API credentials
docker compose up -d
```

Then open http://localhost:8000.

### Getting eBay API credentials

1. Sign up at https://developer.ebay.com/
2. Create a "keyset" for the Production environment
3. Copy the **App ID (Client ID)** and **Cert ID (Client Secret)** into `.env`

The app uses the OAuth client credentials grant — no user login required.

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
DATABASE_URL="sqlite:///./data/ebay_monitor.db" \
EBAY_CLIENT_ID=... EBAY_CLIENT_SECRET=... \
uvicorn app.main:app --reload
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `EBAY_CLIENT_ID` | — | eBay App ID |
| `EBAY_CLIENT_SECRET` | — | eBay Cert ID |
| `EBAY_SANDBOX` | `false` | Use `api.sandbox.ebay.com` when true |
| `DATABASE_URL` | `sqlite:////data/ebay_monitor.db` | SQLAlchemy URL |

## Project layout

```
app/
  main.py              FastAPI app + lifespan (scheduler start/shutdown)
  config.py            Settings (pydantic-settings, reads .env)
  database.py          SQLAlchemy engine + session
  models.py            ORM: Search, Result, PriceHistory, ApiCallLog
  schemas.py           Pydantic request/response models
  api/
    searches.py        REST: /api/searches CRUD + run/enable/disable
    results.py         REST: results list + discard + price history
    status.py          REST: /api/status (calls today, scheduler status)
    web.py             HTML routes (dashboard, forms, HTMX discard)
  services/
    ebay.py            OAuth + Browse API client
    scheduler.py       APScheduler integration, search job runner
  templates/           Jinja2 templates (Pico CSS + HTMX)
data/                  SQLite DB lives here (mounted volume in Docker)
```

## REST API

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/searches` | list all |
| `POST` | `/api/searches` | create |
| `GET` | `/api/searches/{id}` | show |
| `PUT` | `/api/searches/{id}` | update |
| `DELETE` | `/api/searches/{id}` | delete (cascades to results) |
| `POST` | `/api/searches/{id}/run` | trigger immediate run |
| `POST` | `/api/searches/{id}/enable` / `/disable` | toggle schedule |
| `GET` | `/api/searches/{id}/results?include_discarded=false` | list results |
| `POST` | `/api/results/{id}/discard` | discard a result |
| `GET` | `/api/results/{id}/price-history` | price points over time |
| `GET` | `/api/status` | health + call counter |

Interactive docs at `/docs`.

## Filters

All filters are optional:

- Price range (`price_min`, `price_max`, `price_currency`)
- Condition (`NEW`, `USED`, `CERTIFIED_REFURBISHED`, etc.)
- Listing type (`FIXED_PRICE`, `AUCTION`, or both — default is both)
- Free shipping only
- Ships from / ships to country (ISO 2-letter codes)
- eBay category IDs

## Rate limits

The Browse API allows 5,000 calls/day by default. Each scheduled run uses one call (limit=200 results). The dashboard shows today's usage in the top-right corner.

## Data persistence

All state lives in `/data/ebay_monitor.db` inside the container, mounted from `./data` on the host. Stop/restart the container freely — discards, price history, and search config survive.
