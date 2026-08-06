-- Migration 0027: length preference is a RANGE, not a point target.
--
-- Product decision (2026-08-06): the customer picks a band ("10-60 sec",
-- "1-5 min", "5-10 min", or let the AI decide) and the planner recommends
-- the ideal length WITHIN it from the footage and story — which is exactly
-- the planner's native durationMin/durationMax contract. Supersedes 0026's
-- single target_duration_seconds (column kept, no longer written).

alter table public.projects
  add column if not exists duration_min_seconds integer,
  add column if not exists duration_max_seconds integer;

do $$ begin
  alter table public.projects
    add constraint projects_duration_range_chk
    check (
      (duration_min_seconds is null and duration_max_seconds is null)
      or (duration_min_seconds between 5 and 3600
          and duration_max_seconds between 5 and 3600
          and duration_min_seconds <= duration_max_seconds));
exception when duplicate_object then null; end $$;
