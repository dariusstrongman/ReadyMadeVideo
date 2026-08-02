-- Audiovisual editor Milestone 6: immutable complete candidates, structured
-- critics, publishability evidence, and tournament selection.

create table if not exists public.candidate_runs (
  id uuid primary key default gen_random_uuid(),
  batch_id uuid not null,
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  preproduction_run_id uuid not null references public.preproduction_runs(id) on delete restrict,
  picture_edit_run_id uuid not null references public.picture_edit_runs(id) on delete restrict,
  music_sound_run_id uuid not null references public.music_sound_runs(id) on delete restrict,
  audio_mix_run_id uuid not null references public.audio_mix_runs(id) on delete restrict,
  graphics_run_id uuid not null references public.graphics_runs(id) on delete restrict,
  caption_run_id uuid not null references public.caption_runs(id) on delete restrict,
  color_run_id uuid not null references public.color_runs(id) on delete restrict,
  parent_candidate_run_id uuid references public.candidate_runs(id) on delete restrict,
  candidate_key text not null,
  candidate_index integer not null check (candidate_index > 0),
  generation_kind text not null check (generation_kind in ('initial','revised')),
  source_picture_candidate_id text not null,
  variant_config jsonb not null,
  manifest jsonb not null,
  render_qc jsonb not null,
  preview_storage_bucket text not null check (preview_storage_bucket = 'exports'),
  preview_storage_path text not null,
  fabricated_footage boolean not null default false check (not fabricated_footage),
  created_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default clock_timestamp(),
  unique (batch_id, candidate_key),
  unique (batch_id, candidate_index)
);

create table if not exists public.critic_runs (
  id uuid primary key default gen_random_uuid(),
  batch_id uuid not null,
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  candidate_run_id uuid not null references public.candidate_runs(id) on delete restrict,
  critic_kind text not null check (critic_kind in ('hook_effectiveness','story_structure','pacing_retention','picture_quality','music_synchronization','audio_quality','motion_graphics','captions','color_finishing','publishability')),
  version integer not null default 1 check (version > 0),
  score numeric(6,3) not null check (score between 0 and 100),
  passed boolean not null,
  evidence jsonb not null check (jsonb_typeof(evidence) = 'array' and jsonb_array_length(evidence) > 0),
  issues jsonb not null,
  revision_requests jsonb not null,
  consistency_hash text not null check (length(consistency_hash) = 64),
  created_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default clock_timestamp(),
  unique (candidate_run_id, critic_kind, version)
);

create table if not exists public.publishability_reports (
  id uuid primary key default gen_random_uuid(),
  batch_id uuid not null,
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  candidate_run_id uuid not null references public.candidate_runs(id) on delete restrict,
  version integer not null default 1 check (version > 0),
  dimensions jsonb not null,
  overall_publishability_score numeric(6,3) not null check (overall_publishability_score between 0 and 100),
  publishable boolean not null,
  blocking_issues jsonb not null,
  technical_qc_passed boolean not null,
  rendered_media_qc_passed boolean not null default false,
  tournament_eligible boolean not null default false,
  rendered_media_qc jsonb not null default '{}'::jsonb,
  created_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default clock_timestamp(),
  check (not publishable or
         (technical_qc_passed and rendered_media_qc_passed and tournament_eligible)),
  check (not tournament_eligible or rendered_media_qc_passed),
  unique (candidate_run_id, version)
);

alter table public.publishability_reports
  add column if not exists rendered_media_qc_passed boolean not null default false,
  add column if not exists tournament_eligible boolean not null default false,
  add column if not exists rendered_media_qc jsonb not null default '{}'::jsonb;

create table if not exists public.tournament_runs (
  id uuid primary key default gen_random_uuid(),
  batch_id uuid not null unique,
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  preproduction_run_id uuid not null references public.preproduction_runs(id) on delete restrict,
  picture_edit_run_id uuid not null references public.picture_edit_runs(id) on delete restrict,
  music_sound_run_id uuid not null references public.music_sound_runs(id) on delete restrict,
  audio_mix_run_id uuid not null references public.audio_mix_runs(id) on delete restrict,
  graphics_run_id uuid not null references public.graphics_runs(id) on delete restrict,
  caption_run_id uuid not null references public.caption_runs(id) on delete restrict,
  color_run_id uuid not null references public.color_runs(id) on delete restrict,
  version integer not null check (version > 0),
  candidate_run_ids uuid[] not null check (cardinality(candidate_run_ids) >= 2),
  pairwise_comparisons jsonb not null,
  bracket jsonb not null,
  winner_candidate_run_id uuid not null references public.candidate_runs(id) on delete restrict,
  winner_reasoning jsonb not null,
  human_ceiling_comparison jsonb not null,
  created_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default clock_timestamp(),
  unique (project_id, version)
);

create index if not exists candidate_runs_project_idx on public.candidate_runs(project_id, created_at desc);
create index if not exists critic_runs_candidate_idx on public.critic_runs(candidate_run_id, critic_kind);
create index if not exists publishability_project_idx on public.publishability_reports(project_id, created_at desc);
create index if not exists tournament_project_idx on public.tournament_runs(project_id, version desc);

alter table public.candidate_runs enable row level security;
alter table public.critic_runs enable row level security;
alter table public.publishability_reports enable row level security;
alter table public.tournament_runs enable row level security;

do $$ declare t text; begin
  foreach t in array array['candidate_runs','critic_runs','publishability_reports','tournament_runs'] loop
    execute format('drop policy if exists %I on public.%I', t || '_select_own', t);
    execute format('create policy %I on public.%I for select to authenticated using (user_id = auth.uid())', t || '_select_own', t);
    execute format('drop policy if exists operator_read on public.%I', t);
    execute format('create policy operator_read on public.%I for select to authenticated using (public.is_operator())', t);
    execute format('drop trigger if exists own_project_check on public.%I', t);
    execute format('create trigger own_project_check before insert or update on public.%I for each row execute function public.enforce_project_ownership()', t);
  end loop;
end $$;

create or replace function public.enforce_editorial_candidate_refs()
returns trigger language plpgsql security definer set search_path=public as $$
declare c record; p record;
begin
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

create or replace function public.enforce_editorial_child_refs()
returns trigger language plpgsql security definer set search_path=public as $$
declare c record;
begin
  select batch_id,project_id,user_id into c from public.candidate_runs where id=new.candidate_run_id;
  if c.batch_id is null or (c.batch_id,c.project_id,c.user_id) is distinct from
     (new.batch_id,new.project_id,new.user_id) then
    raise exception 'editorial evidence candidate is outside project/batch lineage';
  end if;
  return new;
end $$;

create or replace function public.enforce_tournament_refs()
returns trigger language plpgsql security definer set search_path=public as $$
declare cid uuid; c record; base record; winner_eligible boolean;
begin
  select project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,
         audio_mix_run_id,graphics_run_id,caption_run_id into base
    from public.color_runs where id=new.color_run_id;
  if base.project_id is null or (base.project_id,base.user_id,base.preproduction_run_id,
     base.picture_edit_run_id,base.music_sound_run_id,base.audio_mix_run_id,
     base.graphics_run_id,base.caption_run_id) is distinct from
    (new.project_id,new.user_id,new.preproduction_run_id,new.picture_edit_run_id,
     new.music_sound_run_id,new.audio_mix_run_id,new.graphics_run_id,new.caption_run_id) then
    raise exception 'tournament ancestry is outside Milestones 1-5';
  end if;
  if not new.winner_candidate_run_id=any(new.candidate_run_ids) then raise exception 'winner is outside tournament'; end if;
  select tournament_eligible into winner_eligible
    from public.publishability_reports
    where candidate_run_id=new.winner_candidate_run_id and batch_id=new.batch_id
    order by version desc limit 1;
  if winner_eligible is distinct from true then
    raise exception 'tournament winner failed rendered-media eligibility';
  end if;
  foreach cid in array new.candidate_run_ids loop
    select batch_id,project_id,user_id,color_run_id into c from public.candidate_runs where id=cid;
    if c.batch_id is null or (c.batch_id,c.project_id,c.user_id,c.color_run_id) is distinct from
       (new.batch_id,new.project_id,new.user_id,new.color_run_id) then
      raise exception 'tournament candidate is outside project/batch lineage';
    end if;
  end loop;
  return new;
end $$;

drop trigger if exists editorial_candidate_refs_check on public.candidate_runs;
create trigger editorial_candidate_refs_check before insert or update on public.candidate_runs for each row execute function public.enforce_editorial_candidate_refs();
drop trigger if exists editorial_critic_refs_check on public.critic_runs;
create trigger editorial_critic_refs_check before insert or update on public.critic_runs for each row execute function public.enforce_editorial_child_refs();
drop trigger if exists editorial_publishability_refs_check on public.publishability_reports;
create trigger editorial_publishability_refs_check before insert or update on public.publishability_reports for each row execute function public.enforce_editorial_child_refs();
drop trigger if exists editorial_tournament_refs_check on public.tournament_runs;
create trigger editorial_tournament_refs_check before insert or update on public.tournament_runs for each row execute function public.enforce_tournament_refs();

create or replace function public.protect_editorial_intelligence_evidence()
returns trigger language plpgsql set search_path=public as $$ begin
  raise exception 'editorial-intelligence evidence % is immutable',old.id;
end $$;

do $$ declare t text; begin
  foreach t in array array['candidate_runs','critic_runs','publishability_reports','tournament_runs'] loop
    execute format('drop trigger if exists protect_editorial_intelligence_evidence on public.%I',t);
    execute format('create trigger protect_editorial_intelligence_evidence before update or delete on public.%I for each row execute function public.protect_editorial_intelligence_evidence()',t);
  end loop;
end $$;
