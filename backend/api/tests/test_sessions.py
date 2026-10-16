from app import sessions
from tests.conftest import SAMPLE_PARTICIPANT

PID = SAMPLE_PARTICIPANT["id"]
OLD_ID = "77777777-7777-7777-7777-777777777777"
NEW_ID = "88888888-8888-8888-8888-888888888888"


def sql_calls(fake_db, fragment):
    return [c for c in fake_db.calls if fragment in c[0]]


def test_active_session_is_extended(fake_db):
    fake_db.queue = [{"id": OLD_ID, "active": True}]
    assert sessions.touch_session(fake_db, PID, "BASIC") == OLD_ID
    (touch,) = sql_calls(fake_db, "set last_activity_at = now()")
    assert touch[1] == (OLD_ID,)
    assert sql_calls(fake_db, "insert into public.app_sessions") == []
    assert sql_calls(fake_db, "end_reason") == []


def test_expired_session_is_closed_and_a_new_one_opened(fake_db):
    fake_db.queue = [{"id": OLD_ID, "active": False}, {"id": NEW_ID}]
    assert sessions.touch_session(fake_db, PID, "FULL") == NEW_ID
    (close,) = sql_calls(fake_db, "end_reason = 'TIMEOUT'")
    assert close[1] == (OLD_ID,)
    (opened,) = sql_calls(fake_db, "insert into public.app_sessions")
    assert opened[1] == (PID, "FULL")
    assert sql_calls(fake_db, "set last_activity_at = now()") == []


def test_first_ever_session_is_opened(fake_db):
    fake_db.queue = [None, {"id": NEW_ID}]
    assert sessions.touch_session(fake_db, PID, "BASIC") == NEW_ID
    assert sql_calls(fake_db, "end_reason") == []
    (opened,) = sql_calls(fake_db, "insert into public.app_sessions")
    assert opened[1] == (PID, "BASIC")


def test_lock_is_taken_before_anything_else(fake_db):
    fake_db.queue = [None, {"id": NEW_ID}]
    sessions.touch_session(fake_db, PID, "BASIC")
    assert "pg_advisory_xact_lock" in fake_db.calls[0][0]
    assert fake_db.calls[0][1] == (f"session|{PID}",)


def test_timeout_is_thirty_minutes(fake_db):
    fake_db.queue = [None, {"id": NEW_ID}]
    sessions.touch_session(fake_db, PID, "BASIC")
    (latest,) = sql_calls(fake_db, "order by started_at desc")
    assert latest[1] == (30, PID)