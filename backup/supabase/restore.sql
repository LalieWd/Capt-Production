-- RESTAURATION des donnees Supabase au 2026-11-19 (sauvegarde avant la V1).
--
-- ATTENTION : ce script REMPLACE le contenu actuel des tables « Clients » et « Animations »
-- par celui des copies backup_before_v1_*. Ne l'executer qu'apres avoir decide de revenir en arriere.
-- Les copies backup_before_v1_* ne sont jamais modifiees ni supprimees par ce script.
-- Ou l'executer : Supabase, SQL Editor, projet du depot (reference dans SUPABASE_URL).

-- 0) Controle prealable : doit afficher 7 / 18 / 1 lignes.
select (select count(*) from public.backup_before_v1_clients)    as clients,
       (select count(*) from public.backup_before_v1_jobs)       as jobs,
       (select count(*) from public.backup_before_v1_animations) as animations;

begin;

-- 1) Styles des marques (le plus important : c'est la source de verite du style).
delete from public."Clients";
insert into public."Clients" select * from public.backup_before_v1_clients;

-- 2) Gamme d'animations.
delete from public."Animations";
insert into public."Animations" select * from public.backup_before_v1_animations;

-- 3) Historique des jobs : restaurer seulement si necessaire (decommenter).
-- delete from public."Jobs";
-- insert into public."Jobs" select * from public.backup_before_v1_jobs;

commit;

-- 4) Verification : les empreintes doivent valoir
--    Clients    4bc9d144d1195b6e10486537038f620e
--    Animations b8f4f3bb025d54b1c487f4077ee981b8
--    Jobs       78d20ff213dff70c63b738522c7959b3
select 'Clients' as t, count(*) as n, md5(string_agg(c::text, '|' order by code)) as empreinte from public."Clients" c
union all select 'Animations', count(*), md5(coalesce(string_agg(a::text, '|' order by 1), '')) from public."Animations" a
union all select 'Jobs', count(*), md5(coalesce(string_agg(j::text, '|' order by job_id), '')) from public."Jobs" j;
