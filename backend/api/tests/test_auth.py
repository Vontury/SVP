import time

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from tests.conftest import ISSUER, USER_ID


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_health_is_public(client):
    assert client.get("/api/health").status_code == 200


def test_me_without_token_is_401(client):
    assert client.get("/api/me").status_code == 401

def test_me_returns_own_participant_row(client, make_token, fake_db):
    r = client.get("/api/me", headers=bearer(make_token()))
    assert r.status_code == 200
    body = r.json()
    assert body["study_code"] == "S001"
    assert "auth_user_id" not in body
    assert fake_db.calls[0][1] == (USER_ID,)


def test_me_without_participant_row_is_403(client, make_token, fake_db):
    fake_db.row = None
    r = client.get("/api/me", headers=bearer(make_token()))
    assert r.status_code == 403

def test_expired_token_is_401(client, make_token):
    token = make_token(exp=int(time.time()) - 60)
    assert client.get("/api/me", headers=bearer(token)).status_code == 401


def test_wrong_audience_is_401(client, make_token):
    token = make_token(aud="anon")
    assert client.get("/api/me", headers=bearer(token)).status_code == 401


def test_wrong_issuer_is_401(client, make_token):
    token = make_token(iss="https://evil.example.com/auth/v1")
    assert client.get("/api/me", headers=bearer(token)).status_code == 401


def test_missing_sub_is_401(client, make_token):
    token = make_token(sub=None)
    assert client.get("/api/me", headers=bearer(token)).status_code == 401


def test_token_signed_with_other_key_is_401(client, make_token):
    other_key = ec.generate_private_key(ec.SECP256R1())
    token = make_token(key=other_key)
    assert client.get("/api/me", headers=bearer(token)).status_code == 401


def test_hs256_token_is_rejected(client):
    now = int(time.time())
    token = jwt.encode(
        {"sub": USER_ID, "aud": "authenticated", "iss": ISSUER, "exp": now + 3600},
        "a-secret-that-is-at-least-32-characters-long",
        algorithm="HS256",
    )
    assert client.get("/api/me", headers=bearer(token)).status_code == 401