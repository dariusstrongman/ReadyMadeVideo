\set ON_ERROR_STOP on

-- Destructive integrity exercise against the disposable CI PostgreSQL service.
-- This file is never pointed at Supabase or any production database.
begin;

create or replace function pg_temp.expect_rejected(statement text, label text)
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
    raise exception 'integrity assertion did not reject: %', label;
  end if;
end $$;

insert into auth.users (id, email) values
  ('10000000-0000-0000-0000-000000000001', 'owner@example.test'),
  ('10000000-0000-0000-0000-000000000002', 'other@example.test'),
  ('10000000-0000-0000-0000-000000000003', 'operator@example.test');
insert into public.operators (user_id)
values ('10000000-0000-0000-0000-000000000003');

insert into public.projects (id, user_id, name, status) values
  ('20000000-0000-0000-0000-000000000001',
   '10000000-0000-0000-0000-000000000001', 'Project One', 'ready'),
  ('20000000-0000-0000-0000-000000000002',
   '10000000-0000-0000-0000-000000000001', 'Other Project', 'ready');

insert into public.timelines
  (id, project_id, user_id, version, timeline_json, lineage, is_immutable)
values
  ('30000000-0000-0000-0000-000000000001',
   '20000000-0000-0000-0000-000000000001',
   '10000000-0000-0000-0000-000000000001', 1, '{"tracks":[]}',
   'autonomous_initial', true),
  ('30000000-0000-0000-0000-000000000002',
   '20000000-0000-0000-0000-000000000001',
   '10000000-0000-0000-0000-000000000001', 2, '{"tracks":[]}',
   'autonomous_revised', true),
  ('30000000-0000-0000-0000-000000000003',
   '20000000-0000-0000-0000-000000000001',
   '10000000-0000-0000-0000-000000000001', 3, '{"tracks":[]}',
   'human_draft', false),
  ('30000000-0000-0000-0000-000000000004',
   '20000000-0000-0000-0000-000000000002',
   '10000000-0000-0000-0000-000000000001', 1, '{"tracks":[]}',
   'legacy', false);

insert into public.edit_runs
  (id, project_id, user_id, status, timeline_v1_id, timeline_v2_id)
values
  ('40000000-0000-0000-0000-000000000001',
   '20000000-0000-0000-0000-000000000001',
   '10000000-0000-0000-0000-000000000001', 'completed',
   '30000000-0000-0000-0000-000000000001',
   '30000000-0000-0000-0000-000000000002');
update public.timelines
set edit_run_id = '40000000-0000-0000-0000-000000000001'
where id = '30000000-0000-0000-0000-000000000003';

select pg_temp.expect_rejected(
  $$update public.timelines set timeline_json = '{"changed":true}'
    where id = '30000000-0000-0000-0000-000000000001'$$,
  'immutable autonomous UPDATE');
select pg_temp.expect_rejected(
  $$delete from public.timelines
    where id = '30000000-0000-0000-0000-000000000001'$$,
  'immutable autonomous DELETE');

select pg_temp.expect_rejected(
  $$insert into public.human_edit_sessions
    (id, project_id, user_id, operator_user_id,
     autonomous_initial_timeline_id, current_timeline_id)
    values ('50000000-0000-0000-0000-000000000091',
     '20000000-0000-0000-0000-000000000001',
     '10000000-0000-0000-0000-000000000002',
     '10000000-0000-0000-0000-000000000003',
     '30000000-0000-0000-0000-000000000001',
     '30000000-0000-0000-0000-000000000003')$$,
  'cross-user session');
select pg_temp.expect_rejected(
  $$insert into public.human_edit_sessions
    (id, project_id, user_id, operator_user_id,
     autonomous_initial_timeline_id, current_timeline_id)
    values ('50000000-0000-0000-0000-000000000092',
     '20000000-0000-0000-0000-000000000002',
     '10000000-0000-0000-0000-000000000001',
     '10000000-0000-0000-0000-000000000003',
     '30000000-0000-0000-0000-000000000001',
     '30000000-0000-0000-0000-000000000004')$$,
  'cross-project session');

insert into public.human_edit_sessions
  (id, project_id, user_id, edit_run_id, operator_user_id,
   autonomous_initial_timeline_id, autonomous_revised_timeline_id,
   current_timeline_id)
values
  ('50000000-0000-0000-0000-000000000001',
   '20000000-0000-0000-0000-000000000001',
   '10000000-0000-0000-0000-000000000001',
   '40000000-0000-0000-0000-000000000001',
   '10000000-0000-0000-0000-000000000003',
   '30000000-0000-0000-0000-000000000001',
   '30000000-0000-0000-0000-000000000002',
   '30000000-0000-0000-0000-000000000003');

select pg_temp.expect_rejected(
  $$insert into public.human_edit_sessions
    (project_id, user_id, operator_user_id,
     autonomous_initial_timeline_id, current_timeline_id)
    values ('20000000-0000-0000-0000-000000000001',
     '10000000-0000-0000-0000-000000000001',
     '10000000-0000-0000-0000-000000000003',
     '30000000-0000-0000-0000-000000000001',
     '30000000-0000-0000-0000-000000000003')$$,
  'duplicate active sessions');

insert into public.human_edit_timing_events
  (project_id, user_id, human_edit_session_id, event_type, operator_user_id)
values
  ('20000000-0000-0000-0000-000000000001',
   '10000000-0000-0000-0000-000000000001',
   '50000000-0000-0000-0000-000000000001', 'start',
   '10000000-0000-0000-0000-000000000003');

insert into public.user_corrections
  (id, project_id, user_id, original_timeline_version, requested_change,
   applied_operations, accepted, final_timeline_version, human_edit_session_id,
   base_timeline_id, result_timeline_id, operation_index, correction_type,
   server_measured_seconds, client_reported_seconds, operator_user_id)
values
  ('60000000-0000-0000-0000-000000000001',
   '20000000-0000-0000-0000-000000000001',
   '10000000-0000-0000-0000-000000000001', 2, 'trim',
   '[{"op":"trim_clip"}]', true, 3,
   '50000000-0000-0000-0000-000000000001',
   '30000000-0000-0000-0000-000000000002',
   '30000000-0000-0000-0000-000000000003', 1, 'trim', 4, 99,
   '10000000-0000-0000-0000-000000000003');

select pg_temp.expect_rejected(
  $$insert into public.user_corrections
    (project_id, user_id, original_timeline_version, requested_change,
     applied_operations, accepted, human_edit_session_id, base_timeline_id,
     result_timeline_id, operation_index, correction_type)
    values ('20000000-0000-0000-0000-000000000001',
     '10000000-0000-0000-0000-000000000001', 2, 'duplicate', '[]', true,
     '50000000-0000-0000-0000-000000000001',
     '30000000-0000-0000-0000-000000000002',
     '30000000-0000-0000-0000-000000000003', 1, 'trim')$$,
  'duplicate operation indexes');
select pg_temp.expect_rejected(
  $$insert into public.user_corrections
    (project_id, user_id, original_timeline_version, requested_change,
     applied_operations, accepted, human_edit_session_id, base_timeline_id,
     result_timeline_id, operation_index, correction_type)
    values ('20000000-0000-0000-0000-000000000001',
     '10000000-0000-0000-0000-000000000001', 2, 'cross project', '[]', true,
     '50000000-0000-0000-0000-000000000001',
     '30000000-0000-0000-0000-000000000004',
     '30000000-0000-0000-0000-000000000003', 2, 'trim')$$,
  'cross-project corrections');

select pg_temp.expect_rejected(
  $$insert into public.timeline_scorecards
    (project_id, user_id, timeline_id, human_edit_session_id,
     evaluator_user_id, overall_rating)
    values ('20000000-0000-0000-0000-000000000001',
     '10000000-0000-0000-0000-000000000001',
     '30000000-0000-0000-0000-000000000004',
     '50000000-0000-0000-0000-000000000001',
     '10000000-0000-0000-0000-000000000003', 5)$$,
  'cross-project scorecards');

insert into public.human_edit_timing_events
  (project_id, user_id, human_edit_session_id, event_type, operation_index,
   operator_user_id)
values
  ('20000000-0000-0000-0000-000000000001',
   '10000000-0000-0000-0000-000000000001',
   '50000000-0000-0000-0000-000000000001', 'operation', 1,
   '10000000-0000-0000-0000-000000000003'),
  ('20000000-0000-0000-0000-000000000001',
   '10000000-0000-0000-0000-000000000001',
   '50000000-0000-0000-0000-000000000001', 'approve', null,
   '10000000-0000-0000-0000-000000000003');

update public.timelines
set lineage = 'human_approved', is_immutable = true,
    approved_by = '10000000-0000-0000-0000-000000000003',
    approved_at = clock_timestamp()
where id = '30000000-0000-0000-0000-000000000003';
update public.human_edit_sessions
set status = 'approved', timing_state = 'closed', server_measured_seconds = 4,
    human_correction_seconds = 4,
    approved_timeline_id = '30000000-0000-0000-0000-000000000003',
    approved_at = clock_timestamp()
where id = '50000000-0000-0000-0000-000000000001';
insert into public.timeline_scorecards
  (project_id, user_id, timeline_id, human_edit_session_id,
   evaluator_user_id, overall_rating, server_measured_seconds)
values
  ('20000000-0000-0000-0000-000000000001',
   '10000000-0000-0000-0000-000000000001',
   '30000000-0000-0000-0000-000000000003',
   '50000000-0000-0000-0000-000000000001',
   '10000000-0000-0000-0000-000000000003', 8, 4);

select pg_temp.expect_rejected(
  $$update public.timelines set approved_at = clock_timestamp()
    where id = '30000000-0000-0000-0000-000000000003'$$,
  'immutable human-approved UPDATE/provenance');
select pg_temp.expect_rejected(
  $$delete from public.timelines
    where id = '30000000-0000-0000-0000-000000000003'$$,
  'immutable human-approved DELETE');

do $$
begin
  if not exists (
    select 1 from public.human_edit_sessions
    where id = '50000000-0000-0000-0000-000000000001'
      and status = 'approved' and server_measured_seconds = 4
  ) then
    raise exception 'valid human-ceiling workflow did not persist';
  end if;
end $$;

rollback;
