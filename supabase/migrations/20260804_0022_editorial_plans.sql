-- Migration 0022: Editorial Planner v1 storage + job kind.
--
-- A separate, structured planning stage that sits between analysis (the segment
-- catalog) and timeline generation. Each row is one versioned, immutable-to-
-- customers EditorialPlan: grounded JSON that downstream picture-edit, graphics,
-- audio, color and render systems can consume. The existing autoedit pipeline
-- and Product Editor tables are untouched.
create table if not exists public.editorial_plans (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  version integer not null check (version > 0),
  status text not null check (status in ('approved', 'insufficient_footage')),
  quality_score integer not null check (quality_score between 0 and 100),
  attempts integer not null default 1 check (attempts > 0),
  request jsonb not null default '{}'::jsonb,      -- binding user constraints
  plan jsonb not null,                             -- the validated EditorialPlan
  validation jsonb not null default '{}'::jsonb,   -- violations history (audit)
  created_at timestamptz not null default now(),
  unique (project_id, version)
);
create index if not exists editorial_plans_project_idx
  on public.editorial_plans(project_id, version desc);

alter table public.editorial_plans enable row level security;

-- Owner reads their own plans only while the parent project is live (matching
-- the 0019/0021 soft-delete child gate). Writes are service-role only, so plans
-- are immutable to customers. Operators read for support.
drop policy if exists editorial_plans_select_own on public.editorial_plans;
create policy editorial_plans_select_own on public.editorial_plans
  for select to authenticated
  using (user_id = auth.uid() and public.project_not_deleted(project_id));
drop policy if exists operator_read on public.editorial_plans;
create policy operator_read on public.editorial_plans
  for select to authenticated using (public.is_operator());

-- Every row must belong to its project's owner (same guard as sibling tables).
drop trigger if exists own_project_check on public.editorial_plans;
create trigger own_project_check before insert or update on public.editorial_plans
  for each row execute function public.enforce_project_ownership();

-- ---- pipeline_jobs: allow the new OPTIONAL job kind (existing kinds unchanged) ----
-- Migration 0005 defined pipeline_jobs_kind_check as
-- (analysis, autoedit, revision, final_render); the planner enqueues
-- kind='editorial_plan'. Idempotent drop-then-add; 0005 is never reapplied in CI,
-- and this file runs after it, so reapplications cannot revert the widened check.
alter table public.pipeline_jobs
  drop constraint if exists pipeline_jobs_kind_check;
alter table public.pipeline_jobs
  add constraint pipeline_jobs_kind_check
  check (kind in ('analysis', 'autoedit', 'revision', 'final_render',
                  'editorial_plan'));
