import secrets

from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, ConfigDict, Field

from app.auth import CurrentUser, get_current_user
from app.config import Settings, get_settings
from app.db import get_conn
from app.participants import PARTICIPANT_SQL, current_condition_row, get_current_participant
router = APIRouter(prefix="/api")

_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

_INSERT_PARTICIPANT = """
    insert into public.participants (auth_user_id, study_code)
    values (%s::uuid, %s)
    on conflict (auth_user_id) do nothing
    returning id, study_code, status, pilot_flag,
              primary_device, region_code, created_at
"""

_INSERT_CONSENT = """
    insert into public.consent_records (participant_id, consent_version)
    values (%s::uuid, %s)
"""

_INSERT_CONDITION = """
    insert into public.study_condition_history
        (participant_id, condition, phase_id, valid_from)
    values (%s::uuid, 'BASIC', %s, now())
"""


def _new_study_code() -> str:
    return "SVP-" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))


class ConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent_version: str = Field(min_length=1, max_length=64)


@router.post("/consent")
def accept_consent(
    body: ConsentRequest,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_conn),
    settings: Settings = Depends(get_settings),
):
    if body.consent_version != settings.consent_version:
        raise HTTPException(status_code=409, detail="Consent version is out of date")

    with conn.transaction():
        participant = None
        for _ in range(5):
            try:
                with conn.transaction():
                    participant = conn.execute(
                        _INSERT_PARTICIPANT, (user.id, _new_study_code())
                    ).fetchone()
            except UniqueViolation: 
                continue
            break
        else:
            raise HTTPException(status_code=503, detail="Could not create participant")

        if participant is None:
            existing = conn.execute(PARTICIPANT_SQL, (user.id,)).fetchone()
            if existing is None:
                raise HTTPException(status_code=409, detail="Please try again")
            return existing

        conn.execute(_INSERT_CONSENT, (participant["id"], body.consent_version))
        conn.execute(_INSERT_CONDITION, (participant["id"], settings.initial_phase_id))

    response.status_code = 201
    return participant


@router.get("/me/condition")
def my_condition(
    participant: dict = Depends(get_current_participant),
    conn=Depends(get_conn),
):
    row = current_condition_row(conn, participant["id"])    
    if row is None:
        raise HTTPException(status_code=409, detail="No active study condition")
    return {"condition": row["condition"], "since": row["valid_from"]}