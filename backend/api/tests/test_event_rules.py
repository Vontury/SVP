from datetime import datetime, timedelta, timezone

import pytest

from app.event_rules import CLIENT_EVENT_TYPES, EventRejected, resolve_occurred_at, validate_event

CATEGORY = "44444444-4444-4444-4444-444444444444"
VIDEO = "33333333-3333-3333-3333-333333333333"
REASON = {"reason_context": "SAVE_REASON", "prompt_version": "p1", "codebook_version": "c1"}
NOW = datetime(2026, 10, 16, 6, 0, tzinfo=timezone.utc)

SERVER_ONLY = [
    "SAVED", "SAVE_REJECTED", "PROCESSING_COMPLETED", "PROCESSING_FAILED",
    "OPENED", "EXTERNAL_OPEN", "EMBED_FAILED", "DELETED",
    "CONSENT_ACCEPTED", "CONSENT_DECLINED", "SURVEY_STARTED", "SURVEY_SUBMITTED",
    "WITHDRAWAL_REQUESTED", "REASON_RESPONSE_SUBMITTED", "NOT_A_REAL_EVENT",
]


def code_of(*args):
    with pytest.raises(EventRejected) as exc:
        validate_event(*args)
    return exc.value.code


def test_client_types_are_exactly_the_approved_list():
    assert len(CLIENT_EVENT_TYPES) == 12


@pytest.mark.parametrize("event_type", SERVER_ONLY)
def test_server_only_types_are_rejected(event_type):
    assert code_of(event_type, None, {}, "FULL") == "EVENT_TYPE_NOT_ALLOWED"


@pytest.mark.parametrize(
    "event_type",
    [
        "DASHBOARD_VIEWED",
        "RECENT_BLOCK_IMPRESSION",
        "FORGOTTEN_BLOCK_IMPRESSION",
        "CATEGORY_TREE_IMPRESSION",
    ],
)
def test_events_without_payload_accept_only_an_empty_payload(event_type):
    assert validate_event(event_type, None, {}, "FULL") == {}
    assert code_of(event_type, None, {"x": 1}, "FULL") == "INVALID_PAYLOAD"


@pytest.mark.parametrize(
    "event_type",
    ["CATEGORY_EXPANDED", "CATEGORY_COLLAPSED", "CATEGORY_SELECTED", "TOPIC_REVIEW_CLICKED"],
)
def test_category_events_need_a_valid_category_id_and_nothing_else(event_type):
    assert validate_event(event_type, None, {"category_id": CATEGORY}, "FULL") == {
        "category_id": CATEGORY
    }
    assert code_of(event_type, None, {}, "FULL") == "INVALID_PAYLOAD"
    assert code_of(event_type, None, {"category_id": "nope"}, "FULL") == "INVALID_PAYLOAD"
    assert code_of(event_type, None, {"category_id": CATEGORY, "x": 1}, "FULL") == "INVALID_PAYLOAD"


@pytest.mark.parametrize(
    "event_type",
    [
        "REASON_BUTTON_DISPLAYED",
        "REASON_BUTTON_CLICKED",
        "REASON_PROMPT_OPENED",
        "REASON_PROMPT_DISMISSED",
    ],
)
def test_reason_events_are_allowed_in_full_with_an_item(event_type):
    assert validate_event(event_type, VIDEO, dict(REASON), "FULL") == REASON


def test_reason_events_are_rejected_in_basic():
    assert code_of("REASON_BUTTON_CLICKED", VIDEO, dict(REASON), "BASIC") == "NOT_ALLOWED_IN_CONDITION"


def test_reason_events_need_an_item():
    assert code_of("REASON_BUTTON_CLICKED", None, dict(REASON), "FULL") == "VIDEO_REQUIRED"


def test_reason_payload_is_strict():
    assert code_of("REASON_BUTTON_CLICKED", VIDEO, {}, "FULL") == "INVALID_PAYLOAD"
    assert code_of("REASON_BUTTON_CLICKED", VIDEO, {**REASON, "note": "free text"}, "FULL") == "INVALID_PAYLOAD"
    assert code_of("REASON_BUTTON_CLICKED", VIDEO, {**REASON, "reason_context": "OTHER"}, "FULL") == "INVALID_PAYLOAD"
    assert code_of("REASON_BUTTON_CLICKED", VIDEO, {**REASON, "prompt_version": "a b"}, "FULL") == "INVALID_PAYLOAD"
    assert code_of("REASON_BUTTON_CLICKED", VIDEO, {**REASON, "codebook_version": 5}, "FULL") == "INVALID_PAYLOAD"


def test_timestamp_inside_the_window_is_kept():
    value = NOW - timedelta(hours=23)
    assert resolve_occurred_at(value, NOW) == value
    assert resolve_occurred_at(NOW + timedelta(minutes=4), NOW) is not None


def test_timestamp_outside_the_window_is_dropped():
    assert resolve_occurred_at(NOW + timedelta(minutes=6), NOW) is None
    assert resolve_occurred_at(NOW - timedelta(hours=25), NOW) is None


def test_missing_or_naive_timestamp_is_dropped():
    assert resolve_occurred_at(None, NOW) is None
    assert resolve_occurred_at(datetime(2026, 10, 16, 5, 59), NOW) is None