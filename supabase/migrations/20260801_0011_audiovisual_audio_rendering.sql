-- Full audiovisual editor, Milestone 4: licensed music and completed audio previews.
-- Additive only. Picture timing and all prior milestone evidence remain immutable.

create table if not exists public.licensed_music_assets (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  music_sound_run_id uuid not null references public.music_sound_runs(id) on delete restrict,
  picture_edit_run_id uuid not null references public.picture_edit_runs(id) on delete restrict,
  selected_candidate_id text not null,
  version integer not null check (version > 0),
  storage_bucket text not null check (storage_bucket = 'raw-footage'),
  storage_path text not null,
  filename text not null,
  content_type text not null,
  size_bytes bigint not null check (size_bytes > 0 and size_bytes <= 52428800),
  license_metadata jsonb not null,
  media_info jsonb not null,
  waveform_analysis jsonb not null,
  attached_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default clock_timestamp(),
  unique (music_sound_run_id, version)
);

create table if not exists public.audio_mix_runs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  preproduction_run_id uuid not null references public.preproduction_runs(id) on delete restrict,
  picture_edit_run_id uuid not null references public.picture_edit_runs(id) on delete restrict,
  music_sound_run_id uuid not null references public.music_sound_runs(id) on delete restrict,
  licensed_music_asset_id uuid not null references public.licensed_music_assets(id) on delete restrict,
  selected_candidate_id text not null,
  version integer not null check (version > 0),
  status text not null check (status in ('qc_passed','qc_failed')),
  target_vs_actual jsonb not null,
  mix_instructions jsonb not null,
  audio_qc jsonb not null,
  preview_storage_bucket text not null check (preview_storage_bucket = 'exports'),
  preview_storage_path text not null,
  picture_timing_changed boolean not null default false check (not picture_timing_changed),
  created_at timestamptz not null default clock_timestamp(),
  unique (project_id, version)
);

create index if not exists licensed_music_assets_project_idx
  on public.licensed_music_assets(project_id, created_at desc);
create index if not exists audio_mix_runs_project_idx
  on public.audio_mix_runs(project_id, version desc);

alter table public.licensed_music_assets enable row level security;
alter table public.audio_mix_runs enable row level security;
drop policy if exists licensed_music_select_own on public.licensed_music_assets;
create policy licensed_music_select_own on public.licensed_music_assets
  for select to authenticated using (user_id = auth.uid());
drop policy if exists operator_read on public.licensed_music_assets;
create policy operator_read on public.licensed_music_assets
  for select to authenticated using (public.is_operator());
drop policy if exists audio_mix_select_own on public.audio_mix_runs;
create policy audio_mix_select_own on public.audio_mix_runs
  for select to authenticated using (user_id = auth.uid());
drop policy if exists operator_read on public.audio_mix_runs;
create policy operator_read on public.audio_mix_runs
  for select to authenticated using (public.is_operator());

drop trigger if exists own_project_check on public.licensed_music_assets;
create trigger own_project_check before insert or update on public.licensed_music_assets
  for each row execute function public.enforce_project_ownership();
drop trigger if exists own_project_check on public.audio_mix_runs;
create trigger own_project_check before insert or update on public.audio_mix_runs
  for each row execute function public.enforce_project_ownership();

create or replace function public.enforce_licensed_music_refs()
returns trigger language plpgsql security definer set search_path = public as $$
declare music record;
begin
  select project_id, user_id, picture_edit_run_id, selected_candidate_id into music
    from public.music_sound_runs where id = new.music_sound_run_id;
  if not found or music.project_id <> new.project_id or music.user_id <> new.user_id
     or music.picture_edit_run_id <> new.picture_edit_run_id
     or music.selected_candidate_id <> new.selected_candidate_id then
    raise exception 'licensed music ancestry is outside the project/selected picture';
  end if;
  if new.storage_path not like
     'users/' || new.user_id::text || '/projects/' || new.project_id::text ||
     '/licensed-music/%' then
    raise exception 'licensed music storage path is outside project ownership';
  end if;
  if coalesce(new.license_metadata->>'confirmedByOperator','false') <> 'true'
     or coalesce(new.license_metadata->>'licenseReference','') = '' then
    raise exception 'licensed music metadata is incomplete';
  end if;
  if new.waveform_analysis->>'analysisSource' <> 'actual_waveform' then
    raise exception 'licensed music requires actual waveform analysis';
  end if;
  return new;
end $$;

create or replace function public.enforce_audio_mix_refs()
returns trigger language plpgsql security definer set search_path = public as $$
declare music record;
declare licensed record;
begin
  select project_id, user_id, preproduction_run_id, picture_edit_run_id,
         selected_candidate_id into music
    from public.music_sound_runs where id = new.music_sound_run_id;
  select project_id, user_id, music_sound_run_id, picture_edit_run_id,
         selected_candidate_id into licensed
    from public.licensed_music_assets where id = new.licensed_music_asset_id;
  if music.project_id is null or music.project_id <> new.project_id
     or music.user_id <> new.user_id
     or music.preproduction_run_id <> new.preproduction_run_id
     or music.picture_edit_run_id <> new.picture_edit_run_id
     or music.selected_candidate_id <> new.selected_candidate_id then
    raise exception 'audio mix music-plan ancestry is invalid';
  end if;
  if licensed.project_id is null or licensed.project_id <> new.project_id
     or licensed.user_id <> new.user_id
     or licensed.music_sound_run_id <> new.music_sound_run_id
     or licensed.picture_edit_run_id <> new.picture_edit_run_id
     or licensed.selected_candidate_id <> new.selected_candidate_id then
    raise exception 'audio mix licensed-track ancestry is invalid';
  end if;
  if new.preview_storage_path not like
     'users/' || new.user_id::text || '/projects/' || new.project_id::text || '/%' then
    raise exception 'audio preview storage path is outside project ownership';
  end if;
  return new;
end $$;

drop trigger if exists licensed_music_refs_check on public.licensed_music_assets;
create trigger licensed_music_refs_check before insert or update on public.licensed_music_assets
  for each row execute function public.enforce_licensed_music_refs();
drop trigger if exists audio_mix_refs_check on public.audio_mix_runs;
create trigger audio_mix_refs_check before insert or update on public.audio_mix_runs
  for each row execute function public.enforce_audio_mix_refs();

create or replace function public.protect_audio_render_evidence()
returns trigger language plpgsql set search_path = public as $$
begin
  raise exception 'audio-render evidence % is immutable', old.id;
end $$;

drop trigger if exists protect_audio_render_evidence on public.licensed_music_assets;
create trigger protect_audio_render_evidence before update or delete on public.licensed_music_assets
  for each row execute function public.protect_audio_render_evidence();
drop trigger if exists protect_audio_render_evidence on public.audio_mix_runs;
create trigger protect_audio_render_evidence before update or delete on public.audio_mix_runs
  for each row execute function public.protect_audio_render_evidence();
