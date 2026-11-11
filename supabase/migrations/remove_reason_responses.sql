drop table public.reason_responses;

drop function public.reason_responses_block_update();

alter table public.interaction_events
  drop constraint interaction_events_event_type_check;

alter table public.interaction_events
  add constraint interaction_events_event_type_check
  check (event_type in (
    'CONSENT_ACCEPTED', 'CONSENT_DECLINED',
    'SURVEY_STARTED', 'SURVEY_SUBMITTED',
    'SAVED', 'SAVE_REJECTED',
    'PROCESSING_COMPLETED', 'PROCESSING_FAILED',
    'DASHBOARD_VIEWED',
    'RECENT_BLOCK_IMPRESSION', 'FORGOTTEN_BLOCK_IMPRESSION',
    'CATEGORY_TREE_IMPRESSION',
    'CATEGORY_EXPANDED', 'CATEGORY_COLLAPSED', 'CATEGORY_SELECTED',
    'TOPIC_REVIEW_CLICKED',
    'OPENED', 'EXTERNAL_OPEN', 'EMBED_FAILED',
    'DELETED', 'WITHDRAWAL_REQUESTED'
  ));