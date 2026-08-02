-- Migration 0006: explicit cancellation states + cancellation attribution
alter table public.pipeline_jobs drop constraint if exists pipeline_jobs_status_check;
alter table public.pipeline_jobs add constraint pipeline_jobs_status_check
  check (status in ('queued','processing','cancel_requested','completed',
                    'failed','cancelled'));
alter table public.pipeline_jobs add column if not exists cancel_requested_by uuid
  references auth.users(id) on delete set null;
alter table public.pipeline_jobs add column if not exists cancel_requested_at timestamptz;

-- the active-job idempotency index must also treat cancel_requested as active
drop index if exists pipeline_jobs_active_uniq;
create unique index pipeline_jobs_active_uniq
  on public.pipeline_jobs (project_id, kind)
  where status in ('queued','processing','cancel_requested');
