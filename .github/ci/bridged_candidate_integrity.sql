\set ON_ERROR_STOP on
begin;

-- Proves migration 0016's bridged-candidate support survives the full migration
-- chain AND the per-migration idempotency reapplications (0013 reapply reverts the
-- pre-bridge trigger; 0016 must be reapplied after to restore it). Run this LAST.
create or replace function pg_temp.expect_rejected(statement text, label text)
returns void language plpgsql as $$
declare rejected boolean := false;
begin
  begin execute statement; exception when others then rejected := true; end;
  if not rejected then raise exception 'bridged assertion did not reject: %', label; end if;
end $$;

insert into auth.users(id, email) values
 ('f5000000-0000-0000-0000-000000000001', 'bridged-owner@example.test');
insert into public.projects(id, user_id, name, status) values
 ('f5000000-0000-0000-0000-000000000010', 'f5000000-0000-0000-0000-000000000001', 'Bridged Project', 'draft_ready');
insert into public.preproduction_runs
 (id, project_id, user_id, version, status, request, creative_treatment,
  capture_quality_report, composition_by_segment, story_variants) values
 ('f5000000-0000-0000-0000-000000000020', 'f5000000-0000-0000-0000-000000000010',
  'f5000000-0000-0000-0000-000000000001', 1, 'ready', '{"origin":"basic_autoedit"}',
  '{}', '{}', '{}', '{}');
insert into public.picture_edit_runs
 (id, project_id, user_id, preproduction_run_id, version, status, request,
  visual_rhythm_plans, candidates, selected_candidate_id) values
 ('f5000000-0000-0000-0000-000000000030', 'f5000000-0000-0000-0000-000000000010',
  'f5000000-0000-0000-0000-000000000001', 'f5000000-0000-0000-0000-000000000020', 1,
  'ready', '{}', '{}', '[]', 'bridged-pc');

-- KEY: a bridged candidate with NO music/audio/graphics/caption/color lineage inserts.
insert into public.candidate_runs
 (id, batch_id, project_id, user_id, preproduction_run_id, picture_edit_run_id,
  candidate_key, candidate_index, generation_kind, source_picture_candidate_id,
  variant_config, manifest, render_qc, preview_storage_bucket, preview_storage_path,
  created_by) values
 ('f5000000-0000-0000-0000-000000000040', 'f5000000-0000-0000-0000-000000000041',
  'f5000000-0000-0000-0000-000000000010', 'f5000000-0000-0000-0000-000000000001',
  'f5000000-0000-0000-0000-000000000020', 'f5000000-0000-0000-0000-000000000030',
  'bridged', 1, 'bridged', 'bridged-pc', '{}', '{"fabricatedFootage":false}', '{}',
  'exports',
  'users/f5000000-0000-0000-0000-000000000001/projects/f5000000-0000-0000-0000-000000000010/autoedit/x.mp4',
  'f5000000-0000-0000-0000-000000000001');
do $$ begin raise notice 'bridged candidate accepted after all migration reapplications'; end $$;

-- initial candidate without full ancestry is rejected (CHECK).
select pg_temp.expect_rejected($sql$
  insert into public.candidate_runs
   (batch_id, project_id, user_id, preproduction_run_id, picture_edit_run_id,
    candidate_key, candidate_index, generation_kind, source_picture_candidate_id,
    variant_config, manifest, render_qc, preview_storage_bucket, preview_storage_path, created_by)
  values ('f5000000-0000-0000-0000-000000000051', 'f5000000-0000-0000-0000-000000000010',
    'f5000000-0000-0000-0000-000000000001', 'f5000000-0000-0000-0000-000000000020',
    'f5000000-0000-0000-0000-000000000030', 'init', 1, 'initial', 'pc', '{}',
    '{"fabricatedFootage":false}', '{}', 'exports',
    'users/f5000000-0000-0000-0000-000000000001/projects/f5000000-0000-0000-0000-000000000010/editorial-intelligence/v.mp4',
    'f5000000-0000-0000-0000-000000000001')
$sql$, 'initial candidate requires full ancestry');

-- bridged candidate carrying audio lineage is rejected (CHECK + trigger).
select pg_temp.expect_rejected($sql$
  insert into public.candidate_runs
   (batch_id, project_id, user_id, preproduction_run_id, picture_edit_run_id, audio_mix_run_id,
    candidate_key, candidate_index, generation_kind, source_picture_candidate_id,
    variant_config, manifest, render_qc, preview_storage_bucket, preview_storage_path, created_by)
  values ('f5000000-0000-0000-0000-000000000061', 'f5000000-0000-0000-0000-000000000010',
    'f5000000-0000-0000-0000-000000000001', 'f5000000-0000-0000-0000-000000000020',
    'f5000000-0000-0000-0000-000000000030', 'f5000000-0000-0000-0000-000000000099',
    'bridged2', 2, 'bridged', 'pc', '{}', '{"fabricatedFootage":false}', '{}', 'exports',
    'users/f5000000-0000-0000-0000-000000000001/projects/f5000000-0000-0000-0000-000000000010/autoedit/y.mp4',
    'f5000000-0000-0000-0000-000000000001')
$sql$, 'bridged candidate must not carry audio lineage');

-- bridged preview outside the autoedit prefix is rejected (trigger).
select pg_temp.expect_rejected($sql$
  insert into public.candidate_runs
   (batch_id, project_id, user_id, preproduction_run_id, picture_edit_run_id,
    candidate_key, candidate_index, generation_kind, source_picture_candidate_id,
    variant_config, manifest, render_qc, preview_storage_bucket, preview_storage_path, created_by)
  values ('f5000000-0000-0000-0000-000000000071', 'f5000000-0000-0000-0000-000000000010',
    'f5000000-0000-0000-0000-000000000001', 'f5000000-0000-0000-0000-000000000020',
    'f5000000-0000-0000-0000-000000000030', 'bridged3', 3, 'bridged', 'pc', '{}',
    '{"fabricatedFootage":false}', '{}', 'exports',
    'users/f5000000-0000-0000-0000-000000000001/projects/f5000000-0000-0000-0000-000000000010/editorial-intelligence/z.mp4',
    'f5000000-0000-0000-0000-000000000001')
$sql$, 'bridged preview must use the autoedit prefix');

rollback;
