-- Migration 0003: analysis pipeline artifacts + canonical segment catalog
-- Every pipeline stage stores an inspectable, versioned artifact row.
-- Analysis runs SERVER-SIDE only (service role writes); users can read their own.

-- ============ asset_analysis: one row per (asset, stage-kind, version) ============
create table if not exists public.asset_analysis (
  id uuid primary key default gen_random_uuid(),
  asset_id uuid not null references public.media_assets(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  kind text not null check (kind in
    ('probe','proxy','scenes','mechanical','audio','transcript','semantic','motion','catalog')),
  version integer not null default 1,
  status text not null default 'completed'
    check (status in ('running','completed','failed')),
  error_message text,
  data jsonb,
  storage_paths jsonb,          -- e.g. {"proxy": "...", "thumbs": [...], "wav": "..."}
  created_at timestamptz not null default now(),
  unique (asset_id, kind, version)
);
create index if not exists asset_analysis_asset_idx on public.asset_analysis(asset_id, kind);
alter table public.asset_analysis enable row level security;

drop policy if exists asset_analysis_select_own on public.asset_analysis;
create policy asset_analysis_select_own on public.asset_analysis
  for select to authenticated using (user_id = auth.uid());
-- no insert/update/delete policies for authenticated: service-role writes only

-- ============ segments: canonical merged segment records ============
create table if not exists public.segments (
  id uuid primary key default gen_random_uuid(),
  segment_key text not null,               -- stable human id e.g. seg_assetprefix_003
  asset_id uuid not null references public.media_assets(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  source_start double precision not null check (source_start >= 0),
  source_end double precision not null,
  data jsonb not null,                     -- full canonical segment schema (versioned inside)
  search_text text,
  schema_version integer not null default 1,
  created_at timestamptz not null default now(),
  unique (asset_id, segment_key, schema_version),
  check (source_end > source_start)
);
create index if not exists segments_project_idx on public.segments(project_id);
create index if not exists segments_search_idx on public.segments
  using gin (to_tsvector('english', coalesce(search_text, '')));
alter table public.segments enable row level security;

drop policy if exists segments_select_own on public.segments;
create policy segments_select_own on public.segments
  for select to authenticated using (user_id = auth.uid());
-- service-role writes only
