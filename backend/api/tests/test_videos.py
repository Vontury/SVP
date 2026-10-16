import json

import app.videos as videos_module
from app.urls import TIKTOK, UrlRejected, ValidatedUrl
from tests.conftest import SAMPLE_PARTICIPANT

PID = SAMPLE_PARTICIPANT["id"]
YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
VIDEO_ID = "33333333-3333-3333-3333-333333333333"
CATEGORY_ID = "44444444-4444-4444-4444-444444444444"
BASIC = {"condition": "BASIC", "valid_from": "2026-10-13T00:00:00Z"}
FULL = {"condition": "FULL", "valid_from": "2026-11-10T00:00:00Z"}
OPEN_BODY = {"idempotency_key": "open-00000001", "source_block": "CHRONOLOGICAL_LIST"}
SESSION = {"id": "99999999-9999-9999-9999-999999999999", "active": True}


def test_open_requires_token(client):
    assert client.post(f"/api/videos/{VIDEO_ID}/open", json=OPEN_BODY).status_code == 401


def test_open_records_event_and_updates_counters(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC, {"id": VIDEO_ID}, SESSION, {"id": "e1"}]
    r = client.post(f"/api/videos/{VIDEO_ID}/open", json=OPEN_BODY, headers=bearer(make_token()))
    assert r.status_code == 200
    assert r.json() == {"recorded": True, "event_type": "OPENED"}
    (event,) = sql_calls(fake_db, "insert into public.interaction_events")
    assert event[1][2] == "OPENED"
    assert event[1][6] == "open-00000001"
    assert event[1][10] == "CHRONOLOGICAL_LIST"
    assert len(sql_calls(fake_db, "open_count = open_count + 1")) == 1


def test_duplicate_open_does_not_touch_counters(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC, {"id": VIDEO_ID}, SESSION, None]
    r = client.post(f"/api/videos/{VIDEO_ID}/open", json=OPEN_BODY, headers=bearer(make_token()))
    assert r.status_code == 200
    assert r.json()["recorded"] is False
    assert sql_calls(fake_db, "open_count = open_count + 1") == []


def test_external_open_logs_separately_and_skips_counters(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC, {"id": VIDEO_ID}, SESSION, {"id": "e1"}]
    r = client.post(
        f"/api/videos/{VIDEO_ID}/open",
        json={**OPEN_BODY, "external": True},
        headers=bearer(make_token()),
    )
    assert r.status_code == 200
    assert r.json() == {"recorded": True, "event_type": "EXTERNAL_OPEN"}
    (event,) = sql_calls(fake_db, "insert into public.interaction_events")
    assert event[1][2] == "EXTERNAL_OPEN"
    assert sql_calls(fake_db, "open_count = open_count + 1") == []


def test_open_foreign_or_missing_item_is_404(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC, None]
    r = client.post(f"/api/videos/{VIDEO_ID}/open", json=OPEN_BODY, headers=bearer(make_token()))
    assert r.status_code == 404
    assert sql_calls(fake_db, "insert into public.interaction_events") == []


def test_open_rejects_bad_body(client, make_token):
    h = bearer(make_token())
    assert client.post(f"/api/videos/{VIDEO_ID}/open", json={"idempotency_key": "short", "source_block": "SEARCH"}, headers=h).status_code == 422
    assert client.post(f"/api/videos/{VIDEO_ID}/open", json={**OPEN_BODY, "source_block": "NOPE"}, headers=h).status_code == 422
    assert client.post(f"/api/videos/{VIDEO_ID}/open", json={**OPEN_BODY, "participant_id": "x"}, headers=h).status_code == 422


def test_open_by_withdrawn_participant_is_403(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT, status="WITHDRAWN")]
    r = client.post(f"/api/videos/{VIDEO_ID}/open", json=OPEN_BODY, headers=bearer(make_token()))
    assert r.status_code == 403


DELETE_BODY = {"idempotency_key": "del-00000001"}


def test_delete_requires_token(client):
    assert client.request("DELETE", f"/api/videos/{VIDEO_ID}", json=DELETE_BODY).status_code == 401


def test_delete_soft_deletes_and_logs_event(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC, {"id": VIDEO_ID}, SESSION, {"id": "e1"}]
    r = client.request("DELETE", f"/api/videos/{VIDEO_ID}", json=DELETE_BODY, headers=bearer(make_token()))
    assert r.status_code == 204
    (soft,) = sql_calls(fake_db, "set deleted_at = now()")
    assert (str(soft[1][0]), soft[1][1]) == (VIDEO_ID, PID)
    (event,) = sql_calls(fake_db, "insert into public.interaction_events")
    assert event[1][2] == "DELETED"
    assert event[1][6] == "del-00000001"


def test_delete_missing_or_already_deleted_is_404_without_event(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC, None]
    r = client.request("DELETE", f"/api/videos/{VIDEO_ID}", json=DELETE_BODY, headers=bearer(make_token()))
    assert r.status_code == 404
    assert sql_calls(fake_db, "insert into public.interaction_events") == []


def test_delete_by_withdrawn_participant_is_403(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT, status="WITHDRAWN")]
    r = client.request("DELETE", f"/api/videos/{VIDEO_ID}", json=DELETE_BODY, headers=bearer(make_token()))
    assert r.status_code == 403


def video_row(**overrides):
    row = {
        "id": VIDEO_ID,
        "url": YT,
        "platform": "YOUTUBE",
        "title": None,
        "description": None,
        "hashtags": [],
        "thumbnail_url": None,
        "summary": "A short summary",
        "category_id": CATEGORY_ID,
        "processing_status": "PROCESSING",
        "saved_at": "2026-10-13T08:00:00Z",
    }
    row.update(overrides)
    return row


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def sql_calls(fake_db, fragment):
    return [c for c in fake_db.calls if fragment in c[0]]


def test_save_requires_token(client):
    assert client.post("/api/videos", json={"url": YT}).status_code == 401


def test_save_creates_item_and_saved_event(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC, None, video_row()]
    r = client.post("/api/videos", json={"url": YT + "&list=PLx"}, headers=bearer(make_token()))
    assert r.status_code == 201
    body = r.json()
    assert body["id"] == VIDEO_ID
    assert "category_id" not in body 

    (insert,) = sql_calls(fake_db, "insert into public.videos")
    assert insert[1] == (PID, YT, YT, "YOUTUBE", "PASTE", None)

    (event,) = sql_calls(fake_db, "insert into public.interaction_events")
    params = event[1]
    assert params[0] == PID
    assert params[2] == "SAVED"
    assert params[5] == "BASIC"
    assert params[6] == f"saved:{VIDEO_ID}"
    assert json.loads(params[7]) == {"platform": "YOUTUBE"}


def test_saving_same_link_again_returns_existing_and_creates_nothing(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC, video_row()]
    r = client.post("/api/videos", json={"url": YT}, headers=bearer(make_token()))
    assert r.status_code == 200
    assert r.json()["id"] == VIDEO_ID
    assert sql_calls(fake_db, "insert into public.videos") == []
    assert sql_calls(fake_db, "insert into public.interaction_events") == []


def test_rejected_link_logs_event_and_returns_422(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC]
    r = client.post(
        "/api/videos",
        json={"url": "https://evil.com/x", "idempotency_key": "client-key-0001"},
        headers=bearer(make_token()),
    )
    assert r.status_code == 422
    assert r.json() == {"detail": {"code": "UNSUPPORTED_HOST"}}
    assert sql_calls(fake_db, "insert into public.videos") == []

    (event,) = sql_calls(fake_db, "insert into public.interaction_events")
    params = event[1]
    assert params[2] == "SAVE_REJECTED"
    assert params[6] == "save_rejected:client-key-0001"
    assert json.loads(params[7]) == {"reason": "UNSUPPORTED_HOST"}
    assert "evil.com" not in str(params)  


def test_short_link_is_resolved_before_saving(client, make_token, fake_db, monkeypatch):
    final = "https://www.tiktok.com/@u/video/1234567890"
    monkeypatch.setattr(
        videos_module, "resolve_short_link", lambda url: ValidatedUrl(TIKTOK, final)
    )
    fake_db.queue = [
        dict(SAMPLE_PARTICIPANT),
        BASIC,
        None,
        video_row(url=final, platform="TIKTOK"),
    ]
    r = client.post(
        "/api/videos", json={"url": "https://vm.tiktok.com/ZMabc123/"}, headers=bearer(make_token())
    )
    assert r.status_code == 201
    (insert,) = sql_calls(fake_db, "insert into public.videos")
    assert insert[1][1:4] == (final, final, "TIKTOK")


def test_unresolvable_short_link_is_rejected_and_logged(client, make_token, fake_db, monkeypatch):
    def refuse(url):
        raise UrlRejected("REDIRECT_BLOCKED")

    monkeypatch.setattr(videos_module, "resolve_short_link", refuse)
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC]
    r = client.post(
        "/api/videos", json={"url": "https://vm.tiktok.com/ZMabc123/"}, headers=bearer(make_token())
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "REDIRECT_BLOCKED"
    assert len(sql_calls(fake_db, "insert into public.interaction_events")) == 1
    assert sql_calls(fake_db, "insert into public.videos") == []


def test_client_cannot_supply_identity(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC]
    r = client.post(
        "/api/videos",
        json={"url": YT, "participant_id": "someone-else"},
        headers=bearer(make_token()),
    )
    assert r.status_code == 422
    assert sql_calls(fake_db, "insert into public.videos") == []


def test_client_cannot_use_server_only_save_sources(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC]
    r = client.post(
        "/api/videos", json={"url": YT, "save_source": "IOS_SHORTCUT"}, headers=bearer(make_token())
    )
    assert r.status_code == 422
    assert sql_calls(fake_db, "insert into public.videos") == []


def test_withdrawn_participant_cannot_save(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT, status="WITHDRAWN")]
    r = client.post("/api/videos", json={"url": YT}, headers=bearer(make_token()))
    assert r.status_code == 403
    assert sql_calls(fake_db, "insert into public.videos") == []
    assert sql_calls(fake_db, "insert into public.interaction_events") == []


def test_save_without_active_condition_is_409(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), None]
    r = client.post("/api/videos", json={"url": YT}, headers=bearer(make_token()))
    assert r.status_code == 409
    assert sql_calls(fake_db, "insert into public.videos") == []


def test_reads_require_token(client):
    assert client.get("/api/videos").status_code == 401
    assert client.get(f"/api/videos/{VIDEO_ID}").status_code == 401


def test_list_hides_classification_in_basic(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC]
    fake_db.all_rows = [video_row()]
    r = client.get("/api/videos", headers=bearer(make_token()))
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert "category_id" not in item
    assert item["summary"] == "A short summary"
    (query,) = sql_calls(fake_db, "order by saved_at desc")
    assert query[1] == (PID, 50, 0) 


def test_list_includes_category_in_full(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), FULL]
    fake_db.all_rows = [video_row()]
    r = client.get("/api/videos", headers=bearer(make_token()))
    assert r.json()["items"][0]["category_id"] == CATEGORY_ID


def test_list_limit_is_bounded(client, make_token):
    r = client.get("/api/videos?limit=1000", headers=bearer(make_token()))
    assert r.status_code == 422


def test_get_video_is_scoped_to_its_owner(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC, video_row()]
    r = client.get(f"/api/videos/{VIDEO_ID}", headers=bearer(make_token()))
    assert r.status_code == 200
    (query,) = sql_calls(fake_db, "where id = %s::uuid")
    assert str(query[1][0]) == VIDEO_ID
    assert query[1][1] == PID  


def test_get_video_not_found_is_404(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC, None]
    r = client.get(f"/api/videos/{VIDEO_ID}", headers=bearer(make_token()))
    assert r.status_code == 404


def test_get_video_with_malformed_id_is_422(client, make_token):
    r = client.get("/api/videos/not-a-uuid", headers=bearer(make_token()))
    assert r.status_code == 422


def test_retry_requires_token(client):
    assert client.post(f"/api/videos/{VIDEO_ID}/retry").status_code == 401


def test_retry_failed_item_is_requeued(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC, video_row(processing_status="PROCESSING")]
    r = client.post(f"/api/videos/{VIDEO_ID}/retry", headers=bearer(make_token()))
    assert r.status_code == 202
    assert r.json()["processing_status"] == "PROCESSING"
    assert "category_id" not in r.json()
    (update,) = sql_calls(fake_db, "set processing_status = 'PROCESSING'")
    assert (str(update[1][0]), update[1][1]) == (VIDEO_ID, PID)
    assert sql_calls(fake_db, "insert into public.videos") == []


def test_retry_already_processing_changes_nothing(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC, None, video_row(processing_status="PROCESSING")]
    r = client.post(f"/api/videos/{VIDEO_ID}/retry", headers=bearer(make_token()))
    assert r.status_code == 200


def test_retry_completed_item_is_409(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC, None, video_row(processing_status="COMPLETED")]
    r = client.post(f"/api/videos/{VIDEO_ID}/retry", headers=bearer(make_token()))
    assert r.status_code == 409
    assert r.json() == {"detail": {"code": "NOT_RETRYABLE"}}


def test_retry_missing_or_foreign_item_is_404(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), BASIC, None, None]
    r = client.post(f"/api/videos/{VIDEO_ID}/retry", headers=bearer(make_token()))
    assert r.status_code == 404


def test_retry_withdrawn_participant_is_403(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT, status="WITHDRAWN")]
    r = client.post(f"/api/videos/{VIDEO_ID}/retry", headers=bearer(make_token()))
    assert r.status_code == 403


def test_retry_with_malformed_id_is_422(client, make_token):
    r = client.post("/api/videos/not-a-uuid/retry", headers=bearer(make_token()))
    assert r.status_code == 422