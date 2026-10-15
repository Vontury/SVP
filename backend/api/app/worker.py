import asyncio
import logging

from app import db
from app.events import record_event
from app.metadata import MetadataError, decide_outcome, fetch_metadata

logger = logging.getLogger("app.worker")

MAX_ATTEMPTS = 3
LEASE_MINUTES = 5
POLL_SECONDS = 5
BACKOFF_SECONDS = (60, 300)

_CLAIM = """
    update public.videos v
    set retry_count = v.retry_count + 1,
        last_attempt_at = now(),
        next_retry_at = now() + make_interval(mins => %s)
    from (
        select id from public.videos
        where processing_status = 'PROCESSING'
          and deleted_at is null
          and retry_count < %s
          and (next_retry_at is null or next_retry_at <= now())
        order by saved_at
        for update skip locked
        limit 1
    ) due
    where v.id = due.id
    returning v.id, v.participant_id, v.url, v.platform, v.retry_count, v.last_attempt_at
"""

_REAP = """
    update public.videos
    set processing_status = 'FAILED',
        processing_error_code = 'PROCESSING_TIMEOUT',
        next_retry_at = null
    where processing_status = 'PROCESSING'
      and deleted_at is null
      and retry_count >= %s
      and next_retry_at <= now()
    returning id, participant_id, last_attempt_at
"""

_SAVE_METADATA = """
    update public.videos
    set title = %s, description = %s, hashtags = %s::text[], thumbnail_url = %s,
        processing_status = %s, processing_error_code = null, next_retry_at = null
    where id = %s::uuid and processing_status = 'PROCESSING'
    returning id
"""

_SCHEDULE_RETRY = """
    update public.videos
    set processing_error_code = %s,
        next_retry_at = now() + make_interval(secs => %s)
    where id = %s::uuid and processing_status = 'PROCESSING'
"""

_MARK_FAILED = """
    update public.videos
    set processing_status = 'FAILED', processing_error_code = %s, next_retry_at = null
    where id = %s::uuid and processing_status = 'PROCESSING'
    returning id
"""

_LATEST_CONDITION = """
    select condition from public.study_condition_history
    where participant_id = %s::uuid and valid_from <= now()
    order by valid_from desc
    limit 1
"""


def backoff_seconds(attempts_made):
    index = min(max(attempts_made, 1), len(BACKOFF_SECONDS)) - 1
    return BACKOFF_SECONDS[index]


def _record_final_event(conn, job, event_type, payload):
    condition = conn.execute(_LATEST_CONDITION, (job["participant_id"],)).fetchone()
    if condition is None:
        logger.warning("no study condition for video %s; event skipped", job["id"])
        return
    attempt_ms = int(job["last_attempt_at"].timestamp() * 1000)
    record_event(
        conn,
        participant_id=job["participant_id"],
        condition=condition["condition"],
        event_type=event_type,
        idempotency_key=f"processing_final:{job['id']}:{attempt_ms}",
        video_id=job["id"],
        payload=payload,
    )


def process_one(fetch=fetch_metadata):
    with db.connection() as conn:
        job = conn.execute(_CLAIM, (LEASE_MINUTES, MAX_ATTEMPTS)).fetchone()
    if job is None:
        return False

    metadata = None
    error_code = "METADATA_FAILED"
    try:
        metadata = fetch(job["url"], job["platform"])
    except MetadataError as exc:
        error_code = exc.code
    except Exception as exc:
        logger.warning("metadata fetch crashed for video %s: %s", job["id"], type(exc).__name__)

    outcome = decide_outcome(metadata)
    with db.connection() as conn:
        if outcome != "FAILED":
            saved = conn.execute(
                _SAVE_METADATA,
                (
                    metadata.title,
                    metadata.description,
                    list(metadata.hashtags),
                    metadata.thumbnail_url,
                    outcome,
                    job["id"],
                ),
            ).fetchone()
            if saved is not None:
                _record_final_event(conn, job, "PROCESSING_COMPLETED", {"status": outcome})
        elif job["retry_count"] >= MAX_ATTEMPTS:
            failed = conn.execute(_MARK_FAILED, (error_code, job["id"])).fetchone()
            if failed is not None:
                _record_final_event(conn, job, "PROCESSING_FAILED", {"error_code": error_code})
        else:
            conn.execute(
                _SCHEDULE_RETRY,
                (error_code, backoff_seconds(job["retry_count"]), job["id"]),
            )
    return True


def reap_stuck():
    with db.connection() as conn:
        rows = conn.execute(_REAP, (MAX_ATTEMPTS,)).fetchall()
        for row in rows:
            _record_final_event(conn, row, "PROCESSING_FAILED", {"error_code": "PROCESSING_TIMEOUT"})
    return len(rows)


async def run_forever():
    while True:
        try:
            await asyncio.to_thread(reap_stuck)
            while await asyncio.to_thread(process_one):
                pass
        except Exception as exc:
            logger.warning("worker loop error: %s", type(exc).__name__)
        await asyncio.sleep(POLL_SECONDS)