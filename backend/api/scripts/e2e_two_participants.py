import base64
import json
import sys

import httpx
import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

env = dotenv_values(".env")
REQUIRED = [
    "SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "DATABASE_URL",
    "TEST_A_EMAIL", "TEST_A_PASSWORD", "TEST_B_EMAIL", "TEST_B_PASSWORD",
]
missing = [k for k in REQUIRED if not env.get(k)]
if missing:
    sys.exit("Missing in .env: " + ", ".join(missing))

API = f"http://localhost:{env.get('PORT') or '8083'}"
SUPABASE_URL = env["SUPABASE_URL"].rstrip("/")
ISSUER = f"{SUPABASE_URL}/auth/v1"
CONSENT_VERSION = env.get("CONSENT_VERSION") or "draft-1"

failures = 0


def check(name, ok, detail=""):
    global failures
    suffix = f"  [{detail}]" if detail != "" and not ok else ""
    print(("PASS  " if ok else "FAIL  ") + name + suffix)
    if not ok:
        failures += 1


def decode_part(token, index):
    part = token.split(".")[index]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


def sign_in(label):
    r = httpx.post(
        f"{SUPABASE_URL}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": env["SUPABASE_PUBLISHABLE_KEY"]},
        json={"email": env[f"TEST_{label}_EMAIL"], "password": env[f"TEST_{label}_PASSWORD"]},
        timeout=20,
    )
    if r.status_code != 200:
        sys.exit(f"Login failed for user {label}: HTTP {r.status_code} {r.text[:200]}")
    return r.json()["access_token"]


def api(method, path, token=None, **kwargs):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.request(method, API + path, headers=headers, timeout=20, **kwargs)


tokens = {label: sign_in(label) for label in ("A", "B")}
sub = {}
for label, token in tokens.items():
    header, claims = decode_part(token, 0), decode_part(token, 1)
    sub[label] = claims["sub"]
    check(
        f"user {label}: real token is ES256 from the expected issuer",
        header.get("alg") == "ES256" and claims.get("iss") == ISSUER,
        f"alg={header.get('alg')} iss={claims.get('iss')}",
    )

check("no token -> 401", api("GET", "/api/me").status_code == 401)
check("junk token -> 401", api("GET", "/api/me", "abc").status_code == 401)

profile = {}
for label, token in tokens.items():
    r = api("POST", "/api/consent", token, json={"consent_version": "wrong"})
    check(f"user {label}: wrong consent version -> 409", r.status_code == 409, r.status_code)

    r = api("POST", "/api/consent", token, json={"consent_version": CONSENT_VERSION})
    check(f"user {label}: consent accepted (201 first run, 200 on re-run)",
          r.status_code in (200, 201), r.status_code)
    if r.status_code not in (200, 201):
        sys.exit(f"Stopping: consent returned {r.status_code}: {r.text[:200]}")
    profile[label] = r.json()

    r = api("GET", "/api/me", token)
    check(f"user {label}: /api/me returns the same profile",
          r.status_code == 200 and r.json().get("study_code") == profile[label]["study_code"])
    check(f"user {label}: auth_user_id not exposed", "auth_user_id" not in r.json())

    r = api("GET", "/api/me/condition", token)
    check(f"user {label}: condition is BASIC",
          r.status_code == 200 and r.json().get("condition") == "BASIC", r.text[:100])

check("A and B are different participants with different codes",
      profile["A"]["id"] != profile["B"]["id"]
      and profile["A"]["study_code"] != profile["B"]["study_code"])

r = api("POST", "/api/consent", tokens["A"], json={"consent_version": CONSENT_VERSION})
check("A: repeating consent -> 200 with the same profile",
      r.status_code == 200 and r.json().get("study_code") == profile["A"]["study_code"], r.status_code)

COUNTS = """
    select p.id,
           (select count(*) from public.consent_records c
             where c.participant_id = p.id) as consents,
           (select count(*) from public.study_condition_history h
             where h.participant_id = p.id and h.valid_to is null) as open_conditions
    from public.participants p
    where p.auth_user_id = %s::uuid
"""

with psycopg.connect(env["DATABASE_URL"], autocommit=True, row_factory=dict_row) as conn:
    for label in ("A", "B"):
        row = conn.execute(COUNTS, (sub[label],)).fetchone()
        check(f"user {label}: participant row exists in the database", row is not None)
        if row is None:
            continue
        check(f"user {label}: API profile matches the row linked to this login",
              str(row["id"]) == profile[label]["id"])
        check(f"user {label}: exactly one consent record", row["consents"] == 1, row["consents"])
        check(f"user {label}: exactly one open condition row",
              row["open_conditions"] == 1, row["open_conditions"])
        conn.execute(
            "update public.participants set pilot_flag = true where auth_user_id = %s::uuid",
            (sub[label],),
        )

for label, token in tokens.items():
    r = api("GET", "/api/me", token)
    check(f"user {label}: flagged pilot_flag = true", r.json().get("pilot_flag") is True)

print()
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
sys.exit(1 if failures else 0)