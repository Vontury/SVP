import asyncio
from contextlib import asynccontextmanager, suppress
from fastapi import Depends, FastAPI

from app.config import get_settings
from app.db import close_pool, get_conn, init_pool
from app.participants import get_current_participant
from app.study import router as study_router
from app.videos import router as videos_router
from app.worker import run_forever
from app.events_api import router as events_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_pool(get_settings().database_url)
    worker_task = asyncio.create_task(run_forever())
    yield
    worker_task.cancel()
    with suppress(asyncio.CancelledError):
        await worker_task
    close_pool()


app = FastAPI(title="SVP API", lifespan=lifespan)
app.include_router(study_router)
app.include_router(videos_router)
app.include_router(events_router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/health/db")
def health_db(conn=Depends(get_conn)):
    conn.execute("select 1")
    return {"db": "ok"}

@app.get("/api/me")
def me(participant: dict = Depends(get_current_participant)):
    return participant