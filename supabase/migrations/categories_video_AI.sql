create table public.categories (
  id               uuid primary key default gen_random_uuid(),
  name             text not null,
  parent_id        uuid,
  level            smallint not null check (level in (1, 2, 3)),
  taxonomy_version text not null,
  parent_level     smallint generated always as (level - 1) stored,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  constraint categories_id_level_uk unique (id, level),
  constraint categories_parent_fk
    foreign key (parent_id, parent_level)
    references public.categories (id, level),
  constraint categories_parent_rule
    check ((level = 1 and parent_id is null) or (level > 1 and parent_id is not null)),
  constraint categories_unique_name_per_parent
    unique nulls not distinct (taxonomy_version, parent_id, name)
);

create index categories_parent_idx on public.categories (parent_id);

create trigger categories_set_updated_at
  before update on public.categories
  for each row execute function public.set_updated_at();

alter table public.categories enable row level security;

create table public.videos (
  id                    uuid primary key default gen_random_uuid(),
  participant_id        uuid not null references public.participants (id) on delete restrict,
  url                   text not null,
  url_normalized        text not null,
  platform              text not null check (platform in ('TIKTOK', 'YOUTUBE')),
  title                 text,
  description           text,
  hashtags              text[] not null default '{}',
  thumbnail_url         text,
  summary               text,
  category_id           uuid references public.categories (id),
  processing_status     text not null default 'PROCESSING'
                        check (processing_status in ('PROCESSING', 'COMPLETED', 'PARTIAL', 'FAILED')),
  processing_error_code text,
  retry_count           integer not null default 0 check (retry_count >= 0),
  last_attempt_at       timestamptz,
  next_retry_at         timestamptz,
  saved_at              timestamptz not null default now(),
  save_source           text not null
                        check (save_source in ('PASTE', 'SHARE_SHEET', 'IOS_SHORTCUT', 'CHROME_EXTENSION')),
  device_type           text,
  first_opened_at       timestamptz,
  last_opened_at        timestamptz,
  open_count            integer not null default 0 check (open_count >= 0),
  deleted_at            timestamptz,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  constraint videos_id_participant_uk unique (id, participant_id),
  constraint videos_open_order
    check (first_opened_at is null or last_opened_at is null or first_opened_at <= last_opened_at)
);

create index videos_participant_saved_idx
  on public.videos (participant_id, saved_at desc)
  where deleted_at is null;

create index videos_participant_category_idx
  on public.videos (participant_id, category_id);

create index videos_participant_url_idx
  on public.videos (participant_id, url_normalized);

create index videos_processing_retry_idx
  on public.videos (next_retry_at)
  where processing_status = 'PROCESSING';

create trigger videos_set_updated_at
  before update on public.videos
  for each row execute function public.set_updated_at();

alter table public.videos enable row level security;

create table public.ai_classification_runs (
  id                      uuid primary key default gen_random_uuid(),
  video_id                uuid not null references public.videos (id) on delete restrict,
  raw_level1              text,
  raw_level2              text,
  raw_level3              text,
  canonical_category_id   uuid references public.categories (id),
  summary                 text,
  model_name              text not null,
  model_version           text,
  prompt_version          text not null,
  taxonomy_version        text not null,
  mapping_version         text,
  classification_status   text not null
                          check (classification_status in ('SUCCESS', 'PARTIAL', 'ABSTAIN', 'FAILED')),
  created_at              timestamptz not null default now()
);

create index ai_runs_video_idx
  on public.ai_classification_runs (video_id, created_at desc);

create or replace function public.ai_runs_guard_immutable()
returns trigger
language plpgsql
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'ai_classification_runs rows cannot be deleted';
  end if;

  if row(new.video_id, new.raw_level1, new.raw_level2, new.raw_level3, new.summary,
         new.model_name, new.model_version, new.prompt_version, new.taxonomy_version,
         new.created_at)
     is distinct from
     row(old.video_id, old.raw_level1, old.raw_level2, old.raw_level3, old.summary,
         old.model_name, old.model_version, old.prompt_version, old.taxonomy_version,
         old.created_at)
  then
    raise exception 'raw AI labels and version fields are immutable';
  end if;

  return new;
end;
$$;

create trigger ai_runs_guard_immutable
  before update or delete on public.ai_classification_runs
  for each row execute function public.ai_runs_guard_immutable();

alter table public.ai_classification_runs enable row level security;

create table public.category_label_mappings (
  id                    uuid primary key default gen_random_uuid(),
  raw_label             text not null,
  level                 smallint not null check (level in (1, 2, 3)),
  canonical_category_id uuid not null,
  parent_context_id     uuid references public.categories (id),
  mapping_version       text not null,
  review_status         text not null default 'SUGGESTED'
                        check (review_status in ('SUGGESTED', 'APPROVED', 'REJECTED')),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  constraint mappings_canonical_level_fk
    foreign key (canonical_category_id, level)
    references public.categories (id, level),
  constraint mappings_unique
    unique nulls not distinct (raw_label, level, parent_context_id, mapping_version)
);

create index category_label_mappings_lookup_idx
  on public.category_label_mappings (level, raw_label)
  where review_status = 'APPROVED';

create trigger category_label_mappings_set_updated_at
  before update on public.category_label_mappings
  for each row execute function public.set_updated_at();

alter table public.category_label_mappings enable row level security;