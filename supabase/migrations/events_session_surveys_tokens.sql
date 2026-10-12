create table public.app_sessions (
  id                uuid primary key default gen_random_uuid(),
  participant_id    uuid not null references public.participants (id) on delete restrict,
  started_at        timestamptz not null default now(),
  last_activity_at  timestamptz not null default now(),
  ended_at          timestamptz,
  end_reason        text,
  condition_at_start text not null check (condition_at_start in ('BASIC', 'FULL')),
  created_at        timestamptz not null default now(),
  constraint app_sessions_id_participant_uk unique (id, participant_id),
  constraint app_sessions_activity_order check (last_activity_at >= started_at),
  constraint app_sessions_end_order check (ended_at is null or ended_at >= started_at)
);

create index app_sessions_participant_started_idx
  on public.app_sessions (participant_id, started_at);

alter table public.app_sessions enable row level security;

create table public.interaction_events (
  id                 uuid primary key default gen_random_uuid(),
  participant_id     uuid not null references public.participants (id) on delete restrict,
  video_id           uuid,
  session_id         uuid,
  event_type         text not null check (event_type in (
                       'CONSENT_ACCEPTED', 'CONSENT_DECLINED',
                       'SURVEY_STARTED', 'SURVEY_SUBMITTED',
                       'SAVED', 'SAVE_REJECTED',
                       'PROCESSING_COMPLETED', 'PROCESSING_FAILED',
                       'DASHBOARD_VIEWED',
                       'RECENT_BLOCK_IMPRESSION', 'FORGOTTEN_BLOCK_IMPRESSION',
                       'CATEGORY_TREE_IMPRESSION',
                       'CATEGORY_EXPANDED', 'CATEGORY_COLLAPSED', 'CATEGORY_SELECTED',
                       'OPENED', 'EXTERNAL_OPEN', 'EMBED_FAILED',
                       'DELETED', 'WITHDRAWAL_REQUESTED'
                     )),
  occurred_at        timestamptz not null,
  received_at        timestamptz not null default now(),
  source             text,
  source_block       text,
  device_type        text,
  condition_at_event text not null check (condition_at_event in ('BASIC', 'FULL')),
  idempotency_key    text not null,
  payload            jsonb not null default '{}'::jsonb
                     check (jsonb_typeof(payload) = 'object'
                            and octet_length(payload::text) <= 2048),
  created_at         timestamptz not null default now(),
  constraint interaction_events_idempotency_uk
    unique (participant_id, idempotency_key),

  constraint interaction_events_video_fk
    foreign key (video_id, participant_id)
    references public.videos (id, participant_id),
  constraint interaction_events_session_fk
    foreign key (session_id, participant_id)
    references public.app_sessions (id, participant_id)
);

create index interaction_events_participant_time_idx
  on public.interaction_events (participant_id, occurred_at);

create index interaction_events_participant_type_time_idx
  on public.interaction_events (participant_id, event_type, occurred_at);

create index interaction_events_video_opens_idx
  on public.interaction_events (video_id, occurred_at)
  where event_type = 'OPENED';

create or replace function public.interaction_events_block_update()
returns trigger
language plpgsql
as $$
begin
  raise exception 'interaction_events rows are immutable';
end;
$$;

create trigger interaction_events_block_update
  before update on public.interaction_events
  for each row execute function public.interaction_events_block_update();

alter table public.interaction_events enable row level security;

create table public.survey_submissions (
  id             uuid primary key default gen_random_uuid(),
  participant_id uuid not null references public.participants (id) on delete restrict,
  survey_type    text not null check (survey_type in ('BASELINE', 'WEEK4', 'POST')),
  scale_version  text not null,
  status         text not null default 'IN_PROGRESS'
                 check (status in ('IN_PROGRESS', 'SUBMITTED')),
  started_at     timestamptz not null default now(),
  submitted_at   timestamptz,
  created_at     timestamptz not null default now(),
  constraint survey_submitted_consistency
    check ((status = 'SUBMITTED') = (submitted_at is not null))
);

create unique index survey_submissions_one_submitted_idx
  on public.survey_submissions (participant_id, survey_type)
  where status = 'SUBMITTED';

create index survey_submissions_participant_idx
  on public.survey_submissions (participant_id, survey_type);

alter table public.survey_submissions enable row level security;

create table public.survey_answers (
  id            uuid primary key default gen_random_uuid(),
  submission_id uuid not null references public.survey_submissions (id) on delete restrict,
  question_id   text not null,
  raw_answer    jsonb not null,
  answered_at   timestamptz not null default now(),
  constraint survey_answers_one_per_question unique (submission_id, question_id)
);

alter table public.survey_answers enable row level security;

create table public.api_tokens (
  id             uuid primary key default gen_random_uuid(),
  participant_id uuid not null references public.participants (id) on delete restrict,
  token_hash     text not null unique,
  scope          text not null default 'SAVE_URL' check (scope = 'SAVE_URL'),
  device_label   text,
  created_at     timestamptz not null default now(),
  last_used_at   timestamptz,
  revoked_at     timestamptz
);

create index api_tokens_participant_idx on public.api_tokens (participant_id);

alter table public.api_tokens enable row level security;