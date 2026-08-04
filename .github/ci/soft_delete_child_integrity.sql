\set ON_ERROR_STOP on
begin;

-- Proves migrations 0019 + 0021: (a) EVERY authenticated owner-readable project-owned
-- child table gates its SELECT on the parent project being live, (b) a soft-deleted
-- project's child rows are actually invisible under the authenticated role across every
-- policy shape, (c) operator access is preserved, and (d) the legacy authenticated INSERT
-- into render_jobs is gone. Runs against the full migration chain on real Postgres.

-- (a) Structural: EVERY gated child table's owner-readable USING clause references the
-- parent-liveness predicate. This list is the complete set of authenticated owner-readable
-- project-owned tables (profiles is per-user, not project-owned, so it is excluded).
do $$
declare t text; missing text := '';
begin
  foreach t in array array[
      -- gated by 0019
      'media_assets','timelines','render_jobs','pipeline_jobs','asset_analysis','segments',
      'candidate_runs','critic_runs','publishability_reports','tournament_runs',
      'editor_documents','editor_operations','editor_revision_proposals',
      'editor_render_requests','editor_audit_events',
      -- gated by 0021
      'edit_runs','user_corrections','draft_evaluations','preproduction_runs',
      'picture_edit_runs','music_sound_runs','licensed_music_assets','audio_mix_runs',
      'graphics_runs','caption_runs','color_runs','human_edit_sessions',
      'human_edit_timing_events','timeline_scorecards','project_status_events',
      -- gated at creation (0022)
      'editorial_plans'] loop
    if not exists (
      select 1 from pg_policies
      where schemaname = 'public' and tablename = t
        and cmd in ('SELECT', 'ALL')
        -- most tables call project_not_deleted(project_id); project_status_events uses an
        -- inline EXISTS(projects ... deleted_at IS NULL). Accept either form. ILIKE because
        -- pg_get_expr decompiles the predicate as uppercase "IS NULL".
        and (coalesce(qual, '') ilike '%project_not_deleted%'
             or coalesce(qual, '') ilike '%deleted_at is null%')) then
      missing := missing || ' ' || t;
    end if;
  end loop;
  if missing <> '' then
    raise exception 'child SELECT RLS missing the parent-not-deleted gate on:%', missing;
  end if;
end $$;

-- (a2) Leak guard: on those same tables NO owner-readable SELECT/ALL policy may reference
-- auth.uid() WITHOUT the not-deleted gate. This catches an ungated DUPLICATE policy left
-- behind (RLS policies are OR-ed, so one ungated owner policy would defeat the gate).
do $$
declare r record; leaking text := '';
begin
  for r in
    select tablename, policyname, coalesce(qual, '') as q
    from pg_policies
    where schemaname = 'public'
      and tablename in (
        'media_assets','timelines','render_jobs','pipeline_jobs','asset_analysis','segments',
        'candidate_runs','critic_runs','publishability_reports','tournament_runs',
        'editor_documents','editor_operations','editor_revision_proposals',
        'editor_render_requests','editor_audit_events',
        'edit_runs','user_corrections','draft_evaluations','preproduction_runs',
        'picture_edit_runs','music_sound_runs','licensed_music_assets','audio_mix_runs',
        'graphics_runs','caption_runs','color_runs','human_edit_sessions',
        'human_edit_timing_events','timeline_scorecards','project_status_events',
        'editorial_plans')
      and cmd in ('SELECT', 'ALL')
  loop
    if r.q ilike '%auth.uid()%'
       and r.q not ilike '%project_not_deleted%'
       and r.q not ilike '%deleted_at is null%' then
      leaking := leaking || ' ' || r.tablename || '.' || r.policyname;
    end if;
  end loop;
  if leaking <> '' then
    raise exception 'ungated owner-readable policy leaks soft-deleted children:%', leaking;
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

-- Seed a child row in the LIVE and the DEAD project for a representative table of each
-- policy shape: owner-only (edit_runs), milestone owner-only (preproduction_runs),
-- owner-or-operator (draft_evaluations), and inline-EXISTS (project_status_events).
insert into public.edit_runs(id, project_id, user_id) values
 ('f7000000-0000-0000-0000-000000000040', 'f7000000-0000-0000-0000-000000000010', 'f7000000-0000-0000-0000-000000000001'),
 ('f7000000-0000-0000-0000-000000000041', 'f7000000-0000-0000-0000-000000000011', 'f7000000-0000-0000-0000-000000000001');
insert into public.preproduction_runs
 (id, project_id, user_id, version, status, request, creative_treatment,
  capture_quality_report, composition_by_segment, story_variants) values
 ('f7000000-0000-0000-0000-000000000050', 'f7000000-0000-0000-0000-000000000010',
  'f7000000-0000-0000-0000-000000000001', 1, 'ready', '{}', '{}', '{}', '{}', '{}'),
 ('f7000000-0000-0000-0000-000000000051', 'f7000000-0000-0000-0000-000000000011',
  'f7000000-0000-0000-0000-000000000001', 1, 'ready', '{}', '{}', '{}', '{}', '{}');
insert into public.draft_evaluations(id, project_id, user_id) values
 ('f7000000-0000-0000-0000-000000000060', 'f7000000-0000-0000-0000-000000000010', 'f7000000-0000-0000-0000-000000000001'),
 ('f7000000-0000-0000-0000-000000000061', 'f7000000-0000-0000-0000-000000000011', 'f7000000-0000-0000-0000-000000000001');
insert into public.project_status_events(id, project_id, to_status) values
 ('f7000000-0000-0000-0000-000000000070', 'f7000000-0000-0000-0000-000000000010', 'ready'),
 ('f7000000-0000-0000-0000-000000000071', 'f7000000-0000-0000-0000-000000000011', 'ready');

-- An operator (separate user) to prove operator access is preserved.
insert into auth.users(id, email) values
 ('f7000000-0000-0000-0000-0000000000AA', 'sd-operator@example.test');
insert into public.operators(user_id) values ('f7000000-0000-0000-0000-0000000000AA');

-- Grant table privileges so the assertions below exercise RLS, not missing GRANTs.
-- projects is granted too: project_status_events' policy reads it via an inline EXISTS
-- (authenticated has this grant in production; RLS still filters the rows).
grant select on public.projects, public.media_assets, public.edit_runs,
                public.preproduction_runs, public.draft_evaluations,
                public.project_status_events to authenticated;
grant insert on public.render_jobs to authenticated;

-- Resolve auth.uid() to the owner for the RLS checks.
create or replace function auth.uid() returns uuid language sql stable as
  $f$ select 'f7000000-0000-0000-0000-000000000001'::uuid $f$;

-- (b) Under the authenticated OWNER role, every gated table shows ONLY the live row.
do $$
declare t text; visible int;
begin
  set local role authenticated;
  foreach t in array array['media_assets','edit_runs','preproduction_runs',
                            'draft_evaluations','project_status_events'] loop
    execute format('select count(*) from public.%I', t) into visible;
    if visible <> 1 then
      reset role;
      raise exception 'soft-deleted % child leaked to owner: expected 1 visible, got %', t, visible;
    end if;
  end loop;
  reset role;
end $$;
reset role;

-- (c) Operator access preserved: the operator still sees BOTH draft_evaluations rows
-- (live + soft-deleted) via the owner-or-operator policy's operator branch.
create or replace function auth.uid() returns uuid language sql stable as
  $f$ select 'f7000000-0000-0000-0000-0000000000AA'::uuid $f$;
do $$
declare visible int;
begin
  set local role authenticated;
  select count(*) into visible from public.draft_evaluations;
  reset role;
  if visible <> 2 then
    raise exception 'operator lost access to project children: expected 2, got %', visible;
  end if;
end $$;
reset role;
-- restore owner uid for the remaining check
create or replace function auth.uid() returns uuid language sql stable as
  $f$ select 'f7000000-0000-0000-0000-000000000001'::uuid $f$;

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
