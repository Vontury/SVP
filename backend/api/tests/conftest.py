import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app import auth
from app.config import Settings, get_settings
from app.main import app
from app.db import get_conn


SUPABASE_URL = "https://test.supabase.co"
ISSUER = f"{SUPABASE_URL}/auth/v1"
USER_ID = "11111111-1111-1111-1111-111111111111"
SAMPLE_PARTICIPANT = {
    "id": "22222222-2222-2222-2222-222222222222",
    "study_code": "S001",
    "status": "TEST",
    "pilot_flag": False,
    "primary_device": None,
    "region_code": None,
    "created_at": "2026-10-13T00:00:00Z",
}


@pytest.fixture(scope="session")
def private_key():
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def client(private_key, fake_db, monkeypatch):
    fake_jwks = SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(
            key=private_key.public_key()
        )
    )
    monkeypatch.setattr(auth, "_jwks_client", lambda url: fake_jwks)
    app.dependency_overrides[get_settings] = lambda: Settings(
        supabase_url=SUPABASE_URL, database_url="postgresql://unused"
    )
    app.dependency_overrides[get_conn] = lambda: fake_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def make_token(private_key):
    def _make(key=None, **overrides):
        now = int(time.time())
        claims = {
            "sub": USER_ID,
            "aud": "authenticated",
            "iss": ISSUER,
            "role": "authenticated",
            "iat": now,
            "exp": now + 3600,
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, key or private_key, algorithm="ES256")

    return _make

class FakeConn:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchone(self):
        return self.row


@pytest.fixture
def fake_db():
    return FakeConn(dict(SAMPLE_PARTICIPANT))