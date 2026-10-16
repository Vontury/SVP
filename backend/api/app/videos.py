import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from app.sessions import touch_session

from app.db import get_conn
from app.events import record_event
from app.participants import (
    current_condition_row,
    get_active_participant,
    get_current_participant,
)
from app.urls import UrlRejected, resolve_short_link, validate_url

router = APIRouter(prefix="/api")

_VIDEO_COLUMNS = """id, url, platform, title, description, hashtags,
    thumbnail_url, summary, category_id, processing_status, saved_at"""

_FIND_ACTIVE = f"""
    select {_VIDEO_COLUMNS}
    from public.videos
    where participant_id = %s::uuid and url_normalized = %s and deleted_at is null
"""

_INSERT_VIDEO = f"""
    insert into public.videos
        (participant_id, url, url_normalized, platform, save_source, device_type)
    values (%s::uuid, %s, %s, %s, %s, %s)
    returning {_VIDEO_COLUMNS}
"""

_LIST_VIDEOS = f"""
    select {_VIDEO_COLUMNS}
    from public.videos
    where participant_id = %s::uuid and deleted_at is null
    order by saved_at desc, id desc
    limit %s offset %s
"""

_GET_VIDEO = f"""
    select {_VIDEO_COLUMNS}
    from public.videos
    where id = %s::uuid and participant_id = %s::uuid and deleted_at is null
"""

_RETRY_VIDEO = f"""
    update public.videos
    set processing_status = 'PROCESSING',
        processing_error_code = null,
        retry_count = 0,
        next_retry_at = null
    where id = %s::uuid
      and participant_id = %s::uuid
      and deleted_at is null
      and processing_status = 'FAILED'
    returning {_VIDEO_COLUMNS}
"""

_LOCK_VIDEO = """
    select id from public.videos
    where id = %s::uuid and participant_id = %s::uuid and deleted_at is null
    for update
"""

_MARK_OPENED = """
    update public.videos
    set first_opened_at = coalesce(first_opened_at, now()),
        last_opened_at = now(),
        open_count = open_count + 1
    where id = %s::uuid
"""

_SOFT_DELETE = """
    update public.videos
    set deleted_at = now()
    where id = %s::uuid and participant_id = %s::uuid and deleted_at is null
    returning id
"""

_FULL_ONLY_FIELDS = {"category_id"}


class SaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(max_length=10000)
    save_source: Literal["PASTE", "SHARE_SHEET"] = "PASTE"
    device_type: Literal["ANDROID", "IOS", "DESKTOP", "OTHER"] | None = None
    idempotency_key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,64}$")


def _current_condition(conn, participant_id) -> str:
    row = current_condition_row(conn, participant_id)
    if row is None:
        raise HTTPException(status_code=409, detail="No active study condition")
    return row["condition"]


def _present(row: dict, condition: str) -> dict:
    if condition == "FULL":
        return dict(row)
    return {k: v for k, v in row.items() if k not in _FULL_ONLY_FIELDS}


@router.post("/videos")
def save_video(
    body: SaveRequest,
    response: Response,
    participant: dict = Depends(get_active_participant),
    conn=Depends(get_conn, scope="function"),
):
    condition = _current_condition(conn, participant["id"])

    try:
        checked = validate_url(body.url)
        if checked.needs_resolution:
            checked = resolve_short_link(checked.canonical_url)
    except UrlRejected as rejected:
        record_event(
            conn,
            participant_id=participant["id"],
            condition=condition,
            event_type="SAVE_REJECTED",
            idempotency_key=f"save_rejected:{body.idempotency_key or uuid.uuid4()}",
            source=body.save_source,
            device_type=body.device_type,
            payload={"reason": rejected.code},
        )
        return JSONResponse(status_code=422, content={"detail": {"code": rejected.code}})

    with conn.transaction():
        conn.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"{participant['id']}|{checked.canonical_url}",),
        )
        existing = conn.execute(
            _FIND_ACTIVE, (participant["id"], checked.canonical_url)
        ).fetchone()
        if existing is not None:
            return _present(existing, condition) 

        video = conn.execute(
            _INSERT_VIDEO,
            (
                participant["id"],
                checked.canonical_url,
                checked.canonical_url,
                checked.platform,
                body.save_source,
                body.device_type,
            ),
        ).fetchone()
        record_event(
            conn,
            participant_id=participant["id"],
            condition=condition,
            event_type="SAVED",
            idempotency_key=f"saved:{video['id']}",
            video_id=video["id"],
            source=body.save_source,
            device_type=body.device_type,
            payload={"platform": checked.platform},
        )

    response.status_code = 201
    return _present(video, condition)


@router.get("/videos")
def list_videos(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    participant: dict = Depends(get_current_participant),
    conn=Depends(get_conn, scope="function"),
):
    condition = _current_condition(conn, participant["id"])
    rows = conn.execute(_LIST_VIDEOS, (participant["id"], limit, offset)).fetchall()
    return {
        "items": [_present(r, condition) for r in rows],
        "limit": limit,
        "offset": offset,
    }


@router.get("/videos/{video_id}")
def get_video(
    video_id: uuid.UUID,
    participant: dict = Depends(get_current_participant),
    conn=Depends(get_conn, scope="function"),
):
    condition = _current_condition(conn, participant["id"])
    row = conn.execute(_GET_VIDEO, (video_id, participant["id"])).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _present(row, condition)

@router.post("/videos/{video_id}/retry")
def retry_video(
    video_id: uuid.UUID,
    response: Response,
    participant: dict = Depends(get_active_participant),
    conn=Depends(get_conn, scope="function"),
):
    condition = _current_condition(conn, participant["id"])

    retried = conn.execute(_RETRY_VIDEO, (video_id, participant["id"])).fetchone()
    if retried is not None:
        response.status_code = 202
        return _present(retried, condition)

    current = conn.execute(_GET_VIDEO, (video_id, participant["id"])).fetchone()
    if current is None:
        raise HTTPException(status_code=404, detail="Not found")
    if current["processing_status"] == "PROCESSING":
        return _present(current, condition)
    raise HTTPException(status_code=409, detail={"code": "NOT_RETRYABLE"})

class OpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    source_block: Literal[
        "CHRONOLOGICAL_LIST", "RECENT", "FORGOTTEN", "CATEGORY_TREE", "SEARCH", "SAVE_RESULT"
    ]
    device_type: Literal["ANDROID", "IOS", "DESKTOP", "OTHER"] | None = None
    external: bool = False


class DeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    device_type: Literal["ANDROID", "IOS", "DESKTOP", "OTHER"] | None = None


@router.post("/videos/{video_id}/open")
def open_video(
    video_id: uuid.UUID,
    body: OpenRequest,
    participant: dict = Depends(get_active_participant),
    conn=Depends(get_conn, scope="function"),
):
    condition = _current_condition(conn, participant["id"])
    event_type = "EXTERNAL_OPEN" if body.external else "OPENED"

    with conn.transaction():
        locked = conn.execute(_LOCK_VIDEO, (video_id, participant["id"])).fetchone()
        if locked is None:
            raise HTTPException(status_code=404, detail="Not found")
        session_id = touch_session(conn, participant["id"], condition)
        inserted = record_event(
            conn,
            participant_id=participant["id"],
            condition=condition,
            event_type=event_type,
            idempotency_key=body.idempotency_key,
            video_id=str(video_id),
            device_type=body.device_type,
            session_id=session_id,
            source_block=body.source_block,
        )
        if inserted and not body.external:
            conn.execute(_MARK_OPENED, (video_id,))

    return {"recorded": inserted, "event_type": event_type}


@router.delete("/videos/{video_id}")
def delete_video(
    video_id: uuid.UUID,
    body: DeleteRequest,
    participant: dict = Depends(get_active_participant),
    conn=Depends(get_conn, scope="function"),
):
    condition = _current_condition(conn, participant["id"])

    with conn.transaction():
        deleted = conn.execute(_SOFT_DELETE, (video_id, participant["id"])).fetchone()
        if deleted is None:
            raise HTTPException(status_code=404, detail="Not found")
        session_id = touch_session(conn, participant["id"], condition)
        record_event(
            conn,
            participant_id=participant["id"],
            condition=condition,
            event_type="DELETED",
            idempotency_key=body.idempotency_key,
            video_id=str(video_id),
            device_type=body.device_type,
            session_id=session_id,
        )

    return Response(status_code=204)