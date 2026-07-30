-- Stromation agency: leads table for the Lead Leak Audit funnel.
-- Run once in the Supabase SQL editor (main project: iadzcnzgbtuigyodeqas).
-- Until this runs, the intake workflow stores leads in `subscribers` with
-- source='lead_audit' (graceful fallback) and upgrades automatically after.

create table if not exists public.leads (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  business_name text not null,
  contact_name text not null,
  email text not null,
  phone text,
  industry text,
  team_size text,
  lead_volume text,
  current_systems text,
  problem text,
  sms_consent boolean not null default false,
  source jsonb,
  status text not null default 'new',   -- new / contacted / audit_booked / audit_done / proposal / won / lost
  notes text,
  submission_hash text
);

create index if not exists leads_email_idx on public.leads (email);
create index if not exists leads_status_idx on public.leads (status);
create index if not exists leads_hash_idx on public.leads (submission_hash);

alter table public.leads enable row level security;
-- No policies on purpose: only the service role (used by n8n) can read/write.
