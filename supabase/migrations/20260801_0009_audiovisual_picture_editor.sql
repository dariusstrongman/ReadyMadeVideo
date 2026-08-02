-- Full audiovisual editor, Milestone 2: deterministic picture-edit candidates.
-- Additive only. Existing timelines and Project One baseline artifacts are untouched.

create table if not exists public.picture_edit_runs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  preproduction_run_id uuid not null
    references public.preproduction_runs(id) on delete restrict,
  version integer not null check (version > 0),
  status text not null check (status in ('ready','insufficient_coverage')),
  request jsonb not null default '{}'::jsonb,
  visual_rhythm_plans jsonb not null,
  candidates jsonb not null,
  selected_candidate_id text,
  warnings jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default clock_timestamp(),
  unique (project_id, version)
);

create index if not exists picture_edit_runs_project_idx
  on public.picture_edit_runs(project_id, version desc);

alter table public.picture_edit_runs enable row level security;
drop policy if exists picture_edit_runs_select_own on public.picture_edit_runs;
create policy picture_edit_runs_select_own on public.picture_edit_runs
  for select to authenticated using (user_id = auth.uid());
drop policy if exists operator_read on public.picture_edit_runs;
create policy operator_read on public.picture_edit_runs
  for select to authenticated using (public.is_operator());
-- Writes remain service-role-only through the authenticated, audited API.

drop trigger if exists own_project_check on public.picture_edit_runs;
create trigger own_project_check before insert or update on public.picture_edit_runs
  for each row execute function public.enforce_project_ownership();

create or replace function public.enforce_picture_edit_refs()
returns trigger language plpgsql security definer set search_path = public as $$
declare ref record;
begin
  select project_id, user_id into ref from public.preproduction_runs
    where id = new.preproduction_run_id;
  if not found or ref.project_id <> new.project_id or ref.user_id <> new.user_id then
    raise exception 'preproduction run is outside the picture-edit project/user';
  end if;
  return new;
end $$;

drop trigger if exists picture_edit_refs_check on public.picture_edit_runs;
create trigger picture_edit_refs_check before insert or update on public.picture_edit_runs
  for each row execute function public.enforce_picture_edit_refs();

create or replace function public.protect_picture_edit_evidence()
returns trigger language plpgsql set search_path = public as $$
begin
  raise exception 'picture-edit run % is immutable evidence', old.id;
end $$;

drop trigger if exists protect_picture_edit_evidence on public.picture_edit_runs;
create trigger protect_picture_edit_evidence
  before update or delete on public.picture_edit_runs
  for each row execute function public.protect_picture_edit_evidence();
