import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import results as results_api
from app.api import searches as searches_api
from app.api import status as status_api
from app.api import web
from app.database import init_db
from app.services.scheduler import get_scheduler, load_all_searches

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler = get_scheduler()
    scheduler.start()
    load_all_searches()
    logger.info("Scheduler started with %s jobs", len(scheduler.get_jobs()))
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="eBay Monitor", lifespan=lifespan)

app.include_router(searches_api.router)
app.include_router(results_api.router)
app.include_router(status_api.router)
app.include_router(web.router)
