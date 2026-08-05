-- Migration 0023: ancestry-bound bridged-candidate uniqueness.
--
-- The bridge's picture_edit_runs row is deterministic per (project, source
-- timeline), so binding bridged-candidate uniqueness to picture_edit_run_id
-- enforces at the database level exactly the reuse rule the application uses:
-- at most ONE bridged candidate per source timeline, while candidates of
-- DIFFERENT timelines (e.g. successive Picture Edit Engine versions) coexist
-- as separate immutable rows. Additive only; no existing ancestry rule,
-- trigger or immutability protection is weakened.
create unique index if not exists candidate_runs_bridged_per_picture_idx
  on public.candidate_runs (picture_edit_run_id)
  where generation_kind = 'bridged';
