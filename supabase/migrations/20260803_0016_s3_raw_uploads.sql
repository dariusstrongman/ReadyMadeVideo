-- Migration 0016: S3 multipart raw-footage uploads
--
-- Adds storage-provider provenance to media_assets and a server-owned table that
-- tracks in-flight multipart upload sessions.
--
-- SECURITY (review blocker #1): media_assets writes are moved to the service role
-- ONLY. Authenticated users keep owner-scoped SELECT but can no longer INSERT or
-- UPDATE — so they cannot forge/mutate storage_provider/bucket/key/etag and point
-- the service-role worker at another user's object. Provenance is created solely
-- by the finalize endpoint after full validation.
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

-- Provider-conditional provenance: s3 rows must carry bucket/key/etag + validated;
-- supabase rows must carry a storage_path (legacy behavior preserved).
do $$ begin
  alter table public.media_assets
    add constraint media_assets_provenance_chk check (
      (storage_provider = 'supabase' and storage_path is not null)
      or (storage_provider = 's3'
          and storage_bucket is not null and storage_key is not null
          and etag is not null and validation_status = 'validated')
    );
exception when duplicate_object then null; end $$;

-- SELECT-only for authenticated; writes are service-role only.
drop policy if exists media_assets_all_own on public.media_assets;
drop policy if exists media_assets_select_own on public.media_assets;
create policy media_assets_select_own on public.media_assets
  for select to authenticated using (user_id = auth.uid());

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
    check (status in ('initiated', 'completing', 'completed',
                      'finalizing', 'finalized', 'aborted', 'failed')),
  error_reason text,
  expires_at timestamptz not null default (now() + interval '24 hours'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint raw_upload_sessions_provider_chk check (provider = 's3'),
  constraint raw_upload_sessions_size_chk
    check (declared_size between 1 and 2147483648),
  constraint raw_upload_sessions_part_size_chk check (part_size >= 5242880),
  constraint raw_upload_sessions_asset_uniq unique (asset_id),
  constraint raw_upload_sessions_object_key_uniq unique (object_key),
  constraint raw_upload_sessions_upload_id_uniq unique (upload_id)
);

create index if not exists raw_upload_sessions_user_idx
  on public.raw_upload_sessions(user_id, status);
create index if not exists raw_upload_sessions_project_idx
  on public.raw_upload_sessions(project_id);
create index if not exists raw_upload_sessions_expiry_idx
  on public.raw_upload_sessions(expires_at) where status in ('initiated', 'completing');

alter table public.raw_upload_sessions enable row level security;
-- Intentionally NO policy for role `authenticated`: RLS-enabled + no policy means
-- the browser (publishable key) can neither read nor write these rows. The render
-- backend uses the service role, which bypasses RLS after checking ownership.
