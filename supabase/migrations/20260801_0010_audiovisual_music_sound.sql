-- Full audiovisual editor, Milestone 3: immutable music and sound plans.
-- Additive only. Existing timelines and milestone evidence remain untouched.

create table if not exists public.music_sound_runs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  preproduction_run_id uuid not null
    references public.preproduction_runs(id) on delete restrict,
  picture_edit_run_id uuid not null
    references public.picture_edit_runs(id) on delete restrict,
  selected_candidate_id text not null,
  version integer not null check (version > 0),
  status text not null check (status = 'ready'),
  request jsonb not null default '{}'::jsonb,
  music_plan jsonb not null,
  created_at timestamptz not null default clock_timestamp(),
  unique (project_id, version)
);

create index if not exists music_sound_runs_project_idx
  on public.music_sound_runs(project_id, version desc);

alter table public.music_sound_runs enable row level security;
drop policy if exists music_sound_runs_select_own on public.music_sound_runs;
create policy music_sound_runs_select_own on public.music_sound_runs
  for select to authenticated using (user_id = auth.uid());
drop policy if exists operator_read on public.music_sound_runs;
create policy operator_read on public.music_sound_runs
  for select to authenticated using (public.is_operator());
-- Writes remain service-role-only through the authenticated, audited API.

drop trigger if exists own_project_check on public.music_sound_runs;
create trigger own_project_check before insert or update on public.music_sound_runs
  for each row execute function public.enforce_project_ownership();

create or replace function public.enforce_music_sound_refs()
returns trigger language plpgsql security definer set search_path = public as $$
declare preprod record;
declare picture record;
begin
  select project_id, user_id into preprod from public.preproduction_runs
    where id = new.preproduction_run_id;
  select project_id, user_id, preproduction_run_id, selected_candidate_id
    into picture from public.picture_edit_runs where id = new.picture_edit_run_id;
  if not found then
    raise exception 'picture-edit run does not exist';
  end if;
  if preprod.project_id is null
     or preprod.project_id <> new.project_id or preprod.user_id <> new.user_id then
    raise exception 'preproduction run is outside the music-sound project/user';
  end if;
  if picture.project_id <> new.project_id or picture.user_id <> new.user_id
     or picture.preproduction_run_id <> new.preproduction_run_id then
    raise exception 'picture-edit ancestry is outside the music-sound project/user';
  end if;
  if picture.selected_candidate_id is null
     or picture.selected_candidate_id <> new.selected_candidate_id then
    raise exception 'music-sound plan must reference the selected picture candidate';
  end if;
  return new;
end $$;

drop trigger if exists music_sound_refs_check on public.music_sound_runs;
create trigger music_sound_refs_check before insert or update on public.music_sound_runs
  for each row execute function public.enforce_music_sound_refs();

create or replace function public.protect_music_sound_evidence()
returns trigger language plpgsql set search_path = public as $$
begin
  raise exception 'music-sound run % is immutable evidence', old.id;
end $$;

drop trigger if exists protect_music_sound_evidence on public.music_sound_runs;
create trigger protect_music_sound_evidence
  before update or delete on public.music_sound_runs
  for each row execute function public.protect_music_sound_evidence();
