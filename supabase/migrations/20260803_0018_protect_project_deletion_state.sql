-- Migration 0018: server-owned project deletion state.
--
-- Migration 0017's projects_all_own (FOR ALL, with check user_id = auth.uid()) lets an
-- authenticated customer directly UPDATE their own row to set deleted_at /
-- deleted_cleanup_done, bypassing the server-authorized deletion + storage-cleanup
-- workflow. This trigger rejects any change to those columns from the customer roles;
-- only the service role (the backend DELETE endpoint) may transition deletion state.
create or replace function public.protect_project_deletion_state()
returns trigger language plpgsql as $$
begin
  if (new.deleted_at is distinct from old.deleted_at
      or new.deleted_cleanup_done is distinct from old.deleted_cleanup_done)
     and current_user in ('authenticated', 'anon') then
    raise exception 'project deletion state is managed by the server';
  end if;
  return new;
end $$;

drop trigger if exists protect_project_deletion_state on public.projects;
create trigger protect_project_deletion_state
  before update on public.projects
  for each row execute function public.protect_project_deletion_state();
