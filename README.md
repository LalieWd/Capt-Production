# Capt-Production : sous-titrage automatique de vidéos

Chaîne : dossier Drive « Ajouter les sous-titres » → n8n → ce dépôt (GitHub Actions) → Supabase → validation par e-mail → dépôt Drive.

## Organisation du dépôt

| Dossier / fichier | Rôle |
|---|---|
| `.github/workflows/subtitles.yml` | Workflow « Sous-titres » appelé par n8n (phases `transcription` puis `render`). Ne pas renommer ce fichier : n8n le retrouve par son identifiant. |
| `scripts/process.py` | Transcription (Groq Whisper), phases n8n, rendu ffmpeg, captures. |
| `scripts/render.py` | Style client → blocs → fichiers ASS/SRT. |
| `styles/` | Styles de repli (`mazars`, `corporate`, `impact`, `karaoke`, `default`), utilisés si Supabase est injoignable. |
| `clients.json` | Alias des clients pour la détection de repli. |
| `fonts/` | Polices des clients (voir `fonts/README.md`). |
| `inputs/` | Vidéos de test (déjà dans le dépôt, utilisables via `video_path`). |

La source de vérité des clients (style, glossaire, skill) est la table `Clients` de Supabase.

## Secrets GitHub requis

`GROQ_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`.
