import json

_INSERT_EVENT = """
    insert into public.interaction_events
        (participant_id, video_id, event_type, source, device_type,
         condition_at_event, idempotency_key, payload,
         occurred_at, session_id, source_block)
    values (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s::jsonb,
            coalesce(%s::timestamptz, now()), %s::uuid, %s)
    on conflict (participant_id, idempotency_key) do nothing
    returning id
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
    occurred_at=None,
    session_id=None,
    source_block=None,
):
    inserted = conn.execute(
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
            occurred_at,
            session_id,
            source_block,
        ),
    ).fetchone()
    return inserted is not None