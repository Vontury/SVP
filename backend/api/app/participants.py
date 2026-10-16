from fastapi import Depends, HTTPException, status

from app.auth import CurrentUser, get_current_user
from app.db import get_conn

PARTICIPANT_SQL = """
    select id, study_code, status, pilot_flag,
           primary_device, region_code, created_at
    from public.participants
    where auth_user_id = %s::uuid
"""

_CURRENT_CONDITION = """
    select condition, valid_from
    from public.study_condition_history
    where participant_id = %s::uuid
      and valid_from <= now()
      and (valid_to is null or valid_to > now())
"""


def get_current_participant(
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_conn, scope="function"),
) -> dict:
    row = conn.execute(PARTICIPANT_SQL, (user.id,)).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No participant profile for this account",
        )
    return row

def get_active_participant(participant: dict = Depends(get_current_participant)) -> dict:
    if participant["status"] != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Participant is not active",
        )
    return participant


def current_condition_row(conn, participant_id) -> dict | None:
    return conn.execute(_CURRENT_CONDITION, (participant_id,)).fetchone()