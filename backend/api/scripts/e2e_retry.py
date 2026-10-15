import sys
import time
import uuid

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
LINK = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

failures = 0
conn = psycopg.connect(env["DATABASE_URL"], autocommit=True, row_factory=dict_row)


def check(name, ok, detail=""):
    global failures
    suffix = f"  [{detail}]" if detail != "" and not ok else ""
    print(("PASS  " if ok else "FAIL  ") + name + suffix)
    if not ok:
        failures += 1


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
    return httpx.request(method, API + path, headers=headers, timeout=60, **kwargs)


def status_of(vid):
    return conn.execute(
        "select processing_status, retry_count, next_retry_at from public.videos where id = %s::uuid",
        (vid,),
    ).fetchone()


def wait_until(vid, predicate, seconds=120):
    end = time.time() + seconds
    while time.time() < end:
        row = status_of(vid)
        if predicate(row):
            return row
        time.sleep(3)
    return status_of(vid)


ta, tb = sign_in("A"), sign_in("B")

r = api("POST", "/api/videos", ta, json={"url": LINK})
if r.status_code not in (200, 201):
    sys.exit(f"Stopping: save returned {r.status_code}: {r.text[:200]}")
vid = r.json()["id"]

row = wait_until(vid, lambda s: s["processing_status"] != "PROCESSING")
check("item finished its first processing", row["processing_status"] != "PROCESSING", row)

check("retry without a token -> 401", api("POST", f"/api/videos/{vid}/retry").status_code == 401)
check("B retrying A's item -> 404", api("POST", f"/api/videos/{vid}/retry", tb).status_code == 404)
check("a random item id -> 404", api("POST", f"/api/videos/{uuid.uuid4()}/retry", ta).status_code == 404)
check("malformed id -> 422", api("POST", "/api/videos/not-a-uuid/retry", ta).status_code == 422)

r = api("POST", f"/api/videos/{vid}/retry", ta)
check("retrying a finished (not failed) item -> 409",
      r.status_code == 409 and r.json() == {"detail": {"code": "NOT_RETRYABLE"}}, r.text[:100])

conn.execute(
    "update public.videos set processing_status = 'PROCESSING', retry_count = 1, "
    "next_retry_at = now() + interval '1 hour' where id = %s::uuid",
    (vid,),
)
r = api("POST", f"/api/videos/{vid}/retry", ta)
after = status_of(vid)
check("retrying an item already PROCESSING -> 200, nothing changed",
      r.status_code == 200 and after["retry_count"] == 1 and after["next_retry_at"] is not None,
      f"{r.status_code} {after}")

conn.execute(
    "update public.videos set processing_status = 'FAILED', processing_error_code = 'METADATA_UNAVAILABLE', "
    "retry_count = 3, next_retry_at = null, title = null, thumbnail_url = null where id = %s::uuid",
    (vid,),
)
r = api("POST", f"/api/videos/{vid}/retry", ta)
check("retrying a FAILED item -> 202 and PROCESSING",
      r.status_code == 202 and r.json().get("processing_status") == "PROCESSING", r.text[:120])
check("the retry returned the same item", r.status_code == 202 and r.json().get("id") == vid)

row = wait_until(vid, lambda s: s["processing_status"] != "PROCESSING")
check("worker picked it up and finished (COMPLETED or PARTIAL)",
      row["processing_status"] in ("COMPLETED", "PARTIAL"), row)
check("attempt counter restarted (1 attempt used)", row["retry_count"] == 1, row["retry_count"])

n = conn.execute(
    "select count(*) as n from public.videos where participant_id = "
    "(select participant_id from public.videos where id = %s::uuid) "
    "and url_normalized = %s and deleted_at is null",
    (vid, LINK),
).fetchone()["n"]
check("still exactly one item for the link", n == 1, n)

print()
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
sys.exit(1 if failures else 0)