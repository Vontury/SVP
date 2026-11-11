import uuid
from datetime import timedelta

CATEGORY_EVENT_TYPES = frozenset({
    "CATEGORY_EXPANDED",
    "CATEGORY_COLLAPSED",
    "CATEGORY_SELECTED",
    "TOPIC_REVIEW_CLICKED",
})

VIDEO_EVENT_TYPES = frozenset({"EMBED_FAILED"})

CLIENT_EVENT_TYPES = frozenset({
    "DASHBOARD_VIEWED",
    "RECENT_BLOCK_IMPRESSION",
    "FORGOTTEN_BLOCK_IMPRESSION",
    "CATEGORY_TREE_IMPRESSION",
}) | CATEGORY_EVENT_TYPES | VIDEO_EVENT_TYPES

FUTURE_TOLERANCE = timedelta(minutes=5)
MAX_AGE = timedelta(hours=24)


class EventRejected(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def validate_payload(event_type, payload):
    if event_type in CATEGORY_EVENT_TYPES:
        if set(payload) != {"category_id"}:
            raise EventRejected("INVALID_PAYLOAD")
        try:
            return {"category_id": str(uuid.UUID(str(payload["category_id"])))}
        except ValueError:
            raise EventRejected("INVALID_PAYLOAD")

    if payload:
        raise EventRejected("INVALID_PAYLOAD")
    return {}


def validate_event(event_type, video_id, payload, condition):
    if event_type not in CLIENT_EVENT_TYPES:
        raise EventRejected("EVENT_TYPE_NOT_ALLOWED")
    if event_type in VIDEO_EVENT_TYPES and video_id is None:
        raise EventRejected("VIDEO_REQUIRED")
    return validate_payload(event_type, payload)


def resolve_occurred_at(value, now):
    if value is None or value.tzinfo is None:
        return None
    if value > now + FUTURE_TOLERANCE or value < now - MAX_AGE:
        return None
    return value