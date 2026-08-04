-- Migration 0016: bridged (basic-autoedit) candidate origin — Strategy B.
--
-- ADDITIVE ONLY. Lets candidate_runs represent an honest, music-LESS candidate
-- produced by the basic-autoedit bridge, WITHOUT fabricating M3/M4 music ancestry
-- (tempo/beat grid/energy/license). Milestone 1-6 ('initial'/'revised') validation
-- is unchanged and, if anything, made explicit by a new CHECK.
--
-- A bridged candidate keeps REAL ancestry for project/user/preproduction/picture,
-- carries identity color inside its manifest, and has NO music/audio/graphics/
-- caption/color lineage. Its preview lives under the documented autoedit prefix.

-- 1. Allow the new origin.
alter table public.candidate_runs
  drop constraint if exists candidate_runs_generation_kind_check;
alter table public.candidate_runs
  add constraint candidate_runs_generation_kind_check
  check (generation_kind in ('initial', 'revised', 'bridged'));

-- 2. Relax NOT NULL on the audio/music/graphics/caption/color lineage FKs so a
--    bridged candidate can omit them. (initial/revised are re-required below.)
alter table public.candidate_runs
  alter column music_sound_run_id drop not null,
  alter column audio_mix_run_id  drop not null,
  alter column graphics_run_id   drop not null,
  alter column caption_run_id    drop not null,
  alter column color_run_id      drop not null;

-- 3. Re-enforce full lineage for initial/revised and require empty lineage for
--    bridged (reject mixed/contradictory ancestry) at the schema level.
alter table public.candidate_runs
  drop constraint if exists candidate_runs_bridged_ancestry_check;
alter table public.candidate_runs
  add constraint candidate_runs_bridged_ancestry_check check (
    (generation_kind in ('initial', 'revised')
       and music_sound_run_id is not null and audio_mix_run_id is not null
       and graphics_run_id is not null and caption_run_id is not null
       and color_run_id is not null)
    or
    (generation_kind = 'bridged'
       and music_sound_run_id is null and audio_mix_run_id is null
       and graphics_run_id is null and caption_run_id is null
       and color_run_id is null)
  );

-- 4. Teach the ancestry trigger a bridged branch. The initial/revised body is
--    preserved EXACTLY (color_run mirror + editorial-intelligence preview prefix).
create or replace function public.enforce_editorial_candidate_refs()
returns trigger language plpgsql security definer set search_path=public as $$
declare c record; p record; pe record; pp record;
begin
  if new.generation_kind = 'bridged' then
    -- No music/audio/graphics/caption/color/parent lineage may be present.
    if new.music_sound_run_id is not null or new.audio_mix_run_id is not null
       or new.graphics_run_id is not null or new.caption_run_id is not null
       or new.color_run_id is not null or new.parent_candidate_run_id is not null then
      raise exception 'bridged candidate must not carry music/audio/graphics/caption/color/parent lineage';
    end if;
    -- Real preproduction + picture ancestry, both owned by the same user/project.
    select project_id, user_id into pp from public.preproduction_runs where id=new.preproduction_run_id;
    if pp.project_id is null or pp.project_id <> new.project_id or pp.user_id <> new.user_id then
      raise exception 'bridged candidate preproduction ancestry is outside owner/project';
    end if;
    select project_id, user_id, preproduction_run_id into pe
      from public.picture_edit_runs where id=new.picture_edit_run_id;
    if pe.project_id is null or pe.project_id <> new.project_id or pe.user_id <> new.user_id then
      raise exception 'bridged candidate picture ancestry is outside owner/project';
    end if;
    -- EXACT ancestry: the picture_edit_run must DIRECTLY descend from the candidate's
    -- selected preproduction_run (not merely share project/user). Rejects a
    -- same-project/same-user but mismatched preproduction/picture pair.
    if pe.preproduction_run_id is distinct from new.preproduction_run_id then
      raise exception 'bridged candidate picture_edit_run does not descend from its preproduction_run';
    end if;
    if coalesce(new.manifest->>'fabricatedFootage','true') <> 'false' then
      raise exception 'bridged candidate may not fabricate footage';
    end if;
    if new.preview_storage_path not like 'users/'||new.user_id::text||'/projects/'||new.project_id::text||'/autoedit/%' then
      raise exception 'bridged candidate preview path is outside the autoedit prefix';
    end if;
    return new;
  end if;

  -- ---- initial/revised (Milestone 1-6): unchanged ----
  select project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,
         audio_mix_run_id,graphics_run_id,caption_run_id into c
    from public.color_runs where id=new.color_run_id;
  if c.project_id is null or (c.project_id,c.user_id,c.preproduction_run_id,c.picture_edit_run_id,
      c.music_sound_run_id,c.audio_mix_run_id,c.graphics_run_id,c.caption_run_id) is distinct from
     (new.project_id,new.user_id,new.preproduction_run_id,new.picture_edit_run_id,
      new.music_sound_run_id,new.audio_mix_run_id,new.graphics_run_id,new.caption_run_id) then
    raise exception 'editorial candidate ancestry is outside Milestones 1-5';
  end if;
  if new.preview_storage_path not like 'users/'||new.user_id::text||'/projects/'||new.project_id::text||'/editorial-intelligence/%' then
    raise exception 'editorial candidate preview path is outside project ownership';
  end if;
  if coalesce(new.manifest->>'fabricatedFootage','true') <> 'false' then
    raise exception 'editorial intelligence may not fabricate footage';
  end if;
  if new.generation_kind='initial' and new.parent_candidate_run_id is not null then
    raise exception 'initial candidate cannot have parent';
  elsif new.generation_kind='revised' then
    select batch_id,project_id,user_id,color_run_id into p from public.candidate_runs where id=new.parent_candidate_run_id;
    if p.batch_id is null or (p.batch_id,p.project_id,p.user_id,p.color_run_id) is distinct from
       (new.batch_id,new.project_id,new.user_id,new.color_run_id) then
      raise exception 'revised candidate parent is outside immutable batch lineage';
    end if;
  end if;
  return new;
end $$;
