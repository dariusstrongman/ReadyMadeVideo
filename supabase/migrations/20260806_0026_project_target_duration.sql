-- Migration 0026: the customer chooses how long the video should be.
--
-- The Editorial Planner has always accepted binding durationMin/durationMax
-- constraints, but nothing in the product ever collected a target length —
-- so the model always chose (idiomatically ~25s for 9:16 short-form), and a
-- customer wanting a 3-minute cut had no way to say so.
--
-- Nullable: null means "let the AI decide", preserving current behavior.

alter table public.projects
  add column if not exists target_duration_seconds integer;

do $$ begin
  alter table public.projects
    add constraint projects_target_duration_chk
    check (target_duration_seconds is null
           or target_duration_seconds between 10 and 3600);
exception when duplicate_object then null; end $$;

comment on column public.projects.target_duration_seconds is
  'Requested output length in seconds (null = model decides). The planning '
  'chain maps this to binding durationMin/durationMax constraints.';
