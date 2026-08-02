-- Migration 0005: relational-ownership enforcement, project states + events,
-- operator role, persistent pipeline jobs, evaluation framework, cost telemetry.

-- ============ 1. relational ownership (DB boundary, not just RLS) ============
-- Every child row's user_id must equal its project's owner; extra checks tie
-- render_jobs to their timeline and analysis/segments to their asset.

create or replace function public.enforce_project_ownership()
returns trigger language plpgsql security definer set search_path = public as $$
declare owner uuid;
begin
  select user_id into owner from public.projects where id = new.project_id;
  if owner is null then
    raise exception 'project % does not exist', new.project_id;
  end if;
  if owner <> new.user_id then
    raise exception 'user_id does not match project owner (cross-user write rejected)';
  end if;
  return new;
end $$;

create or replace function public.enforce_render_job_refs()
returns trigger language plpgsql security definer set search_path = public as $$
declare t record;
begin
  select project_id, user_id into t from public.timelines where id = new.timeline_id;
  if t is null then
    raise exception 'timeline % does not exist', new.timeline_id;
  end if;
  if t.project_id <> new.project_id or t.user_id <> new.user_id then
    raise exception 'timeline does not belong to this project/user (cross-ref rejected)';
  end if;
  return new;
end $$;

create or replace function public.enforce_asset_refs()
returns trigger language plpgsql security definer set search_path = public as $$
declare a record;
begin
  select project_id, user_id into a from public.media_assets where id = new.asset_id;
  if a is null then
    raise exception 'asset % does not exist', new.asset_id;
  end if;
  if a.project_id <> new.project_id or a.user_id <> new.user_id then
    raise exception 'asset does not belong to this project/user (cross-ref rejected)';
  end if;
  return new;
end $$;

do $$
declare t text;
begin
  foreach t in array array['media_assets','timelines','render_jobs',
                           'asset_analysis','segments','edit_runs','user_corrections']
  loop
    execute format('drop trigger if exists own_project_check on public.%I', t);
    execute format('create trigger own_project_check before insert or update on public.%I
                    for each row execute function public.enforce_project_ownership()', t);
  end loop;
end $$;

drop trigger if exists render_job_refs_check on public.render_jobs;
create trigger render_job_refs_check before insert or update on public.render_jobs
  for each row execute function public.enforce_render_job_refs();

drop trigger if exists analysis_asset_refs_check on public.asset_analysis;
create trigger analysis_asset_refs_check before insert or update on public.asset_analysis
  for each row execute function public.enforce_asset_refs();
drop trigger if exists segments_asset_refs_check on public.segments;
create trigger segments_asset_refs_check before insert or update on public.segments
  for each row execute function public.enforce_asset_refs();

-- ============ 2. explicit project states + transition events ============
alter table public.projects drop constraint if exists projects_status_check;
alter table public.projects add constraint projects_status_check check (status in
  ('draft','uploading','ready','analyzing','analysis_failed','draft_ready',
   'rendering','render_failed','completed'));
alter table public.projects add column if not exists status_reason text;

create table if not exists public.project_status_events (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  from_status text,
  to_status text not null,
  reason text,
  created_at timestamptz not null default now()
);
alter table public.project_status_events enable row level security;
drop policy if exists pse_select_own on public.project_status_events;
create policy pse_select_own on public.project_status_events
  for select to authenticated using (
    exists (select 1 from public.projects p
            where p.id = project_id and p.user_id = auth.uid()));

create or replace function public.record_status_event()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  if new.status is distinct from old.status then
    insert into public.project_status_events (project_id, from_status, to_status, reason)
    values (new.id, old.status, new.status, new.status_reason);
  end if;
  return new;
end $$;
drop trigger if exists project_status_event on public.projects;
create trigger project_status_event after update of status on public.projects
  for each row execute function public.record_status_event();

-- ============ 3. operator role (server + DB enforced) ============
create table if not exists public.operators (
  user_id uuid primary key references auth.users(id) on delete cascade,
  created_at timestamptz not null default now()
);
alter table public.operators enable row level security;
drop policy if exists operators_see_self on public.operators;
create policy operators_see_self on public.operators
  for select to authenticated using (user_id = auth.uid());

create or replace function public.is_operator()
returns boolean language sql stable security definer set search_path = public as
$$ select exists (select 1 from public.operators where user_id = auth.uid()) $$;

-- operators can READ everything needed for the console (writes stay service-role)
do $$
declare t text;
begin
  foreach t in array array['projects','media_assets','timelines','render_jobs',
                           'asset_analysis','segments','edit_runs','user_corrections',
                           'profiles','project_status_events']
  loop
    execute format('drop policy if exists operator_read on public.%I', t);
    execute format('create policy operator_read on public.%I
                    for select to authenticated using (public.is_operator())', t);
  end loop;
end $$;

create table if not exists public.operator_audit (
  id uuid primary key default gen_random_uuid(),
  operator_user_id uuid not null references auth.users(id) on delete cascade,
  action text not null,
  project_id uuid references public.projects(id) on delete set null,
  details jsonb,
  created_at timestamptz not null default now()
);
alter table public.operator_audit enable row level security;
drop policy if exists audit_operator_read on public.operator_audit;
create policy audit_operator_read on public.operator_audit
  for select to authenticated using (public.is_operator());

-- ============ 4. persistent pipeline jobs ============
-- One table, four kinds (identical shape); partial unique index = idempotency:
-- at most one active job per (project, kind).
create table if not exists public.pipeline_jobs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  kind text not null check (kind in ('analysis','autoedit','revision','final_render')),
  status text not null default 'queued'
    check (status in ('queued','processing','completed','failed','cancelled')),
  current_stage text,
  progress integer not null default 0 check (progress between 0 and 100),
  attempt_count integer not null default 0,
  max_attempts integer not null default 3,
  params jsonb,
  artifacts jsonb,
  error_message text,
  provider_cost_estimate numeric(10,4),
  processing_seconds double precision,
  heartbeat_at timestamptz,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz
);
create unique index if not exists pipeline_jobs_active_uniq
  on public.pipeline_jobs (project_id, kind)
  where status in ('queued','processing');
create index if not exists pipeline_jobs_queue_idx
  on public.pipeline_jobs (status, created_at);
alter table public.pipeline_jobs enable row level security;
drop policy if exists pjobs_select_own on public.pipeline_jobs;
create policy pjobs_select_own on public.pipeline_jobs
  for select to authenticated using (user_id = auth.uid() or public.is_operator());
-- writes: service role only

drop trigger if exists own_project_check on public.pipeline_jobs;
create trigger own_project_check before insert or update on public.pipeline_jobs
  for each row execute function public.enforce_project_ownership();

-- ============ 5. evaluation framework ============
create table if not exists public.draft_evaluations (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  edit_run_id uuid references public.edit_runs(id) on delete set null,
  -- automatic metrics
  raw_footage_seconds double precision,
  source_asset_count integer,
  scene_count integer,
  segment_count integer,
  usable_segment_count integer,
  beats_requested integer,
  beats_filled integer,
  first_draft_seconds double precision,
  final_seconds double precision,
  duplicate_use_count integer,
  validation_issue_count integer,
  critic_request_count integer,
  revision_passes integer,
  -- manual correction metrics (operator-recorded)
  clips_manually_replaced integer,
  clips_manually_trimmed integer,
  captions_manually_changed integer,
  music_adjustments integer,
  human_correction_minutes double precision,
  -- ratings
  first_draft_rating integer check (first_draft_rating between 1 and 10),
  final_rating integer check (final_rating between 1 and 10),
  user_satisfaction integer check (user_satisfaction between 1 and 10),
  user_would_pay boolean,
  user_would_return boolean,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.draft_evaluations enable row level security;
drop policy if exists evals_select on public.draft_evaluations;
create policy evals_select on public.draft_evaluations
  for select to authenticated using (user_id = auth.uid() or public.is_operator());
drop trigger if exists own_project_check on public.draft_evaluations;
create trigger own_project_check before insert or update on public.draft_evaluations
  for each row execute function public.enforce_project_ownership();
drop trigger if exists evals_touch on public.draft_evaluations;
create trigger evals_touch before update on public.draft_evaluations
  for each row execute function public.touch_updated_at();

-- ============ 6. cost + timing telemetry ============
create table if not exists public.stage_metrics (
  id uuid primary key default gen_random_uuid(),
  project_id uuid references public.projects(id) on delete cascade,
  job_id uuid references public.pipeline_jobs(id) on delete set null,
  stage text not null,
  duration_seconds double precision,
  bytes bigint,
  units jsonb,                        -- e.g. {"whisper_minutes":1.2,"gemini_requests":1,"gemini_video_seconds":17}
  estimated_cost_usd numeric(10,5),
  created_at timestamptz not null default now()
);
create index if not exists stage_metrics_project_idx on public.stage_metrics(project_id, created_at);
alter table public.stage_metrics enable row level security;
drop policy if exists metrics_operator_read on public.stage_metrics;
create policy metrics_operator_read on public.stage_metrics
  for select to authenticated using (public.is_operator());
