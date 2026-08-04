\set ON_ERROR_STOP on
begin;

-- Proves the server-owned deletion-state guard (migration 0018) is installed after
-- the full migration chain, and that a customer (authenticated) cannot transition a
-- project into a deleted / cleanup-complete state. The trigger is the real protection
-- in Supabase (where RLS admits own-row updates); here it is asserted to exist and to
-- reject a deletion-state change made under the authenticated role.
do $$ begin
  if not exists (select 1 from pg_proc where proname = 'protect_project_deletion_state') then
    raise exception 'protect_project_deletion_state function is missing';
  end if;
  if not exists (select 1 from pg_trigger
                 where tgname = 'protect_project_deletion_state' and not tgisinternal) then
    raise exception 'protect_project_deletion_state trigger is missing';
  end if;
end $$;

insert into auth.users(id, email) values
 ('f6000000-0000-0000-0000-000000000001', 'del-owner@example.test');
insert into public.projects(id, user_id, name, status) values
 ('f6000000-0000-0000-0000-000000000010', 'f6000000-0000-0000-0000-000000000001', 'Del Project', 'ready');

-- Under the authenticated role, changing deletion state must NOT succeed (the trigger
-- raises; RLS in production also gates the row). Assert the state stays untouched.
create or replace function auth.uid() returns uuid language sql stable as
  $f$ select 'f6000000-0000-0000-0000-000000000001'::uuid $f$;
do $$
begin
  set local role authenticated;
  begin
    update public.projects set deleted_at = now()
      where id = 'f6000000-0000-0000-0000-000000000010';
  exception when others then null;   -- trigger/permission rejection is expected
  end;
  reset role;
end $$;
reset role;
do $$ begin
  if (select deleted_at from public.projects
      where id = 'f6000000-0000-0000-0000-000000000010') is not null then
    raise exception 'customer was able to set project deletion state';
  end if;
end $$;

rollback;
