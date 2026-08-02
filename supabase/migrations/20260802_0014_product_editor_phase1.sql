-- Product Editor Phase 1: immutable editor snapshots, append-only typed
-- operations, exact-version render binding, and customer-visible audit events.

alter table public.timelines drop constraint if exists timelines_lineage_check;
alter table public.timelines drop constraint if exists timelines_lineage_check1;
alter table public.timelines drop constraint if exists timelines_lineage_check2;
alter table public.timelines
  add constraint timelines_lineage_check check (lineage in
    ('legacy','autonomous_initial','autonomous_intermediate','autonomous_revised',
     'human_draft','human_approved','product_editor'));

create table if not exists public.editor_documents (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  candidate_run_id uuid not null references public.candidate_runs(id) on delete restrict,
  parent_document_id uuid references public.editor_documents(id) on delete restrict,
  timeline_id uuid not null unique references public.timelines(id) on delete restrict,
  version integer not null check (version > 0),
  document jsonb not null check (document->>'schemaVersion' = '1'),
  created_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default clock_timestamp(),
  unique (candidate_run_id, version)
);

create table if not exists public.editor_operations (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  candidate_run_id uuid not null references public.candidate_runs(id) on delete restrict,
  base_document_id uuid not null references public.editor_documents(id) on delete restrict,
  result_document_id uuid not null references public.editor_documents(id) on delete restrict,
  operation_id uuid not null unique,
  operation_index integer not null check (operation_index > 0),
  operation_type text not null check (operation_type in
    ('reorder_clip','trim_clip','split_clip','delete_clip','update_caption',
     'set_music_gain','toggle_graphic')),
  target_id text not null,
  actor text not null check (actor in ('user','ai')),
  operation jsonb not null,
  client_timestamp timestamptz not null,
  server_timestamp timestamptz not null default clock_timestamp(),
  unique (result_document_id, operation_index)
);

create table if not exists public.editor_render_requests (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  editor_document_id uuid not null references public.editor_documents(id) on delete restrict,
  editor_document_version integer not null check (editor_document_version > 0),
  pipeline_job_id uuid not null unique references public.pipeline_jobs(id) on delete restrict,
  created_at timestamptz not null default clock_timestamp()
);

create table if not exists public.editor_revision_proposals (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  candidate_run_id uuid not null references public.candidate_runs(id) on delete restrict,
  base_document_id uuid not null references public.editor_documents(id) on delete restrict,
  prompt text not null check (char_length(prompt) between 1 and 500),
  operations jsonb not null check (jsonb_typeof(operations)='array' and jsonb_array_length(operations)>0),
  created_at timestamptz not null default clock_timestamp()
);

create table if not exists public.editor_audit_events (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  action text not null,
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default clock_timestamp()
);

create index if not exists editor_documents_project_idx
  on public.editor_documents(project_id, candidate_run_id, version desc);
create index if not exists editor_operations_project_idx
  on public.editor_operations(project_id, server_timestamp desc);
create index if not exists editor_render_project_idx
  on public.editor_render_requests(project_id, created_at desc);

alter table public.editor_documents enable row level security;
alter table public.editor_operations enable row level security;
alter table public.editor_render_requests enable row level security;
alter table public.editor_audit_events enable row level security;

do $$ declare t text; begin
  foreach t in array array['editor_documents','editor_operations','editor_revision_proposals',
                            'editor_render_requests','editor_audit_events'] loop
    execute format('drop policy if exists %I on public.%I', t||'_select_own', t);
    execute format('create policy %I on public.%I for select to authenticated using (user_id=auth.uid())',
                   t||'_select_own', t);
    execute format('drop trigger if exists own_project_check on public.%I', t);
    execute format('create trigger own_project_check before insert or update on public.%I for each row execute function public.enforce_project_ownership()', t);
  end loop;
end $$;

create or replace function public.enforce_editor_document_refs()
returns trigger language plpgsql security definer set search_path=public as $$
declare c record; p record; tl record;
begin
  select project_id,user_id into c from public.candidate_runs where id=new.candidate_run_id;
  select project_id,user_id,lineage,is_immutable into tl from public.timelines where id=new.timeline_id;
  if c.project_id is null or (c.project_id,c.user_id) is distinct from (new.project_id,new.user_id) then
    raise exception 'editor candidate is outside project ownership';
  end if;
  if tl.project_id is null or (tl.project_id,tl.user_id,tl.lineage,tl.is_immutable) is distinct from
     (new.project_id,new.user_id,'product_editor'::text,true) then
    raise exception 'editor timeline is outside immutable product-editor lineage';
  end if;
  if (new.document->>'projectId')::uuid <> new.project_id or
     (new.document->>'candidateRunId')::uuid <> new.candidate_run_id then
    raise exception 'editor document JSON ancestry mismatch';
  end if;
  if new.version=1 and new.parent_document_id is not null then
    raise exception 'first editor version cannot have a parent';
  elsif new.version>1 then
    select project_id,user_id,candidate_run_id,version into p
      from public.editor_documents where id=new.parent_document_id;
    if p.project_id is null or (p.project_id,p.user_id,p.candidate_run_id,p.version+1) is distinct from
       (new.project_id,new.user_id,new.candidate_run_id,new.version) then
      raise exception 'editor parent lineage or version is invalid';
    end if;
  end if;
  return new;
end $$;

create or replace function public.enforce_editor_operation_refs()
returns trigger language plpgsql security definer set search_path=public as $$
declare b record; r record;
begin
  select project_id,user_id,candidate_run_id,version into b from public.editor_documents where id=new.base_document_id;
  select project_id,user_id,candidate_run_id,parent_document_id,version into r from public.editor_documents where id=new.result_document_id;
  if b.project_id is null or r.project_id is null or
     (b.project_id,b.user_id,b.candidate_run_id) is distinct from (new.project_id,new.user_id,new.candidate_run_id) or
     (r.project_id,r.user_id,r.candidate_run_id,r.parent_document_id,r.version) is distinct from
     (new.project_id,new.user_id,new.candidate_run_id,new.base_document_id,b.version+1) then
    raise exception 'editor operation is outside immutable revision lineage';
  end if;
  return new;
end $$;

create or replace function public.enforce_editor_render_refs()
returns trigger language plpgsql security definer set search_path=public as $$
declare d record; j record;
begin
  select project_id,user_id,version into d from public.editor_documents where id=new.editor_document_id;
  select project_id,user_id,kind,params into j from public.pipeline_jobs where id=new.pipeline_job_id;
  if d.project_id is null or j.project_id is null or
     (d.project_id,d.user_id,d.version) is distinct from (new.project_id,new.user_id,new.editor_document_version) or
     (j.project_id,j.user_id,j.kind) is distinct from (new.project_id,new.user_id,'final_render'::text) or
     j.params->>'editor_document_id' <> new.editor_document_id::text or
     (j.params->>'editor_document_version')::integer <> new.editor_document_version then
    raise exception 'render request is not bound to the saved editor version';
  end if;
  return new;
end $$;

drop trigger if exists editor_document_refs_check on public.editor_documents;
create trigger editor_document_refs_check before insert or update on public.editor_documents
  for each row execute function public.enforce_editor_document_refs();
drop trigger if exists editor_operation_refs_check on public.editor_operations;
create trigger editor_operation_refs_check before insert or update on public.editor_operations
  for each row execute function public.enforce_editor_operation_refs();
drop trigger if exists editor_render_refs_check on public.editor_render_requests;
create trigger editor_render_refs_check before insert or update on public.editor_render_requests
  for each row execute function public.enforce_editor_render_refs();

create or replace function public.enforce_editor_proposal_refs()
returns trigger language plpgsql security definer set search_path=public as $$
declare d record;
begin
  select project_id,user_id,candidate_run_id,version into d
    from public.editor_documents where id=new.base_document_id;
  if d.project_id is null or (d.project_id,d.user_id,d.candidate_run_id) is distinct from
     (new.project_id,new.user_id,new.candidate_run_id) then
    raise exception 'editor revision proposal is outside immutable lineage';
  end if;
  if exists(select 1 from jsonb_array_elements(new.operations) op
            where op->>'actor' <> 'ai' or op->>'proposalId' <> new.id::text
               or (op->>'baseVersion')::integer <> d.version) then
    raise exception 'editor revision proposal operation evidence is invalid';
  end if;
  return new;
end $$;
drop trigger if exists editor_proposal_refs_check on public.editor_revision_proposals;
create trigger editor_proposal_refs_check before insert or update on public.editor_revision_proposals
  for each row execute function public.enforce_editor_proposal_refs();

create or replace function public.protect_product_editor_evidence()
returns trigger language plpgsql set search_path=public as $$ begin
  raise exception 'product-editor evidence % is immutable',old.id;
end $$;

do $$ declare t text; begin
  foreach t in array array['editor_documents','editor_operations','editor_render_requests',
                            'editor_revision_proposals','editor_audit_events'] loop
    execute format('drop trigger if exists protect_product_editor_evidence on public.%I',t);
    execute format('create trigger protect_product_editor_evidence before update or delete on public.%I for each row execute function public.protect_product_editor_evidence()',t);
  end loop;
end $$;
