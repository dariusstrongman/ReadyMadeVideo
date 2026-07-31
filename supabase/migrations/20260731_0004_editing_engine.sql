-- Migration 0004: autoedit runs (auditability) + user corrections (personalization)

create table if not exists public.edit_runs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  status text not null default 'running'
    check (status in ('running','completed','failed')),
  brief text,
  blueprint jsonb,
  selection jsonb,
  validator_report jsonb,
  critic_verdict jsonb,
  revision_ops jsonb,
  timeline_v1_id uuid references public.timelines(id),
  timeline_v2_id uuid references public.timelines(id),
  preview_paths jsonb,
  error_message text,
  created_at timestamptz not null default now(),
  completed_at timestamptz
);
create index if not exists edit_runs_project_idx on public.edit_runs(project_id, created_at desc);
alter table public.edit_runs enable row level security;
drop policy if exists edit_runs_select_own on public.edit_runs;
create policy edit_runs_select_own on public.edit_runs
  for select to authenticated using (user_id = auth.uid());
-- service-role writes only

create table if not exists public.user_corrections (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  original_timeline_version integer not null,
  requested_change text not null,
  applied_operations jsonb not null default '[]',
  accepted boolean not null,
  final_timeline_version integer,
  project_style text,
  segment_features jsonb,
  created_at timestamptz not null default now()
);
create index if not exists user_corrections_user_idx on public.user_corrections(user_id, created_at desc);
alter table public.user_corrections enable row level security;
drop policy if exists user_corrections_select_own on public.user_corrections;
create policy user_corrections_select_own on public.user_corrections
  for select to authenticated using (user_id = auth.uid());
-- service-role writes only
