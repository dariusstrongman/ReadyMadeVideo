-- Full audiovisual editor, Milestone 1: inspectable preproduction contracts.
-- Additive only. No Project One artifacts or existing timeline rows are changed.

create table if not exists public.preproduction_runs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  version integer not null check (version > 0),
  status text not null check (status in ('ready','insufficient_coverage')),
  request jsonb not null,
  creative_treatment jsonb not null,
  capture_quality_report jsonb not null,
  composition_by_segment jsonb not null,
  story_variants jsonb not null,
  warnings jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  unique (project_id, version)
);

create index if not exists preproduction_runs_project_idx
  on public.preproduction_runs(project_id, version desc);

alter table public.preproduction_runs enable row level security;
drop policy if exists preproduction_runs_select_own on public.preproduction_runs;
create policy preproduction_runs_select_own on public.preproduction_runs
  for select to authenticated using (user_id = auth.uid());
drop policy if exists operator_read on public.preproduction_runs;
create policy operator_read on public.preproduction_runs
  for select to authenticated using (public.is_operator());
-- Writes stay service-role-only. The API validates + audits before insertion.

drop trigger if exists own_project_check on public.preproduction_runs;
create trigger own_project_check before insert or update on public.preproduction_runs
  for each row execute function public.enforce_project_ownership();
