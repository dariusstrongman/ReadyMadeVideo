-- Migration 0028: Output Intelligence — recommendations, packages, deliverables.
--
-- A new stage BETWEEN analysis and the Editorial Planner: the system reads the
-- existing segment catalog, derives what finished videos the footage honestly
-- supports, recommends the strongest package, and executes the customer's
-- accepted selection as first-class deliverables — each with its own editorial
-- plan identity, ancestry and failure state. Flag-gated (OUTPUT_INTELLIGENCE_
-- ENABLED, default off); with the flag off nothing writes here and the
-- existing journey is untouched. Additive only: no existing table changes.

-- ============ 1. output_recommendations ============
-- One row per (project, catalog identity, engine version): the ranked package
-- offer derived from a SPECIFIC catalog. catalog_hash binds it to the exact
-- segments it was computed from, so new/removed footage makes it detectably
-- stale rather than silently wrong.
create table if not exists public.output_recommendations (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  engine_version integer not null check (engine_version > 0),
  catalog_hash text not null,
  status text not null default 'active'
    check (status in ('active', 'superseded')),
  inventory jsonb not null,
  packages jsonb not null,
  recommended_key text,
  created_at timestamptz not null default now(),
  -- idempotency: recomputing over the same catalog with the same engine
  -- returns the existing row instead of minting a twin
  unique (project_id, catalog_hash, engine_version)
);
create index if not exists output_recommendations_project_idx
  on public.output_recommendations(project_id, created_at desc);

alter table public.output_recommendations enable row level security;
drop policy if exists output_recommendations_select_own on public.output_recommendations;
create policy output_recommendations_select_own on public.output_recommendations
  for select to authenticated
  using (user_id = auth.uid() and public.project_not_deleted(project_id));
drop policy if exists operator_read on public.output_recommendations;
create policy operator_read on public.output_recommendations
  for select to authenticated using (public.is_operator());
drop trigger if exists own_project_check on public.output_recommendations;
create trigger own_project_check
  before insert or update on public.output_recommendations
  for each row execute function public.enforce_project_ownership();

-- ============ 2. output_packages ============
-- The customer's accepted (or customized) selection, immutable once created.
-- request_key = hash(recommendation id + canonical selection): a double-click
-- or API retry of the same acceptance lands on the same row, never a twin.
-- Package completion is NEVER stored — it is derived truthfully from children.
create table if not exists public.output_packages (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  -- cascade, not restrict: projects cascade into BOTH tables, and a
  -- restrict here makes any future hard project delete order-dependent.
  -- Recommendations are never deleted on their own.
  recommendation_id uuid not null
    references public.output_recommendations(id) on delete cascade,
  catalog_hash text not null,
  request_key text not null,
  selection jsonb not null,
  status text not null default 'active'
    check (status in ('active', 'cancelled')),
  created_at timestamptz not null default now()
);
-- Idempotency binds ACTIVE packages only: a cancelled package must not
-- squat on its request_key forever — cancelling and accepting the same
-- selection again is a legitimate fresh start, not a duplicate.
create unique index if not exists output_packages_request_uniq
  on public.output_packages (project_id, request_key)
  where status = 'active';
create index if not exists output_packages_project_idx
  on public.output_packages(project_id, created_at desc);

alter table public.output_packages enable row level security;
drop policy if exists output_packages_select_own on public.output_packages;
create policy output_packages_select_own on public.output_packages
  for select to authenticated
  using (user_id = auth.uid() and public.project_not_deleted(project_id));
drop policy if exists operator_read on public.output_packages;
create policy operator_read on public.output_packages
  for select to authenticated using (public.is_operator());
drop trigger if exists own_project_check on public.output_packages;
create trigger own_project_check
  before insert or update on public.output_packages
  for each row execute function public.enforce_project_ownership();

-- ============ 3. output_deliverables ============
-- One finished video the package owes the customer. Executes through the
-- EXISTING pipeline (editorial_plan -> autoedit -> timeline/candidate), one
-- at a time per project (pipeline_jobs_active_uniq), and records the exact
-- plan/timeline identities it produced — ancestry, never inference.
create table if not exists public.output_deliverables (
  id uuid primary key default gen_random_uuid(),
  package_id uuid not null references public.output_packages(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  position integer not null check (position >= 0),
  spec jsonb not null,             -- deliverable intent contract (immutable)
  status text not null default 'queued'
    check (status in ('queued', 'planning', 'editing', 'ready',
                      'failed', 'cancelled', 'budget_blocked')),
  editorial_plan_id uuid references public.editorial_plans(id),
  editorial_plan_version integer,
  timeline_id uuid references public.timelines(id),
  error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (package_id, position)
);
create index if not exists output_deliverables_package_idx
  on public.output_deliverables(package_id, position);
create index if not exists output_deliverables_project_idx
  on public.output_deliverables(project_id, status);

alter table public.output_deliverables enable row level security;
drop policy if exists output_deliverables_select_own on public.output_deliverables;
create policy output_deliverables_select_own on public.output_deliverables
  for select to authenticated
  using (user_id = auth.uid() and public.project_not_deleted(project_id));
drop policy if exists operator_read on public.output_deliverables;
create policy operator_read on public.output_deliverables
  for select to authenticated using (public.is_operator());
drop trigger if exists own_project_check on public.output_deliverables;
create trigger own_project_check
  before insert or update on public.output_deliverables
  for each row execute function public.enforce_project_ownership();
