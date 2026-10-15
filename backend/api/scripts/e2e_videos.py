import json
import sys
import uuid

import httpx
import psycopg
from dotenv import dotenv_values
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation
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

LINK_CLEAN = "https://www.youtube.com/watch?v=e2eTestAB01"
LINK_SENT = LINK_CLEAN + "&list=PLtest&si=trackingvalue"  # extra parameters on purpose
BAD_LINK = "https://evil.example.com/watch?v=e2eTestAB01"
REJECT_KEY = "e2e-reject-0001"

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
    return httpx.request(method, API + path, headers=headers, timeout=30, **kwargs)


def participant_id(email):
    row = conn.execute(
        """
        select p.id from public.participants p
        join auth.users u on u.id = p.auth_user_id
        where u.email = %s
        """,
        (email,),
    ).fetchone()
    if row is None:
        sys.exit(f"{email} has no participant. Run scripts/e2e_two_participants.py first.")
    return str(row["id"])


def expect_db_error(name, sql, params, error_type):
    try:
        with conn.transaction(force_rollback=True):
            conn.execute(sql, params)
    except error_type:
        check(name, True)
        return
    except psycopg.Error as exc:
        check(name, False, f"wrong error: {type(exc).__name__}")
        return
    check(name, False, "the database accepted it")


users = {}
for label in ("A", "B"):
    token = sign_in(label)
    r = api("GET", "/api/me/condition", token)
    if r.status_code != 200:
        sys.exit(f"User {label} has no condition ({r.status_code}). Run e2e_two_participants.py first.")
    users[label] = {
        "token": token,
        "pid": participant_id(env[f"TEST_{label}_EMAIL"]),
        "condition": r.json()["condition"],
    }

check("save without a token -> 401",
      api("POST", "/api/videos", json={"url": LINK_SENT}).status_code == 401)

video = {}
for label in ("A", "B"):
    token = users[label]["token"]
    r = api("POST", "/api/videos", token, json={"url": LINK_SENT})
    check(f"{label}: save accepted (201 first run, 200 on re-run)", r.status_code in (200, 201), r.status_code)
    if r.status_code not in (200, 201):
        sys.exit(f"Stopping: save returned {r.status_code}: {r.text[:200]}")
    video[label] = r.json()
    check(f"{label}: link stored without the extra parameters", video[label]["url"] == LINK_CLEAN, video[label]["url"])
    check(f"{label}: platform detected as YOUTUBE", video[label]["platform"] == "YOUTUBE")
    check(f"{label}: classification field hidden unless FULL",
          ("category_id" in video[label]) == (users[label]["condition"] == "FULL"))
    again = api("POST", "/api/videos", token, json={"url": LINK_SENT})
    check(f"{label}: saving the same link again -> 200 with the same item",
          again.status_code == 200 and again.json().get("id") == video[label]["id"], again.status_code)

check("A and B get different items for the same link", video["A"]["id"] != video["B"]["id"])

for label, other in (("A", "B"), ("B", "A")):
    token = users[label]["token"]
    r = api("GET", "/api/videos", token, params={"limit": 100})
    ids = [i["id"] for i in r.json().get("items", [])] if r.status_code == 200 else []
    check(f"{label}: list has its own item and not {other}'s",
          r.status_code == 200 and video[label]["id"] in ids and video[other]["id"] not in ids)
    check(f"{label}: can open its own item",
          api("GET", f"/api/videos/{video[label]['id']}", token).status_code == 200)
    r = api("GET", f"/api/videos/{video[other]['id']}", token)
    check(f"{label}: {other}'s item -> 404", r.status_code == 404, r.status_code)

ta = users["A"]["token"]
check("an item that does not exist -> 404",
      api("GET", f"/api/videos/{uuid.uuid4()}", ta).status_code == 404)
check("a malformed item id -> 422",
      api("GET", "/api/videos/not-a-uuid", ta).status_code == 422)

for attempt in (1, 2):
    r = api("POST", "/api/videos", ta, json={"url": BAD_LINK, "idempotency_key": REJECT_KEY})
    check(f"A: unsupported link rejected (attempt {attempt})",
          r.status_code == 422 and r.json() == {"detail": {"code": "UNSUPPORTED_HOST"}}, r.text[:100])
r = api("POST", "/api/videos", ta, json={"url": LINK_SENT, "participant_id": users["B"]["pid"]})
check("a client-supplied participant_id is refused", r.status_code == 422, r.status_code)

for label in ("A", "B"):
    pid, vid = users[label]["pid"], video[label]["id"]

    n = conn.execute(
        "select count(*) as n from public.videos "
        "where participant_id = %s::uuid and url_normalized = %s and deleted_at is null",
        (pid, LINK_CLEAN),
    ).fetchone()["n"]
    check(f"{label}: exactly one stored item for the link", n == 1, n)

    owner = conn.execute(
        "select participant_id from public.videos where id = %s::uuid", (vid,)
    ).fetchone()
    check(f"{label}: the item belongs to {label}",
          owner is not None and str(owner["participant_id"]) == pid)

    events = conn.execute(
        """
        select e.condition_at_event, e.source, e.idempotency_key, e.payload,
               h.condition as expected_condition
        from public.interaction_events e
        join public.study_condition_history h
          on h.participant_id = e.participant_id
         and h.valid_from <= e.occurred_at
         and (h.valid_to is null or h.valid_to > e.occurred_at)
        where e.participant_id = %s::uuid and e.event_type = 'SAVED' and e.video_id = %s::uuid
        """,
        (pid, vid),
    ).fetchall()
    check(f"{label}: exactly one SAVED event", len(events) == 1, len(events))
    if len(events) == 1:
        e = events[0]
        check(f"{label}: SAVED event has the right condition, source, key and payload",
              e["condition_at_event"] == e["expected_condition"]
              and e["source"] == "PASTE"
              and e["idempotency_key"] == f"saved:{vid}"
              and e["payload"] == {"platform": "YOUTUBE"}, e)

pid_a, pid_b = users["A"]["pid"], users["B"]["pid"]
vid_a, vid_b = video["A"]["id"], video["B"]["id"]

rows = conn.execute(
    "select payload from public.interaction_events "
    "where participant_id = %s::uuid and event_type = 'SAVE_REJECTED' and idempotency_key = %s",
    (pid_a, f"save_rejected:{REJECT_KEY}"),
).fetchall()
check("A: the repeated rejection produced exactly one event", len(rows) == 1, len(rows))
check("A: the rejection event holds only the reason code",
      len(rows) == 1 and rows[0]["payload"] == {"reason": "UNSUPPORTED_HOST"})
n = conn.execute(
    "select count(*) as n from public.videos where participant_id = %s::uuid and url_normalized like %s",
    (pid_a, "%evil.example.com%"),
).fetchone()["n"]
check("A: the rejected link was not stored as an item", n == 0, n)
n = conn.execute(
    "select count(*) as n from public.interaction_events where participant_id = %s::uuid and payload::text like %s",
    (pid_a, "%evil.example.com%"),
).fetchone()["n"]
check("A: the rejected link appears in no event payload", n == 0, n)

INSERT_EVENT = """
    insert into public.interaction_events
        (participant_id, video_id, event_type, occurred_at, condition_at_event,
         idempotency_key, payload)
    values (%s::uuid, %s::uuid, %s, now(), 'BASIC', %s, %s::jsonb)
"""
expect_db_error("DB: an event pointing at another participant's item is refused",
                INSERT_EVENT, (pid_a, vid_b, "OPENED", "e2e-fk-test", "{}"), ForeignKeyViolation)
expect_db_error("DB: reusing an idempotency key is refused",
                INSERT_EVENT, (pid_a, vid_a, "OPENED", f"saved:{vid_a}", "{}"), UniqueViolation)
expect_db_error("DB: an oversized event payload is refused",
                INSERT_EVENT,
                (pid_a, None, "DASHBOARD_VIEWED", "e2e-big-payload", json.dumps({"x": "a" * 3000})),
                CheckViolation)
expect_db_error("DB: an unknown event type is refused",
                INSERT_EVENT, (pid_a, None, "NOT_A_REAL_EVENT", "e2e-bad-type", "{}"), CheckViolation)

event_id = conn.execute(
    "select id from public.interaction_events "
    "where participant_id = %s::uuid and event_type = 'SAVED' and video_id = %s::uuid",
    (pid_a, vid_a),
).fetchone()["id"]
after = None
try:
    with conn.transaction(force_rollback=True):
        conn.execute("update public.interaction_events set source = 'CHANGED' where id = %s", (event_id,))
        after = conn.execute(
            "select source from public.interaction_events where id = %s", (event_id,)
        ).fetchone()["source"]
except psycopg.Error:
    after = None  
check("DB: a stored event cannot be changed", after != "CHANGED", after)

print()
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
sys.exit(1 if failures else 0)