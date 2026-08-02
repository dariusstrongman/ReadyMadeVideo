-- Migration 0002: private storage buckets + path-scoped object policies
-- Buckets are created via the Storage API (see scripts/apply_migrations.py) because
-- storage.buckets rows need the storage schema owner; the POLICIES live here.
--
-- Path convention (enforced by policy — segment 2 must equal auth.uid()):
--   raw-footage: users/{user_id}/projects/{project_id}/raw/{asset_id}/{filename}
--   exports:     users/{user_id}/projects/{project_id}/exports/{render_job_id}.mp4
--
-- raw-footage: users may insert/select/delete ONLY under their own users/{uid}/ prefix.
-- exports: users may select (needed for createSignedUrl) + delete their own; only the
--          render backend (service role) writes exports.

-- raw-footage policies
drop policy if exists raw_footage_insert_own on storage.objects;
create policy raw_footage_insert_own on storage.objects
  for insert to authenticated
  with check (
    bucket_id = 'raw-footage'
    and (storage.foldername(name))[1] = 'users'
    and (storage.foldername(name))[2] = auth.uid()::text
  );

drop policy if exists raw_footage_select_own on storage.objects;
create policy raw_footage_select_own on storage.objects
  for select to authenticated
  using (
    bucket_id = 'raw-footage'
    and (storage.foldername(name))[1] = 'users'
    and (storage.foldername(name))[2] = auth.uid()::text
  );

drop policy if exists raw_footage_delete_own on storage.objects;
create policy raw_footage_delete_own on storage.objects
  for delete to authenticated
  using (
    bucket_id = 'raw-footage'
    and (storage.foldername(name))[1] = 'users'
    and (storage.foldername(name))[2] = auth.uid()::text
  );

-- exports policies (read + delete own; no user writes)
drop policy if exists exports_select_own on storage.objects;
create policy exports_select_own on storage.objects
  for select to authenticated
  using (
    bucket_id = 'exports'
    and (storage.foldername(name))[1] = 'users'
    and (storage.foldername(name))[2] = auth.uid()::text
  );

drop policy if exists exports_delete_own on storage.objects;
create policy exports_delete_own on storage.objects
  for delete to authenticated
  using (
    bucket_id = 'exports'
    and (storage.foldername(name))[1] = 'users'
    and (storage.foldername(name))[2] = auth.uid()::text
  );
