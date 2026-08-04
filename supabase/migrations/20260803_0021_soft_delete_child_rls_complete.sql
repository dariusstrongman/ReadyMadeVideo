-- Migration 0021: complete the soft-delete child-visibility gate started in 0019.
--
-- 0019 gated the Product-Editor / editorial / core-pipeline child tables. This migration
-- covers the REMAINING authenticated owner-readable project-owned tables so that NO child
-- row of a soft-deleted project is readable by its owner through PostgREST. Every such
-- table's owner-read branch now additionally requires public.project_not_deleted(project_id)
-- (defined in 0019). Operator / service-role access is preserved unchanged, and no
-- immutability or ancestry protection is touched (these are SELECT policies only).
--
-- Coverage of owner-readable project-owned tables is now:
--   0019: media_assets, timelines, render_jobs, pipeline_jobs, asset_analysis, segments,
--         candidate_runs, critic_runs, publishability_reports, tournament_runs,
--         editor_documents, editor_operations, editor_revision_proposals,
--         editor_render_requests, editor_audit_events
--   0021: edit_runs, user_corrections, draft_evaluations, preproduction_runs,
--         picture_edit_runs, music_sound_runs, licensed_music_assets, audio_mix_runs,
--         graphics_runs, caption_runs, color_runs, human_edit_sessions,
--         human_edit_timing_events, timeline_scorecards, project_status_events
--   (profiles is per-user, not project-owned, so it is intentionally NOT gated.)

-- ---- owner-only SELECT (using user_id = auth.uid()) whose policy is named {t}_select_own.
-- (Only tables whose ORIGINAL policy follows the {t}_select_own convention go in this loop;
-- differently-named policies are recreated explicitly below so no ungated duplicate remains.)
do $$ declare t text; begin
  foreach t in array array['edit_runs','user_corrections','preproduction_runs',
                            'picture_edit_runs','music_sound_runs',
                            'graphics_runs','caption_runs','color_runs'] loop
    execute format('drop policy if exists %I on public.%I', t || '_select_own', t);
    execute format(
      'create policy %I on public.%I for select to authenticated '
      'using (user_id = auth.uid() and public.project_not_deleted(project_id))',
      t || '_select_own', t);
  end loop;
end $$;

-- differently-named owner-only SELECT policies (names do NOT match {t}_select_own).
drop policy if exists licensed_music_select_own on public.licensed_music_assets;
create policy licensed_music_select_own on public.licensed_music_assets
  for select to authenticated
  using (user_id = auth.uid() and public.project_not_deleted(project_id));

drop policy if exists audio_mix_select_own on public.audio_mix_runs;
create policy audio_mix_select_own on public.audio_mix_runs
  for select to authenticated
  using (user_id = auth.uid() and public.project_not_deleted(project_id));

-- ---- owner-or-operator SELECT (gate the OWNER branch; operators keep full access) ----
drop policy if exists evals_select on public.draft_evaluations;
create policy evals_select on public.draft_evaluations
  for select to authenticated
  using ((user_id = auth.uid() and public.project_not_deleted(project_id))
         or public.is_operator());

drop policy if exists human_sessions_select on public.human_edit_sessions;
create policy human_sessions_select on public.human_edit_sessions
  for select to authenticated
  using ((user_id = auth.uid() and public.project_not_deleted(project_id))
         or public.is_operator());

drop policy if exists human_timing_events_select on public.human_edit_timing_events;
create policy human_timing_events_select on public.human_edit_timing_events
  for select to authenticated
  using ((user_id = auth.uid() and public.project_not_deleted(project_id))
         or public.is_operator());

drop policy if exists timeline_scorecards_select on public.timeline_scorecards;
create policy timeline_scorecards_select on public.timeline_scorecards
  for select to authenticated
  using ((user_id = auth.uid() and public.project_not_deleted(project_id))
         or public.is_operator());

-- ---- project_status_events already checks ownership via EXISTS(projects); add not-deleted ----
drop policy if exists pse_select_own on public.project_status_events;
create policy pse_select_own on public.project_status_events
  for select to authenticated using (
    exists (select 1 from public.projects p
            where p.id = project_id and p.user_id = auth.uid()
              and p.deleted_at is null));
