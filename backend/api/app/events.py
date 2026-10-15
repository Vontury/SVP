import json

_INSERT_EVENT = """
    insert into public.interaction_events
        (participant_id, video_id, event_type, occurred_at, source, device_type,
         condition_at_event, idempotency_key, payload)
    values (%s::uuid, %s::uuid, %s, now(), %s, %s, %s, %s, %s::jsonb)
    on conflict (participant_id, idempotency_key) do nothing
"""


def record_event(
    conn,
    *,
    participant_id,
    condition,
    event_type,
    idempotency_key,
    video_id=None,
    source=None,
    device_type=None,
    payload=None,
):
    conn.execute(
        _INSERT_EVENT,
        (
            participant_id,
            video_id,
            event_type,
            source,
            device_type,
            condition,
            idempotency_key,
            json.dumps(payload or {}, sort_keys=True),
        ),
    )