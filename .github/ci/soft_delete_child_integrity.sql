\set ON_ERROR_STOP on
begin;

-- Proves migration 0019: (a) EVERY customer-readable project-owned child table gates its
-- SELECT on the parent project being live, (b) a soft-deleted project's child rows are
-- actually invisible under the authenticated role, and (c) the legacy authenticated INSERT
-- into render_jobs is gone. Runs against the full migration chain on real Postgres.

-- (a) Structural: each policy's USING clause references the parent-liveness predicate.
do $$
declare t text; missing text := '';
begin
  foreach t in array array[
      'media_assets','timelines','render_jobs','pipeline_jobs','asset_analysis','segments',
      'candidate_runs','critic_runs','publishability_reports','tournament_runs',
      'editor_documents','editor_operations','editor_revision_proposals',
      'editor_render_requests','editor_audit_events'] loop
    if not exists (
      select 1 from pg_policies
      where schemaname = 'public' and tablename = t
        and cmd in ('SELECT', 'ALL')
        and coalesce(qual, '') like '%project_not_deleted%') then
      missing := missing || ' ' || t;
    end if;
  end loop;
  if missing <> '' then
    raise exception 'child SELECT RLS missing the parent-not-deleted gate on:%', missing;
  end if;
end $$;

-- Seed one owner, a LIVE project and a SOFT-DELETED project, each with a media asset.
insert into auth.users(id, email) values
 ('f7000000-0000-0000-0000-000000000001', 'sd-owner@example.test');
insert into public.projects(id, user_id, name, status, deleted_at) values
 ('f7000000-0000-0000-0000-000000000010', 'f7000000-0000-0000-0000-000000000001', 'Live', 'ready', null),
 ('f7000000-0000-0000-0000-000000000011', 'f7000000-0000-0000-0000-000000000001', 'Dead', 'ready', now());
insert into public.timelines(id, project_id, user_id, version, timeline_json) values
 ('f7000000-0000-0000-0000-000000000020', 'f7000000-0000-0000-0000-000000000010',
  'f7000000-0000-0000-0000-000000000001', 1, '{}');
insert into public.media_assets(id, project_id, user_id, filename, storage_path) values
 ('f7000000-0000-0000-0000-000000000030', 'f7000000-0000-0000-0000-000000000010',
  'f7000000-0000-0000-0000-000000000001', 'live.mp4', 'users/f7000000-0000-0000-0000-000000000001/projects/f7000000-0000-0000-0000-000000000010/raw/live.mp4'),
 ('f7000000-0000-0000-0000-000000000031', 'f7000000-0000-0000-0000-000000000011',
  'f7000000-0000-0000-0000-000000000001', 'dead.mp4', 'users/f7000000-0000-0000-0000-000000000001/projects/f7000000-0000-0000-0000-000000000011/raw/dead.mp4');

-- Grant table privileges so the assertions below exercise RLS, not missing GRANTs.
grant select on public.media_assets to authenticated;
grant insert on public.render_jobs to authenticated;

-- Resolve auth.uid() to the owner for the RLS checks.
create or replace function auth.uid() returns uuid language sql stable as
  $f$ select 'f7000000-0000-0000-0000-000000000001'::uuid $f$;

-- (b) Under the authenticated role the owner sees ONLY the live project's asset.
do $$
declare visible int;
begin
  set local role authenticated;
  select count(*) into visible from public.media_assets;
  reset role;
  if visible <> 1 then
    raise exception 'soft-deleted project child leaked: expected 1 visible asset, got %', visible;
  end if;
end $$;
reset role;

-- (c) The authenticated role can NO LONGER insert render_jobs (no INSERT policy remains).
do $$
declare inserted boolean := false;
begin
  set local role authenticated;
  begin
    insert into public.render_jobs(project_id, timeline_id, user_id, status)
      values ('f7000000-0000-0000-0000-000000000010', 'f7000000-0000-0000-0000-000000000020',
              'f7000000-0000-0000-0000-000000000001', 'queued');
    inserted := true;
  exception when others then inserted := false;   -- RLS default-deny (no insert policy)
  end;
  reset role;
  if inserted then
    raise exception 'authenticated was able to INSERT into legacy render_jobs';
  end if;
end $$;
reset role;

rollback;
