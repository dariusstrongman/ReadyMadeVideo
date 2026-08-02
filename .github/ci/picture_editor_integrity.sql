\set ON_ERROR_STOP on

-- Disposable PostgreSQL integrity checks for Audiovisual Milestone 2.
begin;

create or replace function pg_temp.expect_picture_rejected(statement text, label text)
returns void language plpgsql as $$
declare rejected boolean := false;
begin
  begin
    execute statement;
  exception when others then
    rejected := true;
    raise notice 'expected rejection [%]: %', label, sqlerrm;
  end;
  if not rejected then
    raise exception 'picture-editor integrity assertion did not reject: %', label;
  end if;
end $$;

insert into auth.users (id, email) values
  ('11000000-0000-0000-0000-000000000001', 'picture-owner@example.test'),
  ('11000000-0000-0000-0000-000000000002', 'picture-operator@example.test');
insert into public.operators (user_id)
values ('11000000-0000-0000-0000-000000000002');

insert into public.projects (id, user_id, name, status) values
  ('21000000-0000-0000-0000-000000000001',
   '11000000-0000-0000-0000-000000000001', 'Picture Project', 'ready'),
  ('21000000-0000-0000-0000-000000000002',
   '11000000-0000-0000-0000-000000000001', 'Other Picture Project', 'ready');

insert into public.preproduction_runs
  (id, project_id, user_id, version, status, request, creative_treatment,
   capture_quality_report, composition_by_segment, story_variants)
values
  ('31000000-0000-0000-0000-000000000001',
   '21000000-0000-0000-0000-000000000001',
   '11000000-0000-0000-0000-000000000001', 1, 'ready', '{}', '{}', '{}', '{}', '{}'),
  ('31000000-0000-0000-0000-000000000002',
   '21000000-0000-0000-0000-000000000002',
   '11000000-0000-0000-0000-000000000001', 1, 'ready', '{}', '{}', '{}', '{}', '{}');

insert into public.picture_edit_runs
  (id, project_id, user_id, preproduction_run_id, version, status, request,
   visual_rhythm_plans, candidates, selected_candidate_id)
values
  ('41000000-0000-0000-0000-000000000001',
   '21000000-0000-0000-0000-000000000001',
   '11000000-0000-0000-0000-000000000001',
   '31000000-0000-0000-0000-000000000001', 1, 'ready', '{}',
   '{"kinetic_hook":{},"treatment_arc":{},"controlled_payoff":{}}',
   '[{"candidateId":"kinetic_hook"},{"candidateId":"treatment_arc"},
     {"candidateId":"controlled_payoff"}]', 'treatment_arc');

select pg_temp.expect_picture_rejected(
  $$insert into public.picture_edit_runs
    (project_id, user_id, preproduction_run_id, version, status,
     visual_rhythm_plans, candidates)
    values ('21000000-0000-0000-0000-000000000001',
     '11000000-0000-0000-0000-000000000001',
     '31000000-0000-0000-0000-000000000001', 1, 'ready', '{}', '[]')$$,
  'duplicate project version');

select pg_temp.expect_picture_rejected(
  $$insert into public.picture_edit_runs
    (project_id, user_id, preproduction_run_id, version, status,
     visual_rhythm_plans, candidates)
    values ('21000000-0000-0000-0000-000000000001',
     '11000000-0000-0000-0000-000000000001',
     '31000000-0000-0000-0000-000000000002', 2, 'ready', '{}', '[]')$$,
  'cross-project preproduction reference');

select pg_temp.expect_picture_rejected(
  $$update public.picture_edit_runs set selected_candidate_id = 'kinetic_hook'
    where id = '41000000-0000-0000-0000-000000000001'$$,
  'immutable picture-edit update');
select pg_temp.expect_picture_rejected(
  $$delete from public.picture_edit_runs
    where id = '41000000-0000-0000-0000-000000000001'$$,
  'immutable picture-edit delete');

do $$
begin
  if not exists (
    select 1 from public.picture_edit_runs
    where id = '41000000-0000-0000-0000-000000000001'
      and jsonb_array_length(candidates) = 3
      and selected_candidate_id = 'treatment_arc'
  ) then
    raise exception 'valid picture-edit workflow did not persist';
  end if;
end $$;

rollback;
