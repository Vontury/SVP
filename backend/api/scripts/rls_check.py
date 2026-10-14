import json
import sys

import psycopg
from dotenv import dotenv_values
from psycopg.errors import InsufficientPrivilege
from psycopg.rows import dict_row

env = dotenv_values(".env")
missing = [k for k in ("DATABASE_URL", "TEST_A_EMAIL", "TEST_B_EMAIL") if not env.get(k)]
if missing:
    sys.exit("Missing in .env: " + ", ".join(missing))

READABLE = ["participants", "consent_records", "survey_submissions", "survey_answers"]
BACKEND_ONLY = [
    "videos", "ai_classification_runs", "categories", "category_label_mappings",
    "study_condition_history", "app_sessions", "interaction_events", "api_tokens",
]
WRITE_ATTEMPTS = [
    "insert into public.participants default values",
    "update public.participants set pilot_flag = pilot_flag",
    "delete from public.participants",
    "insert into public.consent_records default values",
    "update public.consent_records set consent_version = consent_version",
    "delete from public.consent_records",
    "insert into public.survey_submissions default values",
    "delete from public.survey_submissions",
    "insert into public.survey_answers default values",
    "delete from public.survey_answers",
]
NO_USER = "00000000-0000-0000-0000-000000000000"

failures = 0
conn = psycopg.connect(env["DATABASE_URL"], autocommit=True, row_factory=dict_row)


def check(name, ok, detail=""):
    global failures
    suffix = f"  [{detail}]" if detail != "" and not ok else ""
    print(("PASS  " if ok else "FAIL  ") + name + suffix)
    if not ok:
        failures += 1


def attempt(role, sub, sql):

    try:
        with conn.transaction(force_rollback=True):
            conn.execute(f"set local role {role}")
            if sub:
                claims = json.dumps({"sub": sub, "role": role})
                conn.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
            cur = conn.execute(sql)
            return "ok", (cur.fetchall() if cur.description else [])
    except InsufficientPrivilege:
        return "denied", None
    except psycopg.Error as exc:
        return "error", exc.sqlstate


def lookup(email):
    user = conn.execute("select id from auth.users where email = %s", (email,)).fetchone()
    if user is None:
        sys.exit(f"No Supabase user {email}. Create it in the dashboard first.")
    part = conn.execute(
        "select id from public.participants where auth_user_id = %s", (user["id"],)
    ).fetchone()
    if part is None:
        sys.exit(f"{email} has no participant. Run scripts/e2e_two_participants.py first.")
    return str(user["id"]), str(part["id"])

for role in ("authenticated", "anon"):
    status, rows = attempt(role, None, "select current_user as u")
    if status != "ok" or rows[0]["u"] != role:
        sys.exit(f"Cannot switch to the {role} role from this connection ({status}).")

users = {"A": lookup(env["TEST_A_EMAIL"]), "B": lookup(env["TEST_B_EMAIL"])}

for label, (sub, pid) in users.items():
    status, rows = attempt("authenticated", sub, "select id from public.participants")
    check(f"{label}: sees only its own participant row",
          status == "ok" and [str(r["id"]) for r in rows] == [pid], status)

    status, rows = attempt("authenticated", sub, "select participant_id from public.consent_records")
    check(f"{label}: sees only its own consent record",
          status == "ok" and [str(r["participant_id"]) for r in rows] == [pid], status)

    results = [
        attempt("authenticated", sub, f"select count(*) from public.{t}")[0]
        for t in ("survey_submissions", "survey_answers")
    ]
    check(f"{label}: can query the survey tables (empty for now)", results == ["ok", "ok"], results)

    open_tables = [
        t for t in BACKEND_ONLY
        if attempt("authenticated", sub, f"select 1 from public.{t} limit 1")[0] != "denied"
    ]
    check(f"{label}: denied on all {len(BACKEND_ONLY)} backend-only tables", not open_tables, open_tables)

    allowed = [
        sql for sql in WRITE_ATTEMPTS
        if attempt("authenticated", sub, sql)[0] != "denied"
    ]
    check(f"{label}: all {len(WRITE_ATTEMPTS)} write attempts denied", not allowed, allowed)

status, rows = attempt("authenticated", NO_USER, "select id from public.participants")
check("a login with no participant sees nothing", status == "ok" and rows == [], status)

all_tables = READABLE + BACKEND_ONLY
open_tables = [
    t for t in all_tables
    if attempt("anon", None, f"select 1 from public.{t} limit 1")[0] != "denied"
]
check(f"anon: denied on all {len(all_tables)} tables", not open_tables, open_tables)

print()
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
sys.exit(1 if failures else 0)