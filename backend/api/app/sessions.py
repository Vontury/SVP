SESSION_TIMEOUT_MINUTES = 30

_LOCK = "select pg_advisory_xact_lock(hashtextextended(%s, 0))"

_LATEST = """
    select id,
           last_activity_at > now() - make_interval(mins => %s) as active
    from public.app_sessions
    where participant_id = %s::uuid and ended_at is null
    order by started_at desc
    limit 1
"""

_TOUCH = """
    update public.app_sessions
    set last_activity_at = now()
    where id = %s::uuid
"""

_CLOSE = """
    update public.app_sessions
    set ended_at = last_activity_at, end_reason = 'TIMEOUT'
    where id = %s::uuid and ended_at is null
"""

_OPEN = """
    insert into public.app_sessions (participant_id, condition_at_start)
    values (%s::uuid, %s)
    returning id
"""


def touch_session(conn, participant_id, condition):
    with conn.transaction():
        conn.execute(_LOCK, (f"session|{participant_id}",))
        latest = conn.execute(
            _LATEST, (SESSION_TIMEOUT_MINUTES, participant_id)
        ).fetchone()
        if latest is not None:
            if latest["active"]:
                conn.execute(_TOUCH, (latest["id"],))
                return latest["id"]
            conn.execute(_CLOSE, (latest["id"],))
        opened = conn.execute(_OPEN, (participant_id, condition)).fetchone()
        return opened["id"]