-- Migration 0007: immutable autonomous baselines + human-ceiling evaluation.
--
-- The autonomous initial/revised timelines remain evidence. Human work branches
-- from them into a separate lineage, and every constrained manual operation is
-- recorded through user_corrections. Approved human timelines are also frozen.

alter table public.timelines
  add column if not exists lineage text not null default 'legacy'
    check (lineage in ('legacy','autonomous_initial','autonomous_intermediate',
                       'autonomous_revised','human_draft','human_approved')),
  add column if not exists parent_timeline_id uuid references public.timelines(id),
  add column if not exists edit_run_id uuid references public.edit_runs(id) on delete set null,
  add column if not exists is_immutable boolean not null default false,
  add column if not exists approved_by uuid references auth.users(id) on delete set null,
  add column if not exists approved_at timestamptz;

alter table public.timelines drop constraint if exists timeline_baseline_is_immutable;
alter table public.timelines add constraint timeline_baseline_is_immutable check (
  lineage not in ('autonomous_initial','autonomous_revised','human_approved')
  or is_immutable
);

create index if not exists timelines_lineage_idx
  on public.timelines(project_id, edit_run_id, lineage, version);

create or replace function public.protect_immutable_timeline()
returns trigger language plpgsql set search_path = public as $$
begin
  if tg_op = 'DELETE' and old.is_immutable then
    raise exception 'immutable timeline % cannot be deleted', old.id;
  end if;
  if tg_op = 'UPDATE' and old.is_immutable then
    -- Only a one-way metadata freeze is permitted. Timeline content, lineage,
    -- ancestry, project, owner, and version can never change after freezing.
    if new.timeline_json is distinct from old.timeline_json
       or new.project_id is distinct from old.project_id
       or new.user_id is distinct from old.user_id
       or new.version is distinct from old.version
       or new.lineage is distinct from old.lineage
       or new.parent_timeline_id is distinct from old.parent_timeline_id
       or new.edit_run_id is distinct from old.edit_run_id
       or new.is_immutable is distinct from old.is_immutable then
      raise exception 'immutable timeline % cannot be modified', old.id;
    end if;
  end if;
  if tg_op = 'DELETE' then
    return old;
  end if;
  return new;
end $$;

drop trigger if exists protect_immutable_timeline on public.timelines;
create trigger protect_immutable_timeline
  before update or delete on public.timelines
  for each row execute function public.protect_immutable_timeline();

create table if not exists public.human_edit_sessions (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  edit_run_id uuid references public.edit_runs(id) on delete set null,
  operator_user_id uuid not null references auth.users(id) on delete restrict,
  autonomous_initial_timeline_id uuid not null references public.timelines(id) on delete restrict,
  autonomous_revised_timeline_id uuid not null references public.timelines(id) on delete restrict,
  current_timeline_id uuid references public.timelines(id) on delete restrict,
  approved_timeline_id uuid references public.timelines(id) on delete restrict,
  status text not null default 'active'
    check (status in ('active','approved','abandoned')),
  human_correction_seconds double precision not null default 0
    check (human_correction_seconds >= 0),
  started_at timestamptz not null default now(),
  approved_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index if not exists human_edit_one_active_per_project
  on public.human_edit_sessions(project_id) where status = 'active';
create index if not exists human_edit_sessions_project_idx
  on public.human_edit_sessions(project_id, created_at desc);
alter table public.human_edit_sessions enable row level security;
drop policy if exists human_sessions_select on public.human_edit_sessions;
create policy human_sessions_select on public.human_edit_sessions
  for select to authenticated using (user_id = auth.uid() or public.is_operator());
drop trigger if exists own_project_check on public.human_edit_sessions;
create trigger own_project_check before insert or update on public.human_edit_sessions
  for each row execute function public.enforce_project_ownership();
drop trigger if exists human_sessions_touch on public.human_edit_sessions;
create trigger human_sessions_touch before update on public.human_edit_sessions
  for each row execute function public.touch_updated_at();

create or replace function public.enforce_human_session_refs()
returns trigger language plpgsql security definer set search_path = public as $$
declare ref record;
begin
  select project_id, user_id into ref from public.timelines
    where id = new.autonomous_initial_timeline_id;
  if not found or ref.project_id <> new.project_id or ref.user_id <> new.user_id then
    raise exception 'autonomous initial timeline is outside the session project/user';
  end if;
  select project_id, user_id into ref from public.timelines
    where id = new.autonomous_revised_timeline_id;
  if not found or ref.project_id <> new.project_id or ref.user_id <> new.user_id then
    raise exception 'autonomous revised timeline is outside the session project/user';
  end if;
  if new.current_timeline_id is not null then
    select project_id, user_id into ref from public.timelines where id = new.current_timeline_id;
    if not found or ref.project_id <> new.project_id or ref.user_id <> new.user_id then
      raise exception 'current timeline is outside the session project/user';
    end if;
  end if;
  if new.approved_timeline_id is not null then
    select project_id, user_id into ref from public.timelines where id = new.approved_timeline_id;
    if not found or ref.project_id <> new.project_id or ref.user_id <> new.user_id then
      raise exception 'approved timeline is outside the session project/user';
    end if;
  end if;
  if new.edit_run_id is not null then
    select project_id, user_id into ref from public.edit_runs where id = new.edit_run_id;
    if not found or ref.project_id <> new.project_id or ref.user_id <> new.user_id then
      raise exception 'edit run is outside the session project/user';
    end if;
  end if;
  return new;
end $$;
drop trigger if exists human_session_refs_check on public.human_edit_sessions;
create trigger human_session_refs_check before insert or update on public.human_edit_sessions
  for each row execute function public.enforce_human_session_refs();

alter table public.user_corrections
  add column if not exists human_edit_session_id uuid
    references public.human_edit_sessions(id) on delete set null,
  add column if not exists base_timeline_id uuid references public.timelines(id) on delete restrict,
  add column if not exists result_timeline_id uuid references public.timelines(id) on delete restrict,
  add column if not exists operation_index integer,
  add column if not exists correction_type text
    check (correction_type in ('replacement','trim','reorder','audio','title',
                               'insert','delete','speed','caption')),
  add column if not exists elapsed_seconds double precision
    check (elapsed_seconds is null or elapsed_seconds >= 0),
  add column if not exists operator_user_id uuid references auth.users(id) on delete set null;
create unique index if not exists user_corrections_session_operation_idx
  on public.user_corrections(human_edit_session_id, operation_index)
  where human_edit_session_id is not null;

create or replace function public.enforce_human_correction_refs()
returns trigger language plpgsql security definer set search_path = public as $$
declare ref record;
begin
  if new.human_edit_session_id is null then
    return new;
  end if;
  select project_id, user_id into ref from public.human_edit_sessions
    where id = new.human_edit_session_id;
  if not found or ref.project_id <> new.project_id or ref.user_id <> new.user_id then
    raise exception 'human edit session is outside the correction project/user';
  end if;
  select project_id, user_id into ref from public.timelines where id = new.base_timeline_id;
  if not found or ref.project_id <> new.project_id or ref.user_id <> new.user_id then
    raise exception 'base timeline is outside the correction project/user';
  end if;
  select project_id, user_id into ref from public.timelines where id = new.result_timeline_id;
  if not found or ref.project_id <> new.project_id or ref.user_id <> new.user_id then
    raise exception 'result timeline is outside the correction project/user';
  end if;
  return new;
end $$;
drop trigger if exists human_correction_refs_check on public.user_corrections;
create trigger human_correction_refs_check before insert or update on public.user_corrections
  for each row execute function public.enforce_human_correction_refs();

alter table public.draft_evaluations
  add column if not exists clips_manually_reordered integer,
  add column if not exists audio_changes integer,
  add column if not exists title_changes integer;

create table if not exists public.timeline_scorecards (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  timeline_id uuid not null references public.timelines(id) on delete restrict,
  human_edit_session_id uuid references public.human_edit_sessions(id) on delete set null,
  evaluator_user_id uuid not null references auth.users(id) on delete restrict,
  evaluator_role text not null default 'operator'
    check (evaluator_role in ('operator','founder','customer','system')),
  scores jsonb not null default '{}'::jsonb,
  overall_rating integer not null check (overall_rating between 1 and 10),
  publishable boolean,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(timeline_id, evaluator_user_id, evaluator_role)
);
create index if not exists timeline_scorecards_project_idx
  on public.timeline_scorecards(project_id, created_at desc);
alter table public.timeline_scorecards enable row level security;
drop policy if exists timeline_scorecards_select on public.timeline_scorecards;
create policy timeline_scorecards_select on public.timeline_scorecards
  for select to authenticated using (user_id = auth.uid() or public.is_operator());
drop trigger if exists own_project_check on public.timeline_scorecards;
create trigger own_project_check before insert or update on public.timeline_scorecards
  for each row execute function public.enforce_project_ownership();
drop trigger if exists timeline_scorecards_touch on public.timeline_scorecards;
create trigger timeline_scorecards_touch before update on public.timeline_scorecards
  for each row execute function public.touch_updated_at();

create or replace function public.enforce_timeline_scorecard_refs()
returns trigger language plpgsql security definer set search_path = public as $$
declare ref record;
begin
  select project_id, user_id into ref from public.timelines where id = new.timeline_id;
  if not found or ref.project_id <> new.project_id or ref.user_id <> new.user_id then
    raise exception 'scorecard timeline is outside the scorecard project/user';
  end if;
  if new.human_edit_session_id is not null then
    select project_id, user_id into ref from public.human_edit_sessions
      where id = new.human_edit_session_id;
    if not found or ref.project_id <> new.project_id or ref.user_id <> new.user_id then
      raise exception 'scorecard session is outside the scorecard project/user';
    end if;
  end if;
  return new;
end $$;
drop trigger if exists timeline_scorecard_refs_check on public.timeline_scorecards;
create trigger timeline_scorecard_refs_check before insert or update on public.timeline_scorecards
  for each row execute function public.enforce_timeline_scorecard_refs();

-- New tables remain service-role write only. Operators and owners receive the
-- minimum read access above; all mutations pass through the audited API.
