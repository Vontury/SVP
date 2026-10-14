from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.config import get_settings
from app.db import close_pool, get_conn, init_pool
from app.participants import get_current_participant
from app.study import router as study_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_pool(get_settings().database_url)
    yield
    close_pool()


app = FastAPI(title="SVP API", lifespan=lifespan)
app.include_router(study_router)


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