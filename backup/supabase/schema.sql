-- Structure des tables Supabase au 2026-11-19 (sauvegarde avant la V1).
-- Reconstituee depuis la base reelle (colonnes, types, cles primaires, valeurs par defaut visibles).
-- Les valeurs par defaut non listees ici n'existaient pas au moment de la sauvegarde.
-- Aucune donnee ni secret dans ce fichier.

create table if not exists public."Clients" (
  id          bigint,
  code        text,
  nom         text,
  alias       jsonb,
  menu_label  text,
  style       jsonb,
  glossaire   jsonb,
  actif       boolean,
  created_at  timestamptz,
  email       text,
  skill_md    text
);

create table if not exists public."Jobs" (
  job_id                text primary key,
  drive_file_id         text,
  client_code           text,
  client_email          text,
  title                 text,
  status                text,        -- dispatched | review | approved | rejected | failed
  revision              bigint,
  dispatched_at         timestamptz,
  drive_review_file_id  text,
  drive_review_srt_id   text,
  created_at            timestamptz
);

create table if not exists public."Animations" (
  code        text primary key,
  nom         text not null,
  description text,
  effets      jsonb not null default '{}'::jsonb,
  skill_md    text,
  actif       boolean not null default true,
  created_at  timestamptz not null default now()
);

-- Securite : la protection ligne par ligne (RLS) est activee sur les 3 tables.
alter table public."Clients"    enable row level security;
alter table public."Jobs"       enable row level security;
alter table public."Animations" enable row level security;

-- Stockage : bucket PRIVE « sorties » (videos et .srt generes). Son contenu n'est pas sauvegarde ici.
