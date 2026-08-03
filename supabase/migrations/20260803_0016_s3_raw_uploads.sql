-- Migration 0016: S3 multipart raw-footage uploads
--
-- Adds storage-provider provenance to media_assets and a server-owned table that
-- tracks in-flight multipart upload sessions. The render backend (service role)
-- is the ONLY writer of these; RLS is enabled with no authenticated policy so a
-- browser cannot forge a session, key, or ownership claim — clients act only
-- through the ownership-checked FastAPI endpoints.
--
-- Path convention (server-built, never client-chosen):
--   s3 raw-footage: users/{user_id}/projects/{project_id}/raw-footage/{asset_id}/{safe_filename}
-- Existing Supabase objects are NOT migrated: existing media_assets rows keep
-- storage_provider='supabase' and continue to read from the raw-footage bucket.

-- ---- media_assets provenance ----
alter table public.media_assets
  add column if not exists storage_provider text not null default 'supabase',
  add column if not exists storage_bucket text,
  add column if not exists storage_key text,
  add column if not exists etag text,
  add column if not exists checksum_sha256 text,
  add column if not exists content_type text,
  add column if not exists validation_status text not null default 'validated';

do $$ begin
  alter table public.media_assets
    add constraint media_assets_storage_provider_chk
    check (storage_provider in ('supabase', 's3'));
exception when duplicate_object then null; end $$;

do $$ begin
  alter table public.media_assets
    add constraint media_assets_validation_status_chk
    check (validation_status in ('pending', 'validated', 'rejected'));
exception when duplicate_object then null; end $$;

-- ---- raw_upload_sessions ----
create table if not exists public.raw_upload_sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  asset_id uuid not null,
  provider text not null default 's3',
  bucket text not null,
  object_key text not null,
  upload_id text not null,
  filename text not null,
  content_type text not null,
  declared_size bigint not null,
  part_size integer not null,
  status text not null default 'initiated'
    check (status in ('initiated', 'completed', 'finalized', 'aborted', 'failed')),
  error_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists raw_upload_sessions_user_idx
  on public.raw_upload_sessions(user_id, status);
create index if not exists raw_upload_sessions_project_idx
  on public.raw_upload_sessions(project_id);

alter table public.raw_upload_sessions enable row level security;
-- Intentionally NO policy for role `authenticated`: RLS-enabled + no policy means
-- the browser (publishable key) can neither read nor write these rows. The render
-- backend uses the service role, which bypasses RLS after checking ownership.
