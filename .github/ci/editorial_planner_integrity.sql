\set ON_ERROR_STOP on
begin;

-- Proves migration 0022 (Editorial Planner v1 storage) after the full chain:
-- table + versioning + RLS shape + ownership guard + status/score constraints.

do $$ begin
  if not exists (select 1 from information_schema.tables
                 where table_schema = 'public' and table_name = 'editorial_plans') then
    raise exception 'editorial_plans table is missing';
  end if;
  -- owner SELECT gated on the live parent; no authenticated write policies
  if not exists (select 1 from pg_policies
                 where tablename = 'editorial_plans' and cmd = 'SELECT'
                   and coalesce(qual, '') ilike '%project_not_deleted%') then
    raise exception 'editorial_plans owner SELECT is not gated on parent liveness';
  end if;
  if exists (select 1 from pg_policies
             where tablename = 'editorial_plans'
               and cmd in ('INSERT', 'UPDATE', 'DELETE', 'ALL')) then
    raise exception 'editorial_plans must be service-role write only';
  end if;
  if not exists (select 1 from pg_trigger
                 where tgname = 'own_project_check' and not tgisinternal
                   and tgrelid = 'public.editorial_plans'::regclass) then
    raise exception 'editorial_plans ownership trigger is missing';
  end if;
end $$;

create or replace function pg_temp.expect_rejected(statement text, label text)
returns void language plpgsql as $$
declare rejected boolean := false;
begin
  begin execute statement; exception when others then rejected := true; end;
  if not rejected then raise exception 'assertion did not reject: %', label; end if;
end $$;

insert into auth.users(id, email) values
 ('f8000000-0000-0000-0000-000000000001', 'plan-owner@example.test'),
 ('f8000000-0000-0000-0000-000000000002', 'plan-other@example.test');
insert into public.projects(id, user_id, name, status) values
 ('f8000000-0000-0000-0000-000000000010', 'f8000000-0000-0000-0000-000000000001',
  'Plan Project', 'draft_ready');

-- A valid plan row inserts.
insert into public.editorial_plans
 (id, project_id, user_id, version, status, quality_score, plan) values
 ('f8000000-0000-0000-0000-000000000020', 'f8000000-0000-0000-0000-000000000010',
  'f8000000-0000-0000-0000-000000000001', 1, 'approved', 86, '{"schemaVersion":1}');

-- (project, version) is unique.
select pg_temp.expect_rejected($sql$
  insert into public.editorial_plans
   (project_id, user_id, version, status, quality_score, plan) values
   ('f8000000-0000-0000-0000-000000000010', 'f8000000-0000-0000-0000-000000000001',
    1, 'approved', 90, '{}')
$sql$, 'duplicate editorial plan version');

-- Unknown status is rejected.
select pg_temp.expect_rejected($sql$
  insert into public.editorial_plans
   (project_id, user_id, version, status, quality_score, plan) values
   ('f8000000-0000-0000-0000-000000000010', 'f8000000-0000-0000-0000-000000000001',
    2, 'maybe', 90, '{}')
$sql$, 'invalid editorial plan status');

-- Cross-owner rows are rejected by the ownership trigger.
select pg_temp.expect_rejected($sql$
  insert into public.editorial_plans
   (project_id, user_id, version, status, quality_score, plan) values
   ('f8000000-0000-0000-0000-000000000010', 'f8000000-0000-0000-0000-000000000002',
    3, 'approved', 85, '{}')
$sql$, 'editorial plan outside project ownership');

rollback;
