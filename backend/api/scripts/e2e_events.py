import sys
import uuid
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

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
LINK = "https://www.youtube.com/watch?v=e2eEvents01"
RUN = uuid.uuid4().hex[:10]

failures = 0
conn = psycopg.connect(env["DATABASE_URL"], autocommit=True, row_factory=dict_row)


def check(name, ok, detail=""):
    global failures
    suffix = f"  [{detail}]" if detail != "" and not ok else ""
    print(("PASS  " if ok else "FAIL  ") + name + suffix)
    if not ok:
        failures += 1


def key(name):
    return f"e2e-{RUN}-{name}"


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
    response = httpx.request(method, API + path, headers=headers, timeout=60, **kwargs)
    time.sleep(0.5)
    return response


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


def batch(token, events):
    return api("POST", "/api/events/batch", token, json={"events": events})


def ev(event_type, name, **extra):
    return {"event_type": event_type, "idempotency_key": key(name), **extra}


def count_by_key(pid, name):
    return conn.execute(
        "select count(*) as n from public.interaction_events "
        "where participant_id = %s::uuid and idempotency_key = %s",
        (pid, key(name)),
    ).fetchone()["n"]


def count_events(pid, vid, event_type):
    return conn.execute(
        "select count(*) as n from public.interaction_events "
        "where participant_id = %s::uuid and video_id = %s::uuid and event_type = %s",
        (pid, vid, event_type),
    ).fetchone()["n"]


def video_row(vid):
    return conn.execute(
        "select open_count, first_opened_at, last_opened_at, deleted_at "
        "from public.videos where id = %s::uuid",
        (vid,),
    ).fetchone()


def open_sessions(pid):
    return conn.execute(
        "select id, started_at, last_activity_at from public.app_sessions "
        "where participant_id = %s::uuid and ended_at is null order by started_at",
        (pid,),
    ).fetchall()


def event_row(pid, name):
    return conn.execute(
        "select session_id, occurred_at, received_at from public.interaction_events "
        "where participant_id = %s::uuid and idempotency_key = %s",
        (pid, key(name)),
    ).fetchone()


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

ta, tb = users["A"]["token"], users["B"]["token"]
pid_a, pid_b = users["A"]["pid"], users["B"]["pid"]

video = {}
for label in ("A", "B"):
    r = api("POST", "/api/videos", users[label]["token"], json={"url": LINK})
    if r.status_code not in (200, 201):
        sys.exit(f"Stopping: save returned {r.status_code}: {r.text[:200]}")
    video[label] = r.json()["id"]
vid_a, vid_b = video["A"], video["B"]

check("batch without a token -> 401", api("POST", "/api/events/batch", json={"events": []}).status_code == 401)

category = str(uuid.uuid4())
first = [
    ev("DASHBOARD_VIEWED", "dv1", source_block="CHRONOLOGICAL_LIST", device_type="DESKTOP"),
    ev("CATEGORY_SELECTED", "cs1", payload={"category_id": category}),
]
r = batch(ta, first)
check("A: first batch accepted 2", r.status_code == 200 and r.json() == {"accepted": 2, "duplicates": 0}, r.text[:120])
r = batch(ta, first)
check("A: the same batch again -> 0 accepted, 2 duplicates",
      r.status_code == 200 and r.json() == {"accepted": 0, "duplicates": 2}, r.text[:120])

rows = conn.execute(
    """
    select e.idempotency_key, e.session_id, e.source_block, e.device_type, e.payload,
           e.condition_at_event, h.condition as expected
    from public.interaction_events e
    join public.study_condition_history h
      on h.participant_id = e.participant_id
     and h.valid_from <= e.occurred_at
     and (h.valid_to is null or h.valid_to > e.occurred_at)
    where e.participant_id = %s::uuid and e.idempotency_key = any(%s::text[])
    """,
    (pid_a, [key("dv1"), key("cs1")]),
).fetchall()
by_key = {r["idempotency_key"]: r for r in rows}
check("A: exactly one stored row per event", len(rows) == 2, len(rows))
check("A: both events carry a session", all(r["session_id"] is not None for r in rows))
check("A: both events share one session", len({r["session_id"] for r in rows}) == 1)
check("A: condition_at_event matches the condition history",
      all(r["condition_at_event"] == r["expected"] for r in rows))
dv = by_key.get(key("dv1"), {})
check("A: source_block and device_type stored", dv.get("source_block") == "CHRONOLOGICAL_LIST" and dv.get("device_type") == "DESKTOP", dv)
check("A: category payload stored", by_key.get(key("cs1"), {}).get("payload") == {"category_id": category})
check("A: exactly one open session", len(open_sessions(pid_a)) == 1, len(open_sessions(pid_a)))

r = batch(ta, [ev("DASHBOARD_VIEWED", "rj0"), ev("OPENED", "rj1")])
check("a server-only event type rejects the whole batch",
      r.status_code == 422 and r.json() == {"detail": {"code": "EVENT_TYPE_NOT_ALLOWED", "index": 1}}, r.text[:120])
check("nothing from the rejected batch was stored", count_by_key(pid_a, "rj0") == 0 and count_by_key(pid_a, "rj1") == 0)

reason_payload = {"reason_context": "SAVE_REASON", "prompt_version": "p1", "codebook_version": "c1"}
if users["A"]["condition"] == "BASIC":
    r = batch(ta, [ev("REASON_BUTTON_CLICKED", "rs1", video_id=vid_a, payload=reason_payload)])
    check("A (BASIC): REASON events are rejected",
          r.status_code == 422 and r.json() == {"detail": {"code": "NOT_ALLOWED_IN_CONDITION", "index": 0}}, r.text[:120])
    check("A (BASIC): no REASON event stored", count_by_key(pid_a, "rs1") == 0)
else:
    r = batch(ta, [ev("REASON_BUTTON_CLICKED", "rs1", video_id=vid_a, payload=reason_payload)])
    check("A (FULL): REASON events are accepted", r.status_code == 200 and r.json()["accepted"] == 1, r.text[:120])

r = batch(ta, [ev("DASHBOARD_VIEWED", "fv1", video_id=vid_b)])
check("A cannot attach events to B's item",
      r.status_code == 422 and r.json() == {"detail": {"code": "VIDEO_NOT_FOUND", "index": 0}}, r.text[:120])
check("no event stored for the foreign item", count_by_key(pid_a, "fv1") == 0)

check("a client-supplied participant_id is refused",
      batch(ta, [ev("DASHBOARD_VIEWED", "id1", participant_id=pid_b)]).status_code == 422)
check("a client-supplied condition_at_event is refused",
      batch(ta, [ev("DASHBOARD_VIEWED", "id2", condition_at_event="FULL")]).status_code == 422)
check("an empty batch is refused", batch(ta, []).status_code == 422)
r51 = batch(ta, [ev("DASHBOARD_VIEWED", f"big{i}") for i in range(51)])
check("51 events are refused", r51.status_code == 422, f"{r51.status_code} {r51.text[:200]}")

old = batch(ta, [ev("DASHBOARD_VIEWED", "t_old", occurred_at="2000-01-01T00:00:00Z")])
check("old client timestamp: request accepted", old.status_code == 200, old.text[:120])
row = event_row(pid_a, "t_old")
check("old client timestamp: server time used",
      row is not None and abs((row["occurred_at"] - row["received_at"]).total_seconds()) < 5, row)

sent = datetime.now(timezone.utc) - timedelta(minutes=1)
batch(ta, [ev("DASHBOARD_VIEWED", "t_recent", occurred_at=sent.isoformat())])
row = event_row(pid_a, "t_recent")
check("recent client timestamp is kept",
      row is not None and abs((row["occurred_at"] - sent).total_seconds()) < 2, row)

current = open_sessions(pid_a)[0]
conn.execute(
    "update public.app_sessions set started_at = now() - interval '10 minutes', "
    "last_activity_at = now() - interval '5 minutes' where id = %s::uuid",
    (current["id"],),
)
batch(ta, [ev("DASHBOARD_VIEWED", "s_ext")])
row = event_row(pid_a, "s_ext")
after = conn.execute(
    "select last_activity_at from public.app_sessions where id = %s::uuid", (current["id"],)
).fetchone()
check("session within 30 minutes is reused", row is not None and row["session_id"] == current["id"], row)
check("session activity time was extended",
      after["last_activity_at"] > datetime.now(timezone.utc) - timedelta(minutes=1), after)

conn.execute(
    "update public.app_sessions set started_at = now() - interval '40 minutes', "
    "last_activity_at = now() - interval '31 minutes' where id = %s::uuid",
    (current["id"],),
)
stale = conn.execute(
    "select last_activity_at from public.app_sessions where id = %s::uuid", (current["id"],)
).fetchone()["last_activity_at"]
batch(ta, [ev("DASHBOARD_VIEWED", "s_new")])
row = event_row(pid_a, "s_new")
closed = conn.execute(
    "select ended_at, end_reason from public.app_sessions where id = %s::uuid", (current["id"],)
).fetchone()
check("expired session: a new session was opened", row is not None and row["session_id"] != current["id"], row)
check("expired session: closed at its last activity with TIMEOUT",
      closed["ended_at"] == stale and closed["end_reason"] == "TIMEOUT", closed)
check("exactly one open session afterwards", len(open_sessions(pid_a)) == 1, len(open_sessions(pid_a)))

live = open_sessions(pid_a)[0]
conn.execute(
    "update public.app_sessions set started_at = now() - interval '40 minutes', "
    "last_activity_at = now() - interval '31 minutes' where id = %s::uuid",
    (live["id"],),
)
with ThreadPoolExecutor(6) as pool:
    results = list(pool.map(lambda i: batch(ta, [ev("DASHBOARD_VIEWED", f"race{i}")]), range(6)))
check("parallel requests after a timeout all succeed", all(r.status_code == 200 for r in results),
      [r.status_code for r in results])
race_sessions = {event_row(pid_a, f"race{i}")["session_id"] for i in range(6)}
check("parallel requests share one new session", len(race_sessions) == 1 and live["id"] not in race_sessions, race_sessions)
check("still exactly one open session after the race", len(open_sessions(pid_a)) == 1, len(open_sessions(pid_a)))

open_path = f"/api/videos/{vid_a}/open"


def open_body(name, **extra):
    return {"idempotency_key": key(name), "source_block": "CHRONOLOGICAL_LIST", **extra}


check("open without a token -> 401", api("POST", open_path, json=open_body("o_x")).status_code == 401)
check("open with a bad body -> 422",
      api("POST", open_path, ta, json={"idempotency_key": "short", "source_block": "SEARCH"}).status_code == 422)
check("open with a client-supplied participant_id -> 422",
      api("POST", open_path, ta, json=open_body("o_y", participant_id=pid_b)).status_code == 422)

r = api("POST", open_path, ta, json=open_body("o1"))
check("A: first open recorded", r.status_code == 200 and r.json() == {"recorded": True, "event_type": "OPENED"}, r.text[:120])
state = video_row(vid_a)
first_open = state["first_opened_at"]
check("A: counters set after the first open", state["open_count"] == 1 and first_open is not None, state)
check("A: one OPENED event with its source_block",
      count_events(pid_a, vid_a, "OPENED") == 1 and event_row(pid_a, "o1") is not None)

r = api("POST", open_path, ta, json=open_body("o1"))
check("A: repeating the same open key records nothing", r.status_code == 200 and r.json()["recorded"] is False, r.text[:120])
check("A: counters unchanged after the repeat", video_row(vid_a)["open_count"] == 1)

r = api("POST", open_path, ta, json=open_body("o2"))
state = video_row(vid_a)
check("A: second open counted", r.status_code == 200 and state["open_count"] == 2, state)
check("A: first_opened_at never moves", state["first_opened_at"] == first_open and state["last_opened_at"] >= first_open)

r = api("POST", open_path, ta, json=open_body("x1", external=True))
check("A: external open logged as EXTERNAL_OPEN",
      r.status_code == 200 and r.json() == {"recorded": True, "event_type": "EXTERNAL_OPEN"}, r.text[:120])
check("A: external open leaves the counters alone", video_row(vid_a)["open_count"] == 2)
check("A: one EXTERNAL_OPEN event", count_events(pid_a, vid_a, "EXTERNAL_OPEN") == 1)

with ThreadPoolExecutor(5) as pool:
    results = list(pool.map(lambda i: api("POST", open_path, ta, json=open_body(f"par{i}")), range(5)))
check("A: 5 parallel opens all recorded",
      all(r.status_code == 200 and r.json()["recorded"] is True for r in results), [r.status_code for r in results])
check("A: counter shows 7 and there are 7 OPENED events",
      video_row(vid_a)["open_count"] == 7 and count_events(pid_a, vid_a, "OPENED") == 7,
      (video_row(vid_a)["open_count"], count_events(pid_a, vid_a, "OPENED")))

with ThreadPoolExecutor(5) as pool:
    results = list(pool.map(lambda i: api("POST", open_path, ta, json=open_body("same")), range(5)))
recorded = sum(1 for r in results if r.status_code == 200 and r.json()["recorded"] is True)
check("A: 5 parallel opens with one key record exactly once", recorded == 1, recorded)
check("A: counter shows 8", video_row(vid_a)["open_count"] == 8, video_row(vid_a)["open_count"])

check("B cannot open A's item -> 404", api("POST", open_path, tb, json=open_body("b_open")).status_code == 404)
check("B cannot delete A's item -> 404",
      api("DELETE", f"/api/videos/{vid_a}", tb, json={"idempotency_key": key("b_del")}).status_code == 404)
check("a missing item cannot be opened -> 404",
      api("POST", f"/api/videos/{uuid.uuid4()}/open", ta, json=open_body("o_none")).status_code == 404)
check("B left no events on A's item",
      conn.execute(
          "select count(*) as n from public.interaction_events where participant_id = %s::uuid and video_id = %s::uuid",
          (pid_b, vid_a),
      ).fetchone()["n"] == 0)
check("A's counters were not touched by B", video_row(vid_a)["open_count"] == 8)

delete_path = f"/api/videos/{vid_a}"
check("delete without a token -> 401",
      api("DELETE", delete_path, json={"idempotency_key": key("d_x")}).status_code == 401)
r = api("DELETE", delete_path, ta, json={"idempotency_key": key("d1")})
check("A: delete -> 204", r.status_code == 204, r.status_code)
check("A: item is soft-deleted", video_row(vid_a)["deleted_at"] is not None)
check("A: one DELETED event", count_events(pid_a, vid_a, "DELETED") == 1)

r = api("DELETE", delete_path, ta, json={"idempotency_key": key("d2")})
check("A: deleting again -> 404", r.status_code == 404, r.status_code)
check("A: still one DELETED event", count_events(pid_a, vid_a, "DELETED") == 1)
check("A: deleted item cannot be fetched", api("GET", f"/api/videos/{vid_a}", ta).status_code == 404)
listed = api("GET", "/api/videos", ta, params={"limit": 100}).json().get("items", [])
check("A: deleted item is not in the list", vid_a not in [i["id"] for i in listed])
check("A: deleted item cannot be opened", api("POST", open_path, ta, json=open_body("o_late")).status_code == 404)
check("A: OPENED events were kept", count_events(pid_a, vid_a, "OPENED") == 8)
check("B's item is untouched", video_row(vid_b)["deleted_at"] is None and video_row(vid_b)["open_count"] == 0)

print()
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
sys.exit(1 if failures else 0)