# Capt-Production — sous-titres automatiques

Pipeline : dossier Drive « Ajouter les sous-titres » → n8n (workflow *Devpost-Workflow*) → ce dépôt (GitHub Actions, workflow *Sous-titres*) → Supabase → validation par e-mail → dépôt dans Drive.

## Organisation du dépôt

| Dossier / fichier | Rôle |
|---|---|
| `.github/workflows/subtitles.yml` | Le robot (runner) lancé par n8n. Ne pas renommer : n8n l'appelle par son nom. |
| `scripts/process.py` | Chef d'orchestre : transcription (Groq Whisper), rendu, captures, retour vers n8n. |
| `scripts/render.py` | Découpage en blocs, fichiers `.ass` / `.srt`, style. |
| `styles/` | Styles de secours (`mazars`, `corporate`, `impact`, `karaoke`, `default`), au format mobile 1080×1920. Supabase (`Clients.style`) reste la source de vérité. |
| `fonts/` | Polices des clients (voir `fonts/README.md` pour les licences). |
| `clients.json` | Liste de secours des clients si Supabase est injoignable. |

## Phases du robot (pilotées par n8n)

- `transcription` : vidéo → `mots.json` (mot, début, fin).
- `render` : sous-titres validés par l'agent → vidéo incrustée, `.srt`, captures pour le contrôle qualité.

## Style par défaut (repli)

Le style « Par défaut » (`Clients.code = 'default'`) s'applique tout seul quand la marque est inconnue, ambiguë dans le nom du fichier, ou que son style est inutilisable (bloc manquant, valeur invalide). Le rapport (`report.json` > `alertes`) dit pourquoi.

## Format mobile

Les styles génériques (`default`, `corporate`, `impact`, `karaoke`) sont réglés pour 1080×1920 avec des marges de sécurité : 120 px de chaque côté, texte au-dessus des 420 px du bas et sous les 250 px du haut. Leurs lignes sont mesurées avec la vraie police (`mesure_exacte`) et un bloc qui déborde est réduit.

## Gamme « animation » : impact

L'agent peut mettre en valeur un mot ou une courte phrase avec l'effet **impact**, quelle que soit la marque : même police que le texte de la marque, couleur de marque, et en plus **gras**, **agrandi** et/ou **capitales**. Champ `impact` de `subs.json` : `[{"texte": "mots exacts du sous-titre", "effets": ["agrandi"]}]`.

- Réglages par défaut : table Supabase `Animations` (ligne `impact`).
- Réglages par marque : `Clients.style.impact` (`effets_autorises`, `effets_defaut`, `echelle`, `couleur`, `police_gras`, `max_par_bloc`). Un effet non autorisé par la marque est ignoré.

Secrets GitHub requis : `GROQ_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`.
