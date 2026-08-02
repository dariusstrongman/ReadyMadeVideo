-- Audiovisual editor Milestone 5: immutable visual-finishing evidence.
-- Additive only; the completed audio preview and selected picture remain locked.

create table if not exists public.graphics_runs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  preproduction_run_id uuid not null references public.preproduction_runs(id) on delete restrict,
  picture_edit_run_id uuid not null references public.picture_edit_runs(id) on delete restrict,
  music_sound_run_id uuid not null references public.music_sound_runs(id) on delete restrict,
  audio_mix_run_id uuid not null references public.audio_mix_runs(id) on delete restrict,
  selected_candidate_id text not null,
  version integer not null check (version > 0),
  status text not null check (status in ('ready','render_failed')),
  request jsonb not null,
  platform_preset jsonb not null,
  brand_template jsonb not null,
  graphics_timeline jsonb not null,
  picture_timing_changed boolean not null default false check (not picture_timing_changed),
  audio_changed boolean not null default false check (not audio_changed),
  created_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default clock_timestamp(),
  unique (project_id, version)
);

create table if not exists public.caption_runs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  preproduction_run_id uuid not null references public.preproduction_runs(id) on delete restrict,
  picture_edit_run_id uuid not null references public.picture_edit_runs(id) on delete restrict,
  music_sound_run_id uuid not null references public.music_sound_runs(id) on delete restrict,
  audio_mix_run_id uuid not null references public.audio_mix_runs(id) on delete restrict,
  graphics_run_id uuid not null references public.graphics_runs(id) on delete restrict,
  selected_candidate_id text not null,
  version integer not null check (version > 0),
  status text not null check (status in ('ready','no_speech')),
  caption_timeline jsonb not null,
  timing_provenance jsonb not null,
  overlaps_detected integer not null default 0 check (overlaps_detected = 0),
  picture_timing_changed boolean not null default false check (not picture_timing_changed),
  created_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default clock_timestamp(),
  unique (project_id, version)
);

create table if not exists public.color_runs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  preproduction_run_id uuid not null references public.preproduction_runs(id) on delete restrict,
  picture_edit_run_id uuid not null references public.picture_edit_runs(id) on delete restrict,
  music_sound_run_id uuid not null references public.music_sound_runs(id) on delete restrict,
  audio_mix_run_id uuid not null references public.audio_mix_runs(id) on delete restrict,
  graphics_run_id uuid not null references public.graphics_runs(id) on delete restrict,
  caption_run_id uuid not null references public.caption_runs(id) on delete restrict,
  selected_candidate_id text not null,
  version integer not null check (version > 0),
  status text not null check (status in ('qc_passed','qc_failed')),
  color_instructions jsonb not null,
  render_qc jsonb not null,
  preview_storage_bucket text not null check (preview_storage_bucket = 'exports'),
  preview_storage_path text not null,
  non_destructive boolean not null default true check (non_destructive),
  picture_timing_changed boolean not null default false check (not picture_timing_changed),
  audio_changed boolean not null default false check (not audio_changed),
  created_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default clock_timestamp(),
  unique (project_id, version)
);

create index if not exists graphics_runs_project_idx on public.graphics_runs(project_id, version desc);
create index if not exists caption_runs_project_idx on public.caption_runs(project_id, version desc);
create index if not exists color_runs_project_idx on public.color_runs(project_id, version desc);

alter table public.graphics_runs enable row level security;
alter table public.caption_runs enable row level security;
alter table public.color_runs enable row level security;

do $$
declare table_name text;
begin
  foreach table_name in array array['graphics_runs','caption_runs','color_runs'] loop
    execute format('drop policy if exists %I on public.%I', table_name || '_select_own', table_name);
    execute format('create policy %I on public.%I for select to authenticated using (user_id = auth.uid())', table_name || '_select_own', table_name);
    execute format('drop policy if exists operator_read on public.%I', table_name);
    execute format('create policy operator_read on public.%I for select to authenticated using (public.is_operator())', table_name);
    execute format('drop trigger if exists own_project_check on public.%I', table_name);
    execute format('create trigger own_project_check before insert or update on public.%I for each row execute function public.enforce_project_ownership()', table_name);
  end loop;
end $$;

create or replace function public.enforce_graphics_refs()
returns trigger language plpgsql security definer set search_path = public as $$
declare audio record;
begin
  select project_id, user_id, preproduction_run_id, picture_edit_run_id,
         music_sound_run_id, selected_candidate_id into audio
    from public.audio_mix_runs where id = new.audio_mix_run_id;
  if audio.project_id is null or audio.project_id <> new.project_id
     or audio.user_id <> new.user_id
     or audio.preproduction_run_id <> new.preproduction_run_id
     or audio.picture_edit_run_id <> new.picture_edit_run_id
     or audio.music_sound_run_id <> new.music_sound_run_id
     or audio.selected_candidate_id <> new.selected_candidate_id then
    raise exception 'graphics ancestry is outside the completed audio/picture lineage';
  end if;
  if coalesce(new.graphics_timeline->>'pictureTimingChanged','true') <> 'false'
     or coalesce(new.graphics_timeline->>'audioChanged','true') <> 'false' then
    raise exception 'graphics may not change locked picture or audio';
  end if;
  return new;
end $$;

create or replace function public.enforce_caption_refs()
returns trigger language plpgsql security definer set search_path = public as $$
declare graphics record;
begin
  select project_id, user_id, preproduction_run_id, picture_edit_run_id,
         music_sound_run_id, audio_mix_run_id, selected_candidate_id into graphics
    from public.graphics_runs where id = new.graphics_run_id;
  if graphics.project_id is null or graphics.project_id <> new.project_id
     or graphics.user_id <> new.user_id
     or graphics.preproduction_run_id <> new.preproduction_run_id
     or graphics.picture_edit_run_id <> new.picture_edit_run_id
     or graphics.music_sound_run_id <> new.music_sound_run_id
     or graphics.audio_mix_run_id <> new.audio_mix_run_id
     or graphics.selected_candidate_id <> new.selected_candidate_id then
    raise exception 'caption ancestry is outside the graphics lineage';
  end if;
  return new;
end $$;

create or replace function public.enforce_color_refs()
returns trigger language plpgsql security definer set search_path = public as $$
declare graphics record;
declare captions record;
begin
  select project_id, user_id, preproduction_run_id, picture_edit_run_id,
         music_sound_run_id, audio_mix_run_id, selected_candidate_id into graphics
    from public.graphics_runs where id = new.graphics_run_id;
  select project_id, user_id, preproduction_run_id, picture_edit_run_id,
         music_sound_run_id, audio_mix_run_id, graphics_run_id,
         selected_candidate_id into captions
    from public.caption_runs where id = new.caption_run_id;
  if graphics.project_id is null or graphics.project_id <> new.project_id
     or graphics.user_id <> new.user_id
     or graphics.preproduction_run_id <> new.preproduction_run_id
     or graphics.picture_edit_run_id <> new.picture_edit_run_id
     or graphics.music_sound_run_id <> new.music_sound_run_id
     or graphics.audio_mix_run_id <> new.audio_mix_run_id
     or graphics.selected_candidate_id <> new.selected_candidate_id
     or captions.project_id is null or captions.project_id <> new.project_id
     or captions.user_id <> new.user_id
     or captions.preproduction_run_id <> new.preproduction_run_id
     or captions.picture_edit_run_id <> new.picture_edit_run_id
     or captions.music_sound_run_id <> new.music_sound_run_id
     or captions.audio_mix_run_id <> new.audio_mix_run_id
     or captions.graphics_run_id <> new.graphics_run_id
     or captions.selected_candidate_id <> new.selected_candidate_id then
    raise exception 'color ancestry is outside the graphics/caption lineage';
  end if;
  if new.preview_storage_path not like
     'users/' || new.user_id::text || '/projects/' || new.project_id::text || '/visual-finishing/%' then
    raise exception 'visual-finishing preview path is outside project ownership';
  end if;
  return new;
end $$;

create trigger graphics_refs_check before insert or update on public.graphics_runs
  for each row execute function public.enforce_graphics_refs();
create trigger caption_refs_check before insert or update on public.caption_runs
  for each row execute function public.enforce_caption_refs();
create trigger color_refs_check before insert or update on public.color_runs
  for each row execute function public.enforce_color_refs();

create or replace function public.protect_visual_finishing_evidence()
returns trigger language plpgsql set search_path = public as $$
begin
  raise exception 'visual-finishing evidence % is immutable', old.id;
end $$;

create trigger protect_visual_finishing_evidence before update or delete on public.graphics_runs
  for each row execute function public.protect_visual_finishing_evidence();
create trigger protect_visual_finishing_evidence before update or delete on public.caption_runs
  for each row execute function public.protect_visual_finishing_evidence();
create trigger protect_visual_finishing_evidence before update or delete on public.color_runs
  for each row execute function public.protect_visual_finishing_evidence();
