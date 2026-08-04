-- Migration 0020: retryable storage-cleanup queue.
--
-- When the autoedit bridge uploads a preview and the candidate insert then fails (or is
-- superseded by an idempotency-race winner), the preview object is orphaned. Removal is
-- attempted immediately; if THAT fails it must not be swallowed and must not fail the
-- otherwise-successful edit. Instead a row is persisted here and drained on a later run.
-- Service-role only (internal janitorial queue) plus operator read for support/forensics.
create table if not exists public.pending_storage_cleanup (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  bucket text not null,
  object_path text not null,
  reason text,
  attempts integer not null default 0,
  last_error text,
  created_at timestamptz not null default now(),
  last_attempt_at timestamptz,
  cleaned_at timestamptz,
  unique (bucket, object_path)
);
create index if not exists pending_storage_cleanup_open_idx
  on public.pending_storage_cleanup(project_id) where cleaned_at is null;

alter table public.pending_storage_cleanup enable row level security;
-- No authenticated INSERT/UPDATE/DELETE/SELECT policies: customers never touch this queue.
-- The render backend uses the service role (bypasses RLS). Operators may read it.
drop policy if exists pending_storage_cleanup_operator_read on public.pending_storage_cleanup;
create policy pending_storage_cleanup_operator_read on public.pending_storage_cleanup
  for select to authenticated using (public.is_operator());
