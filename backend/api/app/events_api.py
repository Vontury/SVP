from datetime import datetime, timezone
from typing import Literal

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.db import get_conn
from app.event_rules import EventRejected, resolve_occurred_at, validate_event
from app.events import record_event
from app.participants import current_condition_row, get_active_participant
from app.sessions import touch_session

router = APIRouter(prefix="/api")

_OWNED_VIDEOS = """
    select id from public.videos
    where participant_id = %s::uuid and id = any(%s::uuid[])
"""


class ClientEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_type: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    occurred_at: datetime | None = None
    video_id: uuid.UUID | None = None
    source_block: Literal[
        "CHRONOLOGICAL_LIST", "RECENT", "FORGOTTEN", "CATEGORY_TREE", "SEARCH", "SAVE_RESULT"
    ] | None = None
    device_type: Literal["ANDROID", "IOS", "DESKTOP", "OTHER"] | None = None
    payload: dict = Field(default_factory=dict)


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[ClientEvent] = Field(min_length=1, max_length=50)


def _reject(code, index):
    return HTTPException(status_code=422, detail={"code": code, "index": index})


@router.post("/events/batch")
def record_batch(
    body: BatchRequest,
    participant: dict = Depends(get_active_participant),
    conn=Depends(get_conn, scope="function"),
):
    row = current_condition_row(conn, participant["id"])
    if row is None:
        raise HTTPException(status_code=409, detail="No active study condition")
    condition = row["condition"]

    cleaned = []
    for index, event in enumerate(body.events):
        try:
            cleaned.append(
                validate_event(event.event_type, event.video_id, event.payload, condition)
            )
        except EventRejected as rejected:
            raise _reject(rejected.code, index)

    video_ids = sorted({str(e.video_id) for e in body.events if e.video_id is not None})
    if video_ids:
        found = conn.execute(_OWNED_VIDEOS, (participant["id"], video_ids)).fetchall()
        owned = {str(r["id"]) for r in found}
        for index, event in enumerate(body.events):
            if event.video_id is not None and str(event.video_id) not in owned:
                raise _reject("VIDEO_NOT_FOUND", index)

    now = datetime.now(timezone.utc)
    accepted = 0
    with conn.transaction():
        session_id = touch_session(conn, participant["id"], condition)
        for event, payload in zip(body.events, cleaned):
            inserted = record_event(
                conn,
                participant_id=participant["id"],
                condition=condition,
                event_type=event.event_type,
                idempotency_key=event.idempotency_key,
                video_id=str(event.video_id) if event.video_id is not None else None,
                device_type=event.device_type,
                payload=payload,
                occurred_at=resolve_occurred_at(event.occurred_at, now),
                session_id=session_id,
                source_block=event.source_block,
            )
            if inserted:
                accepted += 1

    return {"accepted": accepted, "duplicates": len(body.events) - accepted}