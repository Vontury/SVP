import contextlib
import json
from datetime import datetime, timezone

import pytest

from app import db, worker
from app.metadata import Metadata, MetadataError
from tests.conftest import SAMPLE_PARTICIPANT

JOB_ID = "55555555-5555-5555-5555-555555555555"
OTHER_JOB_ID = "66666666-6666-6666-6666-666666666666"
PID = SAMPLE_PARTICIPANT["id"]
ATTEMPT_AT = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)
EVENT_KEY = f"processing_final:{JOB_ID}:{int(ATTEMPT_AT.timestamp() * 1000)}"
EVENT_INSERT = "insert into public.interaction_events"
RETRY_UPDATE = "make_interval(secs => %s)"
FAILED_UPDATE = "processing_status = 'FAILED', processing_error_code = %s"


def job(retry_count=1):
    return {
        "id": JOB_ID,
        "participant_id": PID,
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "platform": "YOUTUBE",
        "retry_count": retry_count,
        "last_attempt_at": ATTEMPT_AT,
    }


def sql_calls(fake_db, fragment):
    return [c for c in fake_db.calls if fragment in c[0]]


@pytest.fixture
def worker_db(fake_db, monkeypatch):
    @contextlib.contextmanager
    def connection():
        yield fake_db

    monkeypatch.setattr(db, "connection", connection)
    return fake_db


def unavailable(url, platform):
    raise MetadataError("METADATA_UNAVAILABLE")


def crash(url, platform):
    raise ValueError("internal detail that must not be stored")


@pytest.mark.parametrize("attempts, seconds", [(0, 60), (1, 60), (2, 300), (3, 300), (9, 300)])
def test_backoff_grows_then_levels_off(attempts, seconds):
    assert worker.backoff_seconds(attempts) == seconds


def test_no_job_means_nothing_happens(worker_db):
    worker_db.queue = [None]
    assert worker.process_one() is False
    assert len(worker_db.calls) == 1


def test_complete_metadata_is_saved_and_logged(worker_db):
    worker_db.queue = [job(1), {"id": JOB_ID}, {"condition": "BASIC"}]
    fetched = Metadata("Title", None, ("fun",), "https://i.ytimg.com/t.jpg")
    assert worker.process_one(lambda url, platform: fetched) is True

    (save,) = sql_calls(worker_db, "set title = %s")
    assert save[1] == ("Title", None, ["fun"], "https://i.ytimg.com/t.jpg", "COMPLETED", JOB_ID)

    (event,) = sql_calls(worker_db, EVENT_INSERT)
    params = event[1]
    assert params[0] == PID
    assert params[1] == JOB_ID
    assert params[2] == "PROCESSING_COMPLETED"
    assert params[5] == "BASIC"
    assert params[6] == EVENT_KEY
    assert json.loads(params[7]) == {"status": "COMPLETED"}


def test_missing_thumbnail_gives_partial(worker_db):
    worker_db.queue = [job(1), {"id": JOB_ID}, {"condition": "BASIC"}]
    worker.process_one(lambda url, platform: Metadata(title="Title"))
    (save,) = sql_calls(worker_db, "set title = %s")
    assert save[1][4] == "PARTIAL"
    (event,) = sql_calls(worker_db, EVENT_INSERT)
    assert json.loads(event[1][7]) == {"status": "PARTIAL"}


@pytest.mark.parametrize("attempts_made, wait", [(1, 60), (2, 300)])
def test_failure_with_attempts_left_schedules_a_retry(worker_db, attempts_made, wait):
    worker_db.queue = [job(attempts_made)]
    worker.process_one(unavailable)
    (retry,) = sql_calls(worker_db, RETRY_UPDATE)
    assert retry[1] == ("METADATA_UNAVAILABLE", wait, JOB_ID)
    assert sql_calls(worker_db, FAILED_UPDATE) == []
    assert sql_calls(worker_db, EVENT_INSERT) == []


def test_last_failed_attempt_marks_the_item_failed_and_logs_it(worker_db):
    worker_db.queue = [job(3), {"id": JOB_ID}, {"condition": "FULL"}]
    worker.process_one(unavailable)

    (failed,) = sql_calls(worker_db, FAILED_UPDATE)
    assert failed[1] == ("METADATA_UNAVAILABLE", JOB_ID)
    assert sql_calls(worker_db, RETRY_UPDATE) == []

    (event,) = sql_calls(worker_db, EVENT_INSERT)
    params = event[1]
    assert params[2] == "PROCESSING_FAILED"
    assert params[5] == "FULL"
    assert json.loads(params[7]) == {"error_code": "METADATA_UNAVAILABLE"}


def test_unexpected_error_is_stored_as_a_generic_code(worker_db):
    worker_db.queue = [job(1)]
    worker.process_one(crash)
    (retry,) = sql_calls(worker_db, RETRY_UPDATE)
    assert retry[1][0] == "METADATA_FAILED"
    assert "internal detail" not in str(worker_db.calls)


def test_empty_metadata_counts_as_a_failure(worker_db):
    worker_db.queue = [job(1)]
    worker.process_one(lambda url, platform: Metadata())
    (retry,) = sql_calls(worker_db, RETRY_UPDATE)
    assert retry[1] == ("METADATA_FAILED", 60, JOB_ID)


def test_missing_condition_skips_the_event_but_keeps_the_result(worker_db):
    worker_db.queue = [job(3), {"id": JOB_ID}, None]
    worker.process_one(unavailable)
    assert len(sql_calls(worker_db, FAILED_UPDATE)) == 1
    assert sql_calls(worker_db, EVENT_INSERT) == []


def test_stuck_items_are_failed_and_logged(worker_db):
    worker_db.all_rows = [
        {"id": JOB_ID, "participant_id": PID, "last_attempt_at": ATTEMPT_AT},
        {"id": OTHER_JOB_ID, "participant_id": PID, "last_attempt_at": ATTEMPT_AT},
    ]
    worker_db.queue = [{"condition": "BASIC"}, {"condition": "BASIC"}]
    assert worker.reap_stuck() == 2
    events = sql_calls(worker_db, EVENT_INSERT)
    assert len(events) == 2
    for event in events:
        assert event[1][2] == "PROCESSING_FAILED"
        assert json.loads(event[1][7]) == {"error_code": "PROCESSING_TIMEOUT"}