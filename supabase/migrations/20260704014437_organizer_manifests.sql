create table if not exists public.organizer_manifests (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  source text not null default 'file-organizer-cli',
  root text not null,
  file_count integer not null check (file_count >= 0),
  total_bytes bigint not null default 0 check (total_bytes >= 0),
  category_counts jsonb not null default '{}'::jsonb,
  manifest jsonb not null
);

alter table public.organizer_manifests enable row level security;

revoke all on table public.organizer_manifests from anon, authenticated;

create index if not exists idx_organizer_manifests_created_at
  on public.organizer_manifests (created_at desc);

create index if not exists idx_organizer_manifests_category_counts
  on public.organizer_manifests using gin (category_counts);
