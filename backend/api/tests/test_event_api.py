import json
from datetime import datetime, timedelta, timezone

from tests.conftest import SAMPLE_PARTICIPANT

PID = SAMPLE_PARTICIPANT["id"]
VIDEO_ID = "33333333-3333-3333-3333-333333333333"
CATEGORY_ID = "44444444-4444-4444-4444-444444444444"
SESSION_ID = "99999999-9999-9999-9999-999999999999"
BASIC = {"condition": "BASIC", "valid_from": "2026-10-13T00:00:00Z"}
FULL = {"condition": "FULL", "valid_from": "2026-11-10T00:00:00Z"}
ACTIVE_SESSION = {"id": SESSION_ID, "active": True}
REASON = {"reason_context": "SAVE_REASON", "prompt_version": "p1", "codebook_version": "c1"}


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def sql_calls(fake_db, fragment):
    return [c for c in fake_db.calls if fragment in c[0]]


def ev(event_type="DASHBOARD_VIEWED", key="evt-00000001", **extra):
    return {"event_type": event_type, "idempotency_key": key, **extra}


def post(client, make_token, events):
    return client.post("/api/events/batch", json={"events": events}, headers=bearer(make_token()))


def test_batch_requires_token(client):
    assert client.post("/api/events/batch", json={"events": [ev()]}).status_code == 401


def test_batch_records_events_with_server_side_identity(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC, ACTIVE_SESSION, {"id": "e1"}, {"id": "e2"}]
    events = [
        ev("DASHBOARD_VIEWED", "evt-00000001", source_block="CHRONOLOGICAL_LIST", device_type="IOS"),
        ev("CATEGORY_SELECTED", "evt-00000002", payload={"category_id": CATEGORY_ID}),
    ]
    r = post(client, make_token, events)
    assert r.status_code == 200
    assert r.json() == {"accepted": 2, "duplicates": 0}

    inserts = sql_calls(fake_db, "insert into public.interaction_events")
    assert len(inserts) == 2
    first, second = inserts[0][1], inserts[1][1]
    assert first[0] == PID
    assert first[2] == "DASHBOARD_VIEWED"
    assert first[4] == "IOS"
    assert first[5] == "BASIC"
    assert first[6] == "evt-00000001"
    assert first[9] == SESSION_ID
    assert first[10] == "CHRONOLOGICAL_LIST"
    assert json.loads(second[7]) == {"category_id": CATEGORY_ID}
    assert len(sql_calls(fake_db, "set last_activity_at = now()")) == 1


def test_duplicate_events_are_counted_not_inserted_twice(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC, ACTIVE_SESSION, None]
    r = post(client, make_token, [ev()])
    assert r.status_code == 200
    assert r.json() == {"accepted": 0, "duplicates": 1}


def test_server_only_event_type_rejects_the_whole_batch(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC]
    r = post(client, make_token, [ev(), ev("OPENED", "evt-00000002")])
    assert r.status_code == 422
    assert r.json() == {"detail": {"code": "EVENT_TYPE_NOT_ALLOWED", "index": 1}}
    assert sql_calls(fake_db, "insert into public.interaction_events") == []
    assert sql_calls(fake_db, "public.app_sessions") == []


def test_reason_event_in_basic_is_rejected(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC]
    r = post(client, make_token, [ev("REASON_BUTTON_CLICKED", video_id=VIDEO_ID, payload=dict(REASON))])
    assert r.status_code == 422
    assert r.json() == {"detail": {"code": "NOT_ALLOWED_IN_CONDITION", "index": 0}}
    assert sql_calls(fake_db, "insert into public.interaction_events") == []


def test_reason_event_in_full_for_an_owned_item_is_recorded(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), FULL, ACTIVE_SESSION, {"id": "e1"}]
    fake_db.all_rows = [{"id": VIDEO_ID}]
    r = post(client, make_token, [ev("REASON_PROMPT_OPENED", video_id=VIDEO_ID, payload=dict(REASON))])
    assert r.status_code == 200
    assert r.json() == {"accepted": 1, "duplicates": 0}
    (insert,) = sql_calls(fake_db, "insert into public.interaction_events")
    assert insert[1][1] == VIDEO_ID
    assert insert[1][5] == "FULL"
    assert json.loads(insert[1][7]) == REASON
    (owner,) = sql_calls(fake_db, "id = any(%s::uuid[])")
    assert owner[1] == (PID, [VIDEO_ID])


def test_item_owned_by_someone_else_is_rejected(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), FULL]
    fake_db.all_rows = []
    r = post(client, make_token, [ev("REASON_BUTTON_CLICKED", video_id=VIDEO_ID, payload=dict(REASON))])
    assert r.status_code == 422
    assert r.json() == {"detail": {"code": "VIDEO_NOT_FOUND", "index": 0}}
    assert sql_calls(fake_db, "insert into public.interaction_events") == []


def test_client_cannot_supply_identity_or_condition(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT)]
    for extra in ({"participant_id": "someone-else"}, {"condition_at_event": "FULL"}, {"session_id": SESSION_ID}):
        r = post(client, make_token, [ev(**extra)])
        assert r.status_code == 422
    assert sql_calls(fake_db, "insert into public.interaction_events") == []


def test_batch_size_is_bounded(client, make_token):
    assert post(client, make_token, []).status_code == 422
    many = [ev(key=f"evt-{i:08d}") for i in range(51)]
    assert post(client, make_token, many).status_code == 422


def test_idempotency_key_is_validated(client, make_token):
    assert post(client, make_token, [ev(key="short")]).status_code == 422
    assert post(client, make_token, [ev(key="has space in it")]).status_code == 422


def test_unknown_source_block_is_rejected(client, make_token):
    assert post(client, make_token, [ev(source_block="SOMEWHERE")]).status_code == 422


def test_old_client_timestamp_falls_back_to_server_time(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC, ACTIVE_SESSION, {"id": "e1"}]
    r = post(client, make_token, [ev(occurred_at="2000-01-01T00:00:00Z")])
    assert r.status_code == 200
    (insert,) = sql_calls(fake_db, "insert into public.interaction_events")
    assert insert[1][8] is None


def test_recent_client_timestamp_is_kept(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC, ACTIVE_SESSION, {"id": "e1"}]
    recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    r = post(client, make_token, [ev(occurred_at=recent)])
    assert r.status_code == 200
    (insert,) = sql_calls(fake_db, "insert into public.interaction_events")
    assert insert[1][8] is not None


def test_withdrawn_participant_cannot_send_events(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT, status="WITHDRAWN")]
    assert post(client, make_token, [ev()]).status_code == 403
    assert sql_calls(fake_db, "insert into public.interaction_events") == []


def test_batch_without_active_condition_is_409(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), None]
    assert post(client, make_token, [ev()]).status_code == 409
    assert sql_calls(fake_db, "insert into public.interaction_events") == []


def test_embed_failed_in_basic_for_an_owned_item_is_recorded(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC, ACTIVE_SESSION, {"id": "e1"}]
    fake_db.all_rows = [{"id": VIDEO_ID}]
    r = post(client, make_token, [ev("EMBED_FAILED", video_id=VIDEO_ID)])
    assert r.status_code == 200
    assert r.json() == {"accepted": 1, "duplicates": 0}