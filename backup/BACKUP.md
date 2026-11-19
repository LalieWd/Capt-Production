# Sauvegarde « avant la V1 » — 19 novembre 2026

Point de retour stable du projet de sous-titrage automatique, pris avant les évolutions de la V1.

| Élément | Valeur |
| --- | --- |
| Tag | `backup-before-V1` |
| Branche distante | `backup/before-V1` |
| Code sauvegardé (`main`) | `eaa930a` — fusion du PR n° 13 (FFmpeg statique), PR n° 12 (accent, temps de lecture) déjà fusionné |
| Workflow n8n | « Devpost-Workflow », version `e075e0db-30bc-4e6a-9a9e-64ccc0c57457` (« Accent, fidélité, grammaire au contrôle, mail »), 46 nœuds, publié |
| Supabase | tables `Clients` (7 lignes), `Jobs` (18), `Animations` (1), copiées dans `backup_before_v1_*` |

Le contenu de la branche = `main` au commit `eaa930a` + ce dossier `backup/`. Aucun fichier existant n'a été modifié.

## Ce qui est sauvegardé, et où

| Couche | Sauvegarde | Emplacement |
| --- | --- | --- |
| Code du runner (scripts, styles, polices, workflow GitHub) | Le commit et le tag Git | dépôt |
| Workflow n8n | Export JSON **rédigé** (sans secret ni donnée personnelle) | `backup/n8n/Devpost-Workflow_V0_e075e0db.redige.json` |
| Workflow n8n, copie complète | Export avec les vraies valeurs, **hors Git** (dépôt public) | gardé en privé par la propriétaire du projet |
| Structure de la base | Script de création | `backup/supabase/schema.sql` |
| Données de la base | Copies dans la base, protégées (RLS sans politique) | tables `backup_before_v1_clients`, `_jobs`, `_animations` |
| Restauration des données | Script prêt à l'emploi | `backup/supabase/restore.sql` |

## Ce qui n'est PAS dans cette sauvegarde (volontairement)

- **Aucun secret.** À recréer à la main si besoin : secrets GitHub `GROQ_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `CAPT_CALLBACK_URL` (facultatif) ; connexions n8n Google Drive, Gmail, GitHub, Supabase (OAuth) ; crédits Gateway n8n pour Claude.
- **Le contenu du bucket Storage `sorties`** (28 fichiers vidéo et `.srt` générés au moment de la sauvegarde) : ce sont des résultats, pas la configuration.
- **L'historique des exécutions n8n.**
- Le dépôt étant public, l'export n8n du dépôt remplace les valeurs réelles par des repères `__...__` (voir plus bas).

## Architecture au moment de la sauvegarde

```
Google Drive « Ajouter les sous-titres »
   └─ n8n : Devpost-Workflow (déclencheur : nouveau fichier, relevé chaque minute)
        ├─ lit la marque dans le titre (table Clients) ─ inconnue ? mail de choix, ou style « default »
        ├─ crée la ligne Jobs (status = dispatched)
        └─ lance GitHub Actions « Sous-titres » (branche main), phase « transcription »
GitHub Actions (scripts/process.py + render.py, FFmpeg, Groq Whisper large-v3)
   └─ rappelle le webhook n8n « pstb-sous-titrage-callback » à chaque phase
n8n, par phase
   1. transcription prête  → Agent sous-titrage (Claude Sonnet 5) → lance la phase « render »
   2. rendu prêt           → Agent contrôle qualité (Claude Sonnet 5, 2 captures)
                              ├─ ok, ou 2e tentative atteinte → dépôt dans Drive « Valider » + mail
                              └─ à refaire (1 seule reprise)  → relance « render » corrigé
   3. mail « Nouveau fichier à valider » (Approuver / Refuser) → Jobs.status = approved | rejected
   4. échec du runner       → Jobs.status = failed + mail d'alerte
Supabase : Clients (styles, glossaire, skill_md), Jobs (suivi), Animations ; bucket privé « sorties »
```

Groupes de nœuds n8n : « Lancer le sous-titrage », « Écrire les sous-titres », « Contrôler le rendu », « Signaler un échec », « Déposer pour validation ».

Réglages clés : agents avec 2 essais maximum ; Claude Sonnet 5, réflexion désactivée (10 000 jetons pour le sous-titrage, 4 000 pour le contrôle) ; une seule reprise corrigée après le contrôle ; mails envoyés par Gmail ; installation de FFmpeg par binaire statique avec 3 essais.

## Restaurer

Ne rien exécuter sans avoir décidé de revenir en arrière. Les trois couches se restaurent séparément.

### 1. Le code (GitHub)

`main` est protégé : on restaure par une branche et une demande de fusion.

```bash
git fetch --all --tags
git checkout -b restauration-avant-V1 origin/main
git read-tree -u --reset backup-before-V1     # l'état des fichiers devient exactement celui du tag
git commit -m "Restauration de l'etat backup-before-V1"
git push -u origin restauration-avant-V1      # puis ouvrir une demande de fusion vers main
```

Pour seulement consulter la version sauvegardée : `git checkout backup-before-V1`.
Pour un seul fichier : `git checkout backup-before-V1 -- chemin/du/fichier`.

### 2. Le workflow n8n

- **Voie la plus simple, si l'historique existe encore** : n8n, ouvrir « Devpost-Workflow », menu « … », historique des versions, choisir la version `e075e0db`, restaurer. L'historique n'est conservé que quelques jours selon la formule : ne pas compter dessus longtemps.
- **Sinon, avec la copie complète privée** : n8n, « Import from file », puis rattacher les 4 connexions OAuth et vérifier les nœuds Claude (crédits Gateway).
- **Sinon, avec l'export du dépôt** : l'importer, puis remplacer les repères ci-dessous et rattacher les connexions.

| Repère dans l'export du dépôt | À remplacer par |
| --- | --- |
| `__N8N_INSTANCE__` | le nom de l'instance n8n Cloud (partie avant `.app.n8n.cloud`) |
| `__SUPABASE_PROJECT_REF__` | la référence du projet Supabase |
| `__DRIVE_DOSSIER_ENTREE_ID__` | identifiant du dossier Drive « Ajouter les sous-titres » |
| `__DRIVE_DOSSIER_VALIDER_ID__` | identifiant du sous-dossier « Valider » |
| `__DRIVE_DOSSIER_PARENT_ID__` | identifiant du dossier « Capt-Production » |
| `__EMAIL_DESTINATAIRE__` | l'adresse qui reçoit les mails (3 nœuds Gmail) |

Après import : republier le workflow, puis vérifier que l'adresse du webhook de retour correspond à `CAPT_CALLBACK_URL` / `callback_url`.

### 3. La base Supabase

Ouvrir le SQL Editor et exécuter `backup/supabase/restore.sql` (remplace `Clients` et `Animations` par les copies ; `Jobs` en option). Les empreintes à retrouver sont écrites dans le script. Pour recréer les tables à blanc : `backup/supabase/schema.sql`.

## Vérification de la sauvegarde (19 novembre 2026)

- Copies Supabase : 7 / 18 / 1 lignes, empreintes MD5 identiques aux tables d'origine (`Clients` `4bc9d144…`, `Jobs` `78d20ff2…`, `Animations` `b8f4f3bb…`), RLS activée, aucune politique.
- Export n8n : version `e075e0db` identique à la version active, 46 nœuds, aucun jeton, aucune clé ni adresse personnelle dans la copie du dépôt.
- Dépôt : aucun secret détecté dans `main` ni dans ce dossier.

## Points d'attention

- Une clé `SUPABASE_SERVICE_KEY` a été exposée pendant la construction du projet : la régénérer et mettre à jour le secret GitHub.
- Le webhook de retour n8n n'a aucune authentification : ne pas publier son adresse.
- Deux anciennes demandes de fusion (n° 10 et 11) sont restées ouvertes ; elles ne font pas partie de cette sauvegarde.
