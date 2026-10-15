import sys
import time

import httpx
import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

env = dotenv_values(".env")
REQUIRED = [
    "SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "DATABASE_URL",
    "TEST_A_EMAIL", "TEST_A_PASSWORD",
]
missing = [k for k in REQUIRED if not env.get(k)]
if missing:
    sys.exit("Missing in .env: " + ", ".join(missing))

API = f"http://localhost:{env.get('PORT') or '8083'}"
SUPABASE_URL = env["SUPABASE_URL"].rstrip("/")
TIMEOUT_SECONDS = 600
POLL_SECONDS = 10

LINKS = {
    "real": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "fake": "https://www.youtube.com/watch?v=e2eNoSuchV1",
}
if env.get("TEST_TIKTOK_URL"):
    LINKS["tiktok"] = env["TEST_TIKTOK_URL"]

POLL_SQL = """
    select id, processing_status, processing_error_code, retry_count, title,
           thumbnail_url, saved_at, last_attempt_at
    from public.videos
    where id = any(%s::uuid[])
"""

EVENTS_SQL = """
    select e.event_type, e.payload, e.condition_at_event, h.condition as expected
    from public.interaction_events e
    join public.study_condition_history h
      on h.participant_id = e.participant_id
     and h.valid_from <= e.occurred_at
     and (h.valid_to is null or h.valid_to > e.occurred_at)
    where e.video_id = %s::uuid
      and e.event_type in ('PROCESSING_COMPLETED', 'PROCESSING_FAILED')
"""

failures = 0
conn = psycopg.connect(env["DATABASE_URL"], autocommit=True, row_factory=dict_row)


def check(name, ok, detail=""):
    global failures
    suffix = f"  [{detail}]" if detail != "" and not ok else ""
    print(("PASS  " if ok else "FAIL  ") + name + suffix)
    if not ok:
        failures += 1


def sign_in():
    r = httpx.post(
        f"{SUPABASE_URL}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": env["SUPABASE_PUBLISHABLE_KEY"]},
        json={"email": env["TEST_A_EMAIL"], "password": env["TEST_A_PASSWORD"]},
        timeout=20,
    )
    if r.status_code != 200:
        sys.exit(f"Login failed: HTTP {r.status_code} {r.text[:200]}")
    return r.json()["access_token"]


headers = {"Authorization": f"Bearer {sign_in()}"}

ids = {}
for label, link in LINKS.items():
    r = httpx.post(f"{API}/api/videos", headers=headers, json={"url": link}, timeout=60)
    check(f"{label}: link saved", r.status_code in (200, 201), f"{r.status_code} {r.text[:120]}")
    if r.status_code not in (200, 201):
        sys.exit("Stopping: the link was not saved.")
    ids[label] = r.json()["id"]

labels = {video_id: label for label, video_id in ids.items()}
started = time.time()
seen = {}
rows = {}
while True:
    found = conn.execute(POLL_SQL, (list(ids.values()),)).fetchall()
    rows = {str(r["id"]): r for r in found}
    for video_id, row in rows.items():
        state = (row["processing_status"], row["retry_count"], row["processing_error_code"])
        if seen.get(video_id) != state:
            seen[video_id] = state
            elapsed = int(time.time() - started)
            print(f"[{elapsed:3d}s] {labels[video_id]}: {state[0]}, attempts {state[1]}, error {state[2]}")
    finished = all(r["processing_status"] != "PROCESSING" for r in rows.values())
    if finished or time.time() - started > TIMEOUT_SECONDS:
        break
    time.sleep(POLL_SECONDS)

print()
check("every item finished within the time limit", finished and len(rows) == len(ids))
if not finished or len(rows) != len(ids):
    print()
    print(f"{failures} CHECK(S) FAILED")
    sys.exit(1)


def final(label):
    return rows[ids[label]]


real = final("real")
title = real["title"] or ""
print(f"INFO  real link ended {real['processing_status']}")
check("real link: ends COMPLETED or PARTIAL",
      real["processing_status"] in ("COMPLETED", "PARTIAL"), real["processing_status"])
check("real link: has a genuine title",
      bool(title) and not title.lower().startswith(("youtube video #", "tiktok video #")), title[:60])
check("real link: thumbnail is an https link", (real["thumbnail_url"] or "").startswith("https://"))
check("real link: no error code left over", real["processing_error_code"] is None)

fake = final("fake")
span = 0
if fake["last_attempt_at"] is not None:
    span = (fake["last_attempt_at"] - fake["saved_at"]).total_seconds()
check("fake link: ends FAILED", fake["processing_status"] == "FAILED", fake["processing_status"])
check("fake link: error code is METADATA_UNAVAILABLE",
      fake["processing_error_code"] == "METADATA_UNAVAILABLE", fake["processing_error_code"])
check("fake link: used all 3 attempts", fake["retry_count"] == 3, fake["retry_count"])
check("fake link: no title was stored", fake["title"] is None, fake["title"])
check("fake link: retries waited for the back-off", span >= 350, int(span))

if "tiktok" in ids:
    tiktok = final("tiktok")
    print(f"INFO  tiktok link ended {tiktok['processing_status']} (error {tiktok['processing_error_code']})")
    check("tiktok link: finished", tiktok["processing_status"] != "PROCESSING")

for label in ids:
    row = final(label)
    events = conn.execute(EVENTS_SQL, (ids[label],)).fetchall()
    check(f"{label}: exactly one final processing event", len(events) == 1, len(events))
    if len(events) != 1:
        continue
    event = events[0]
    if row["processing_status"] == "FAILED":
        expected_type = "PROCESSING_FAILED"
        expected_payload = {"error_code": row["processing_error_code"]}
    else:
        expected_type = "PROCESSING_COMPLETED"
        expected_payload = {"status": row["processing_status"]}
    check(f"{label}: event type and payload match the item",
          event["event_type"] == expected_type and event["payload"] == expected_payload, event)
    check(f"{label}: event carries the condition valid at that time",
          event["condition_at_event"] == event["expected"])

r = httpx.get(f"{API}/api/videos", headers=headers, params={"limit": 100}, timeout=30)
listed = {i["id"]: i for i in r.json().get("items", [])} if r.status_code == 200 else {}
for label, video_id in ids.items():
    item = listed.get(video_id)
    check(f"{label}: the API shows the final status",
          item is not None and item["processing_status"] == rows[video_id]["processing_status"])

print()
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
sys.exit(1 if failures else 0)