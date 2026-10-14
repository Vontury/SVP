from tests.conftest import SAMPLE_PARTICIPANT, USER_ID

CONSENT = {"consent_version": "draft-1"}


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def calls_on(fake_db, table):
    return [c for c in fake_db.calls if table in c[0]]


def test_consent_requires_token(client):
    assert client.post("/api/consent", json=CONSENT).status_code == 401


def test_consent_creates_participant_consent_and_condition(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT)]
    r = client.post("/api/consent", json=CONSENT, headers=bearer(make_token()))
    assert r.status_code == 201
    assert "auth_user_id" not in r.json()

    participant_calls = calls_on(fake_db, "public.participants")
    assert len(participant_calls) == 1
    assert participant_calls[0][1][0] == USER_ID  
    assert participant_calls[0][1][1].startswith("SVP-")

    consent_calls = calls_on(fake_db, "public.consent_records")
    assert len(consent_calls) == 1
    assert consent_calls[0][1][1] == "draft-1"

    assert len(calls_on(fake_db, "public.study_condition_history")) == 1


def test_consent_rejects_client_supplied_identity(client, make_token, fake_db):
    body = {**CONSENT, "participant_id": "someone-else"}
    r = client.post("/api/consent", json=body, headers=bearer(make_token()))
    assert r.status_code == 422
    assert fake_db.calls == []


def test_consent_wrong_version_is_409(client, make_token, fake_db):
    r = client.post(
        "/api/consent",
        json={"consent_version": "old"},
        headers=bearer(make_token()),
    )
    assert r.status_code == 409
    assert fake_db.calls == []


def test_consent_repeat_returns_existing_and_creates_nothing(client, make_token, fake_db):
    fake_db.queue = [None, dict(SAMPLE_PARTICIPANT)] 
    r = client.post("/api/consent", json=CONSENT, headers=bearer(make_token()))
    assert r.status_code == 200
    assert r.json()["study_code"] == "S001"
    assert calls_on(fake_db, "public.consent_records") == []
    assert calls_on(fake_db, "public.study_condition_history") == []


def test_consent_retries_study_code_collision(client, make_token, fake_db):
    fake_db.fail_participant_inserts = 1
    fake_db.queue = [dict(SAMPLE_PARTICIPANT)]
    r = client.post("/api/consent", json=CONSENT, headers=bearer(make_token()))
    assert r.status_code == 201
    inserts = [c for c in fake_db.calls if "insert into public.participants" in c[0]]
    assert len(inserts) == 2


def test_condition_requires_token(client):
    assert client.get("/api/me/condition").status_code == 401


def test_condition_returns_current(client, make_token, fake_db):
    fake_db.queue = [
        dict(SAMPLE_PARTICIPANT),
        {"condition": "BASIC", "valid_from": "2026-10-13T00:00:00Z"},
    ]
    r = client.get("/api/me/condition", headers=bearer(make_token()))
    assert r.status_code == 200
    assert r.json()["condition"] == "BASIC"
    assert fake_db.calls[-1][1] == (SAMPLE_PARTICIPANT["id"],)


def test_condition_missing_is_409(client, make_token, fake_db):
    fake_db.queue = [dict(SAMPLE_PARTICIPANT), None]
    r = client.get("/api/me/condition", headers=bearer(make_token()))
    assert r.status_code == 409