import uuid
from datetime import timedelta

CATEGORY_EVENT_TYPES = frozenset({
    "CATEGORY_EXPANDED",
    "CATEGORY_COLLAPSED",
    "CATEGORY_SELECTED",
    "TOPIC_REVIEW_CLICKED",
})

REASON_EVENT_TYPES = frozenset({
    "REASON_BUTTON_DISPLAYED",
    "REASON_BUTTON_CLICKED",
    "REASON_PROMPT_OPENED",
    "REASON_PROMPT_DISMISSED",
})

CLIENT_EVENT_TYPES = frozenset({
    "DASHBOARD_VIEWED",
    "RECENT_BLOCK_IMPRESSION",
    "FORGOTTEN_BLOCK_IMPRESSION",
    "CATEGORY_TREE_IMPRESSION",
}) | CATEGORY_EVENT_TYPES | REASON_EVENT_TYPES

REASON_CONTEXTS = frozenset({"SAVE_REASON", "NEVER_OPENED_REASON", "NOT_REVISITED_REASON"})
REASON_KEYS = frozenset({"reason_context", "prompt_version", "codebook_version"})

FUTURE_TOLERANCE = timedelta(minutes=5)
MAX_AGE = timedelta(hours=24)

_VERSION_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


class EventRejected(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _is_version(value):
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 64
        and all(c in _VERSION_CHARS for c in value)
    )


def validate_payload(event_type, payload):
    if event_type in CATEGORY_EVENT_TYPES:
        if set(payload) != {"category_id"}:
            raise EventRejected("INVALID_PAYLOAD")
        try:
            return {"category_id": str(uuid.UUID(str(payload["category_id"])))}
        except ValueError:
            raise EventRejected("INVALID_PAYLOAD")

    if event_type in REASON_EVENT_TYPES:
        if set(payload) != REASON_KEYS:
            raise EventRejected("INVALID_PAYLOAD")
        if payload["reason_context"] not in REASON_CONTEXTS:
            raise EventRejected("INVALID_PAYLOAD")
        if not _is_version(payload["prompt_version"]) or not _is_version(payload["codebook_version"]):
            raise EventRejected("INVALID_PAYLOAD")
        return dict(payload)

    if payload:
        raise EventRejected("INVALID_PAYLOAD")
    return {}


def validate_event(event_type, video_id, payload, condition):
    if event_type not in CLIENT_EVENT_TYPES:
        raise EventRejected("EVENT_TYPE_NOT_ALLOWED")
    if event_type in REASON_EVENT_TYPES:
        if condition != "FULL":
            raise EventRejected("NOT_ALLOWED_IN_CONDITION")
        if video_id is None:
            raise EventRejected("VIDEO_REQUIRED")
    return validate_payload(event_type, payload)


def resolve_occurred_at(value, now):
    if value is None or value.tzinfo is None:
        return None
    if value > now + FUTURE_TOLERANCE or value < now - MAX_AGE:
        return None
    return value