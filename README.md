# Capt-Production — sous-titres automatiques

Pipeline : dossier Drive « Ajouter les sous-titres » → n8n (workflow *Devpost-Workflow*) → ce dépôt (GitHub Actions, workflow *Sous-titres*) → Supabase → validation par e-mail → dépôt dans Drive.

## Organisation du dépôt

| Dossier / fichier | Rôle |
|---|---|
| `.github/workflows/subtitles.yml` | Le robot (runner) lancé par n8n. Ne pas renommer : n8n l'appelle par son nom. |
| `scripts/process.py` | Chef d'orchestre : transcription (Groq Whisper), rendu, captures, retour vers n8n. |
| `scripts/render.py` | Découpage en blocs, fichiers `.ass` / `.srt`, style. |
| `styles/` | Styles de secours (`mazars`, `corporate`, `impact`, `karaoke`, `default`). Supabase (`Clients.style`) reste la source de vérité. |
| `fonts/` | Polices des clients (voir `fonts/README.md` pour les licences). |
| `clients.json` | Liste de secours des clients si Supabase est injoignable. |
| `inputs/` | Vidéos de test. |

## Phases du robot (pilotées par n8n)

- `transcription` : vidéo → `mots.json` (mot, début, fin).
- `render` : sous-titres validés par l'agent → vidéo incrustée, `.srt`, captures pour le contrôle qualité.

Secrets GitHub requis : `GROQ_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`.
