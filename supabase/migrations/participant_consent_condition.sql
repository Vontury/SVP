create extension if not exists btree_gist with schema extensions;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create table public.participants (
  id            uuid primary key default gen_random_uuid(),
  auth_user_id  uuid not null unique references auth.users (id) on delete restrict,
  study_code    text not null unique,
  status        text not null default 'ACTIVE'
                check (status in ('ACTIVE', 'WITHDRAWN', 'COMPLETED')),
  pilot_flag    boolean not null default false,
  primary_device text
                check (primary_device in ('ANDROID', 'IOS', 'DESKTOP', 'OTHER')),
  region_code   text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create trigger participants_set_updated_at
  before update on public.participants
  for each row execute function public.set_updated_at();

alter table public.participants enable row level security;

create table public.consent_records (
  id                     uuid primary key default gen_random_uuid(),
  participant_id         uuid not null references public.participants (id) on delete restrict,
  consent_version        text not null,
  decision               text not null default 'ACCEPTED'
                         check (decision in ('ACCEPTED', 'DECLINED')),
  decided_at             timestamptz not null default now(),
  withdrawn_at           timestamptz,
  protocol_action_status text not null default 'NONE'
                         check (protocol_action_status in ('NONE', 'PENDING', 'COMPLETED')),
  created_at             timestamptz not null default now(),
  constraint consent_withdrawn_after_decided
    check (withdrawn_at is null or withdrawn_at >= decided_at)
);

create index consent_records_participant_idx
  on public.consent_records (participant_id);

alter table public.consent_records enable row level security;

create table public.study_condition_history (
  id             uuid primary key default gen_random_uuid(),
  participant_id uuid not null references public.participants (id) on delete restrict,
  condition      text not null check (condition in ('BASIC', 'FULL')),
  phase_id       text not null,
  valid_from     timestamptz not null,
  valid_to       timestamptz,
  created_at     timestamptz not null default now(),
  constraint condition_valid_range
    check (valid_to is null or valid_to > valid_from),
  constraint condition_no_overlap
    exclude using gist (
      participant_id with =,
      tstzrange(valid_from, valid_to, '[)') with &&
    )
);

create index study_condition_history_lookup_idx
  on public.study_condition_history (participant_id, valid_from);

alter table public.study_condition_history enable row level security;