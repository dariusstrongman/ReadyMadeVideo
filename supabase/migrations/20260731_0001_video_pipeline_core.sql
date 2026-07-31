-- Migration 0001: real video pipeline core schema
-- Project: Stromation (iadzcnzgbtuigyodeqas). Additive only — does NOT touch
-- existing tables (leads, subscribers, video_intake).
-- All tables RLS-enabled; owner-scoped policies; render_jobs is user-readable
-- but only service-role-writable for status transitions.

-- ============ profiles ============
create table if not exists public.profiles (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null unique references auth.users(id) on delete cascade,
  display_name text,
  created_at timestamptz not null default now()
);
alter table public.profiles enable row level security;

drop policy if exists profiles_select_own on public.profiles;
create policy profiles_select_own on public.profiles
  for select to authenticated using (user_id = auth.uid());
drop policy if exists profiles_update_own on public.profiles;
create policy profiles_update_own on public.profiles
  for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());

-- auto-create a profile row on signup
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (user_id, display_name)
  values (new.id, split_part(new.email, '@', 1))
  on conflict (user_id) do nothing;
  return new;
end $$;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ============ projects ============
create table if not exists public.projects (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (char_length(name) between 1 and 200),
  status text not null default 'draft'
    check (status in ('draft','uploading','ready','rendering','complete')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists projects_user_idx on public.projects(user_id, created_at desc);
alter table public.projects enable row level security;

drop policy if exists projects_all_own on public.projects;
create policy projects_all_own on public.projects
  for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());

create or replace function public.touch_updated_at()
returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end $$;
drop trigger if exists projects_touch on public.projects;
create trigger projects_touch before update on public.projects
  for each row execute function public.touch_updated_at();

-- ============ media_assets ============
create table if not exists public.media_assets (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  filename text not null,
  storage_path text not null,
  mime_type text,
  size_bytes bigint,
  duration_seconds double precision,
  created_at timestamptz not null default now()
);
create index if not exists media_assets_project_idx on public.media_assets(project_id);
alter table public.media_assets enable row level security;

drop policy if exists media_assets_all_own on public.media_assets;
create policy media_assets_all_own on public.media_assets
  for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());

-- ============ timelines ============
create table if not exists public.timelines (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  version integer not null default 1,
  timeline_json jsonb not null,
  created_at timestamptz not null default now()
);
create index if not exists timelines_project_idx on public.timelines(project_id, created_at desc);
alter table public.timelines enable row level security;

drop policy if exists timelines_all_own on public.timelines;
create policy timelines_all_own on public.timelines
  for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());

-- ============ render_jobs ============
-- Users can SELECT their own jobs and INSERT new queued jobs.
-- Status transitions / output fields are written ONLY by the render backend
-- (service role bypasses RLS). No update/delete policy for authenticated.
create table if not exists public.render_jobs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  timeline_id uuid not null references public.timelines(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  status text not null default 'queued'
    check (status in ('queued','processing','completed','failed')),
  progress integer not null default 0 check (progress between 0 and 100),
  error_message text,
  output_storage_path text,
  output_size_bytes bigint,
  output_duration_seconds double precision,
  output_width integer,
  output_height integer,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz
);
create index if not exists render_jobs_project_idx on public.render_jobs(project_id, created_at desc);
alter table public.render_jobs enable row level security;

drop policy if exists render_jobs_select_own on public.render_jobs;
create policy render_jobs_select_own on public.render_jobs
  for select to authenticated using (user_id = auth.uid());
drop policy if exists render_jobs_insert_own on public.render_jobs;
create policy render_jobs_insert_own on public.render_jobs
  for insert to authenticated
  with check (user_id = auth.uid() and status = 'queued');
