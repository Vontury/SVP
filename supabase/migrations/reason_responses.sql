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
    'DELETED', 'WITHDRAWAL_REQUESTED',
    'REASON_BUTTON_DISPLAYED', 'REASON_BUTTON_CLICKED',
    'REASON_PROMPT_OPENED', 'REASON_RESPONSE_SUBMITTED',
    'REASON_PROMPT_DISMISSED'
  ));

create table public.reason_responses (
  id                    uuid primary key default gen_random_uuid(),
  participant_id        uuid not null references public.participants (id) on delete restrict,
  video_id              uuid not null,
  reason_context        text not null
                        check (reason_context in ('SAVE_REASON', 'NEVER_OPENED_REASON', 'NOT_REVISITED_REASON')),
  primary_reason_code   text not null,
  secondary_reason_code text,
  prompt_version        text not null,
  codebook_version      text not null,
  condition_at_event    text not null check (condition_at_event = 'FULL'),
  response_status       text not null default 'SUBMITTED' check (response_status = 'SUBMITTED'),
  answered_at           timestamptz not null default now(),
  created_at            timestamptz not null default now(),
  constraint reason_responses_video_fk
    foreign key (video_id, participant_id)
    references public.videos (id, participant_id),
  constraint reason_responses_one_per_item_context
    unique (video_id, reason_context),
  constraint reason_responses_secondary_differs
    check (secondary_reason_code is null or secondary_reason_code <> primary_reason_code),
  constraint reason_responses_no_secondary_when_declined
    check (primary_reason_code not in ('NOT_SURE', 'PREFER_NOT_ANSWER') or secondary_reason_code is null)
);

create index reason_responses_participant_idx
  on public.reason_responses (participant_id, answered_at);

create or replace function public.reason_responses_block_update()
returns trigger
language plpgsql
as $$
begin
  raise exception 'reason_responses rows are immutable';
end;
$$;

create trigger reason_responses_block_update
  before update on public.reason_responses
  for each row execute function public.reason_responses_block_update();

alter table public.reason_responses enable row level security;