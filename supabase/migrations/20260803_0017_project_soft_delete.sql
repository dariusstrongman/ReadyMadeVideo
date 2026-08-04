-- Migration 0017: safe server-authorized project deletion (soft-delete).
--
-- ADDITIVE ONLY. A project cannot be hard-deleted once it has immutable evidence:
-- candidate_runs / audio_mix_runs / picture_edit_runs are guarded by
-- protect_*_evidence triggers (raise on delete), and editor_documents pin
-- timelines/candidates via on-delete-restrict. A project cascade-delete therefore
-- always fails for any real journey project.
--
-- Safe deletion instead marks deleted_at (hiding the project from the customer) and
-- cleans up storage artifacts, while PRESERVING all immutable evidence. Idempotent.
alter table public.projects add column if not exists deleted_at timestamptz;

-- Active-project lookups exclude soft-deleted rows.
create index if not exists projects_active_idx
  on public.projects(user_id) where deleted_at is null;
