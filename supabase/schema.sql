-- Fresh-install schema. Apply once in a new Supabase project.
create extension if not exists pgmq;
select pgmq.create('adbuild');
create table public.tenants (id uuid primary key default gen_random_uuid(), name text not null);
create table public.memberships (
 tenant_id uuid references tenants on delete cascade, user_id uuid references auth.users on delete cascade,
 role text not null check(role in ('admin','operator','viewer')), primary key(tenant_id,user_id));
create table public.projects (
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null references tenants,
 name text not null, account_id text not null default '', policy jsonb, policy_version int not null default 0,
 reserved numeric(20,4) not null default 0, created_at timestamptz not null default now());
create table public.files (
 id uuid primary key, tenant_id uuid not null references tenants, project_id uuid not null references projects,
 name text not null, kind text not null check(kind in ('requirements','export')), sha256 text not null,
 storage_path text not null, inspection jsonb not null, mapping jsonb, created_at timestamptz not null default now());
create table public.history (
 tenant_id uuid not null references tenants, project_id uuid not null references projects,
 account_id text not null, ad_id text not null, data jsonb not null, primary key(project_id,ad_id));
create table public.drafts (
 id uuid primary key, tenant_id uuid not null references tenants, project_id uuid not null references projects,
 version int not null, data jsonb not null, created_at timestamptz not null default now());
create table public.connections (
 tenant_id uuid primary key references tenants, state jsonb not null default '{}',
 tools jsonb not null default '[]', bindings jsonb not null default '{}', verified boolean not null default false);
create table public.runs (
 id uuid primary key, tenant_id uuid not null references tenants, project_id uuid not null references projects,
 draft_id uuid not null references drafts, draft_version int not null, policy_version int not null,
 status text not null, frozen jsonb not null, error text, cancelled boolean not null default false,
 created_at timestamptz not null default now(), unique(draft_id,draft_version));
create table public.deployments (
 tenant_id uuid not null references tenants, project_id uuid not null references projects,
 fingerprint text not null, run_id uuid not null references runs, requirement jsonb not null,
 status text not null default 'pending', steps jsonb not null default '{}',
 primary key(project_id,fingerprint));
create table public.messages (
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null references tenants,
 project_id uuid not null references projects, role text not null, content text not null,
 created_at timestamptz not null default now());
create table public.mapping_templates (
 tenant_id uuid not null references tenants, project_id uuid not null references projects,
 signature text not null, data jsonb not null, primary key(project_id,signature));
create table public.audit (
 id bigint generated always as identity primary key, tenant_id uuid not null references tenants,
 project_id uuid references projects, event text not null, data jsonb not null, created_at timestamptz not null default now());
create function public.is_member(t uuid) returns boolean language sql stable security definer set search_path=public as $$
 select exists(select 1 from memberships where tenant_id=t and user_id=auth.uid()); $$;
alter table tenants enable row level security;
create policy tenant_read on tenants for select using(public.is_member(id));
alter table memberships enable row level security;
create policy own_memberships on memberships for select using(user_id=auth.uid());
do $$ declare tbl text; begin
 foreach tbl in array array['projects','files','history','drafts','runs','deployments','messages','mapping_templates','audit'] loop
 execute format('alter table public.%I enable row level security',tbl);
 execute format('create policy tenant_read on public.%I for select using(public.is_member(tenant_id))',tbl);
 end loop;
end $$;
-- All mutations go through the authenticated API, never browser service credentials.
alter table connections enable row level security;
revoke all on connections from anon,authenticated;
insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
values('workbooks','workbooks',false,20971520,array['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet']);
-- No client storage policies: authenticated API issues short-lived URLs after project ACL checks.
