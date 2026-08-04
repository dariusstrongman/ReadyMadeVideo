-- Migration 0019: hide soft-deleted projects' child rows + close legacy render_jobs writes.
--
-- Migration 0017 hides a soft-deleted PROJECT (projects_all_own requires
-- deleted_at is null), but every project-owned child table still admitted the owner's
-- rows via `user_id = auth.uid()` alone. A customer reading media_assets / pipeline_jobs
-- / candidate_runs / editor_documents (etc.) directly through PostgREST could therefore
-- still see the children of a project they had deleted. This migration gates every
-- customer-readable child SELECT on the parent project being live, and removes the
-- legacy authenticated INSERT into render_jobs (the customer render surface is the
-- immutable Product Editor; /render is disabled and pipeline_jobs is service-role write).

-- Parent-liveness predicate. SECURITY DEFINER so the check does not itself depend on the
-- caller's RLS view of projects; it returns only a boolean and is always AND-ed with the
-- row's own user_id = auth.uid() ownership check, so it cannot widen visibility.
create or replace function public.project_not_deleted(pid uuid)
returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.projects where id = pid and deleted_at is null);
$$;

-- ---- media_assets (FOR ALL): read + write only while the parent project is live ----
drop policy if exists media_assets_all_own on public.media_assets;
create policy media_assets_all_own on public.media_assets
  for all to authenticated
  using (user_id = auth.uid() and public.project_not_deleted(project_id))
  with check (user_id = auth.uid() and public.project_not_deleted(project_id));

-- ---- timelines (FOR ALL) ----
drop policy if exists timelines_all_own on public.timelines;
create policy timelines_all_own on public.timelines
  for all to authenticated
  using (user_id = auth.uid() and public.project_not_deleted(project_id))
  with check (user_id = auth.uid() and public.project_not_deleted(project_id));

-- ---- render_jobs: SELECT gated on live parent; DROP the authenticated INSERT ----
-- Legacy compatibility read is preserved; customers can no longer insert render_jobs.
-- The render backend uses the service role (bypasses RLS) for any internal write.
drop policy if exists render_jobs_insert_own on public.render_jobs;
drop policy if exists render_jobs_select_own on public.render_jobs;
create policy render_jobs_select_own on public.render_jobs
  for select to authenticated
  using (user_id = auth.uid() and public.project_not_deleted(project_id));

-- ---- pipeline_jobs: owner reads only while parent is live; operators unrestricted ----
drop policy if exists pjobs_select_own on public.pipeline_jobs;
create policy pjobs_select_own on public.pipeline_jobs
  for select to authenticated
  using ((user_id = auth.uid() and public.project_not_deleted(project_id))
         or public.is_operator());

-- ---- asset_analysis + segments (SELECT own; service-role writes) ----
drop policy if exists asset_analysis_select_own on public.asset_analysis;
create policy asset_analysis_select_own on public.asset_analysis
  for select to authenticated
  using (user_id = auth.uid() and public.project_not_deleted(project_id));

drop policy if exists segments_select_own on public.segments;
create policy segments_select_own on public.segments
  for select to authenticated
  using (user_id = auth.uid() and public.project_not_deleted(project_id));

-- ---- editorial intelligence tables (keep the separate operator_read policy intact) ----
do $$ declare t text; begin
  foreach t in array array['candidate_runs','critic_runs','publishability_reports',
                            'tournament_runs'] loop
    execute format('drop policy if exists %I on public.%I', t || '_select_own', t);
    execute format(
      'create policy %I on public.%I for select to authenticated '
      'using (user_id = auth.uid() and public.project_not_deleted(project_id))',
      t || '_select_own', t);
  end loop;
end $$;

-- ---- Product Editor tables ----
do $$ declare t text; begin
  foreach t in array array['editor_documents','editor_operations','editor_revision_proposals',
                            'editor_render_requests','editor_audit_events'] loop
    execute format('drop policy if exists %I on public.%I', t || '_select_own', t);
    execute format(
      'create policy %I on public.%I for select to authenticated '
      'using (user_id = auth.uid() and public.project_not_deleted(project_id))',
      t || '_select_own', t);
  end loop;
end $$;
