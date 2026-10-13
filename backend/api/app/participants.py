from fastapi import Depends, HTTPException, status

from app.auth import CurrentUser, get_current_user
from app.db import get_conn

_PARTICIPANT_SQL = """
    select id, study_code, status, pilot_flag,
           primary_device, region_code, created_at
    from public.participants
    where auth_user_id = %s::uuid
"""


def get_current_participant(
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_conn),
) -> dict:
    row = conn.execute(_PARTICIPANT_SQL, (user.id,)).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No participant profile for this account",
        )
    return row