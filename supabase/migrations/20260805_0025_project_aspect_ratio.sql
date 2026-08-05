-- Migration 0025: persist the New Project answers, and give a project a shape.
--
-- The creation wizard already asks "Where will this live?" (YouTube / Reels /
-- TikTok / archive) and "What's the vibe?", then discarded both — the insert
-- only ever wrote name + user_id. Because nothing carried the answer, every
-- autoedit ran with jobs.py's platform default of "horizontal", so vertical
-- phone footage was rendered into a 1920x1080 landscape frame and exported with
-- the pillarbox bars baked into the file.
--
-- aspect_ratio is the column the render path reads. target_platform and vibe are
-- stored so the wizard's answers stop being thrown away.

alter table public.projects
  add column if not exists aspect_ratio text not null default '16:9',
  add column if not exists target_platform text,
  add column if not exists vibe text;

do $$ begin
  alter table public.projects
    add constraint projects_aspect_ratio_chk
    check (aspect_ratio in ('16:9', '9:16', '1:1'));
exception when duplicate_object then null; end $$;

-- Existing rows keep '16:9' (the shape they were actually rendered at), so no
-- finished project changes meaning retroactively.

comment on column public.projects.aspect_ratio is
  'Output frame shape: 16:9 landscape, 9:16 vertical, 1:1 square. Read by the '
  'autoedit/render path to size the timeline.';
