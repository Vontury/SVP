revoke all on all tables in schema public from anon, authenticated;

alter default privileges in schema public
  revoke all on tables from anon, authenticated;

create or replace function public.current_participant_id()
returns uuid
language sql
stable
security definer
set search_path = ''
as $$
  select p.id
  from public.participants p
  where p.auth_user_id = (select auth.uid());
$$;

revoke all on function public.current_participant_id() from public, anon;
grant execute on function public.current_participant_id() to authenticated;

grant select on
  public.participants,
  public.consent_records,
  public.survey_submissions,
  public.survey_answers
to authenticated;

create policy participants_select_own
  on public.participants
  for select to authenticated
  using (auth_user_id = (select auth.uid()));

create policy consent_records_select_own
  on public.consent_records
  for select to authenticated
  using (participant_id = public.current_participant_id());

create policy survey_submissions_select_own
  on public.survey_submissions
  for select to authenticated
  using (participant_id = public.current_participant_id());

create policy survey_answers_select_own
  on public.survey_answers
  for select to authenticated
  using (
    exists (
      select 1
      from public.survey_submissions s
      where s.id = survey_answers.submission_id
        and s.participant_id = public.current_participant_id()
    )
  );

comment on table public.study_condition_history is
  'Backend only. No participant access: condition is decided and read by the server.';
comment on table public.categories is
  'Backend only. Participants only see categories containing their own content, via the API.';
comment on table public.category_label_mappings is
  'Backend/research only. No participant access.';
comment on table public.videos is
  'Backend only. Direct reads would expose category/summary fields during BASIC.';
comment on table public.ai_classification_runs is
  'Backend only. Raw AI labels must not be visible during BASIC.';
comment on table public.app_sessions is
  'Backend only.';
comment on table public.interaction_events is
  'Backend only. Raw events are immutable (see trigger).';
comment on table public.api_tokens is
  'Backend only. Stores token hashes; never exposed to participants.';