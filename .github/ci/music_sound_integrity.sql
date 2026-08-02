\set ON_ERROR_STOP on
begin;

create or replace function pg_temp.expect_music_rejected(statement text, label text)
returns void language plpgsql as $$
declare rejected boolean := false;
begin
  begin execute statement;
  exception when others then
    rejected := true;
    raise notice 'expected rejection [%]: %', label, sqlerrm;
  end;
  if not rejected then
    raise exception 'music-sound integrity assertion did not reject: %', label;
  end if;
end $$;

insert into auth.users (id, email) values
  ('12000000-0000-0000-0000-000000000001', 'music-owner@example.test'),
  ('12000000-0000-0000-0000-000000000002', 'music-other@example.test');

insert into public.projects (id, user_id, name, status) values
  ('22000000-0000-0000-0000-000000000001',
   '12000000-0000-0000-0000-000000000001', 'Music Project', 'ready'),
  ('22000000-0000-0000-0000-000000000002',
   '12000000-0000-0000-0000-000000000001', 'Other Music Project', 'ready'),
  ('22000000-0000-0000-0000-000000000003',
   '12000000-0000-0000-0000-000000000002', 'Foreign Music Project', 'ready');

insert into public.preproduction_runs
  (id, project_id, user_id, version, status, request, creative_treatment,
   capture_quality_report, composition_by_segment, story_variants)
values
  ('32000000-0000-0000-0000-000000000001',
   '22000000-0000-0000-0000-000000000001',
   '12000000-0000-0000-0000-000000000001', 1, 'ready', '{}', '{}', '{}', '{}', '{}'),
  ('32000000-0000-0000-0000-000000000002',
   '22000000-0000-0000-0000-000000000002',
   '12000000-0000-0000-0000-000000000001', 1, 'ready', '{}', '{}', '{}', '{}', '{}');

insert into public.picture_edit_runs
  (id, project_id, user_id, preproduction_run_id, version, status, request,
   visual_rhythm_plans, candidates, selected_candidate_id)
values
  ('42000000-0000-0000-0000-000000000001',
   '22000000-0000-0000-0000-000000000001',
   '12000000-0000-0000-0000-000000000001',
   '32000000-0000-0000-0000-000000000001', 1, 'ready', '{}', '{}',
   '[{"candidateId":"treatment_arc"}]', 'treatment_arc'),
  ('42000000-0000-0000-0000-000000000002',
   '22000000-0000-0000-0000-000000000002',
   '12000000-0000-0000-0000-000000000001',
   '32000000-0000-0000-0000-000000000002', 1, 'ready', '{}', '{}',
   '[{"candidateId":"kinetic_hook"}]', 'kinetic_hook');

insert into public.music_sound_runs
  (id, project_id, user_id, preproduction_run_id, picture_edit_run_id,
   selected_candidate_id, version, status, request, music_plan)
values
  ('52000000-0000-0000-0000-000000000001',
   '22000000-0000-0000-0000-000000000001',
   '12000000-0000-0000-0000-000000000001',
   '32000000-0000-0000-0000-000000000001',
   '42000000-0000-0000-0000-000000000001',
   'treatment_arc', 1, 'ready', '{}', '{"schemaVersion":1}');

select pg_temp.expect_music_rejected(
  $$insert into public.music_sound_runs
    (project_id,user_id,preproduction_run_id,picture_edit_run_id,
     selected_candidate_id,version,status,music_plan) values
    ('22000000-0000-0000-0000-000000000001','12000000-0000-0000-0000-000000000001',
     '32000000-0000-0000-0000-000000000001','42000000-0000-0000-0000-000000000001',
     'treatment_arc',1,'ready','{}')$$, 'duplicate project version');

select pg_temp.expect_music_rejected(
  $$insert into public.music_sound_runs
    (project_id,user_id,preproduction_run_id,picture_edit_run_id,
     selected_candidate_id,version,status,music_plan) values
    ('22000000-0000-0000-0000-000000000001','12000000-0000-0000-0000-000000000001',
     '32000000-0000-0000-0000-000000000001','42000000-0000-0000-0000-000000000002',
     'kinetic_hook',2,'ready','{}')$$, 'cross-project picture reference');

select pg_temp.expect_music_rejected(
  $$insert into public.music_sound_runs
    (project_id,user_id,preproduction_run_id,picture_edit_run_id,
     selected_candidate_id,version,status,music_plan) values
    ('22000000-0000-0000-0000-000000000001','12000000-0000-0000-0000-000000000001',
     '32000000-0000-0000-0000-000000000002','42000000-0000-0000-0000-000000000001',
     'treatment_arc',2,'ready','{}')$$, 'cross-project preproduction reference');

select pg_temp.expect_music_rejected(
  $$insert into public.music_sound_runs
    (project_id,user_id,preproduction_run_id,picture_edit_run_id,
     selected_candidate_id,version,status,music_plan) values
    ('22000000-0000-0000-0000-000000000001','12000000-0000-0000-0000-000000000001',
     '32000000-0000-0000-0000-000000000001','42000000-0000-0000-0000-000000000001',
     'controlled_payoff',2,'ready','{}')$$, 'non-selected picture candidate');

select pg_temp.expect_music_rejected(
  $$insert into public.music_sound_runs
    (project_id,user_id,preproduction_run_id,picture_edit_run_id,
     selected_candidate_id,version,status,music_plan) values
    ('22000000-0000-0000-0000-000000000001','12000000-0000-0000-0000-000000000002',
     '32000000-0000-0000-0000-000000000001','42000000-0000-0000-0000-000000000001',
     'treatment_arc',2,'ready','{}')$$, 'cross-user reference');

select pg_temp.expect_music_rejected(
  $$update public.music_sound_runs set status='ready'
    where id='52000000-0000-0000-0000-000000000001'$$, 'immutable update');
select pg_temp.expect_music_rejected(
  $$delete from public.music_sound_runs
    where id='52000000-0000-0000-0000-000000000001'$$, 'immutable delete');

do $$ begin
  if not exists (select 1 from public.music_sound_runs
    where id='52000000-0000-0000-0000-000000000001'
      and selected_candidate_id='treatment_arc'
      and music_plan->>'schemaVersion'='1') then
    raise exception 'valid music-sound workflow did not persist';
  end if;
end $$;

rollback;
