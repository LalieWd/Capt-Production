# Polices des sous-titres

`render.py` doit lancer FFmpeg avec `subtitles=subs.ass:fontsdir=fonts`.
Le nom à écrire dans le fichier .ass est dans la colonne `Nom ASS` ci-dessous (et dans Supabase : `clients.style.police.nom_ass`).

| Fichier | Nom ASS | Client | Licence | Statut |
|---|---|---|---|---|
| `InterTight-Black.otf` | Inter Tight Black | PST&B (texte + mots-clés) | SIL OFL (libre) | ✅ présent |
| `Montserrat-Medium.otf` | Montserrat Medium | Crit (mots-clés blancs) | SIL OFL (libre) | ✅ présent |
| `Inter-Medium.otf` | Inter Medium | Secours Forvis Mazars | SIL OFL (libre) | ✅ présent |
| `Inter-Bold.otf` | Inter (gras) | Secours Crit | SIL OFL (libre) | ✅ présent |
| `Inter-Black.otf` | Inter Black | Secours PST&B | SIL OFL (libre) | ✅ présent |
| `HalyardDisplay-Medium.otf` | Halyard Display Medium | Forvis Mazars | Commerciale (Darden Studio) | ✅ présent — licence à régulariser |
| `CircularStd-Bold.otf` | Circular Std Bold | Crit (texte + mots-clés rouges) | Commerciale (Lineto) | ✅ présent — licence à régulariser |

`InterTight-Black.otf` est une instance statique (graisse 900) de la police variable Inter Tight de Google Fonts, convertie en OTF.

## Les deux polices commerciales

Halyard Display et Circular Std n'ont pas de version gratuite officielle. Les « versions free » trouvées sur des sites de téléchargement de polices sont des copies non autorisées : ne pas les utiliser pour des vidéos clients.

Pour les obtenir légalement :
- **Halyard Display** : licence desktop chez Darden Studio, ou demander le fichier à Forvis Mazars si c'est leur police de marque.
- **Circular Std** : licence desktop chez Lineto, ou demander le fichier à Crit / vérifier si l'agence a déjà une licence.

Les fichiers actuels ont été fournis sans licence vérifiée (Halyard vient de fonnts.com). À remplacer par des fichiers sous licence avant toute livraison client.

Si une police est absente, `render.py` doit **échouer** (et pas remplacer en silence) — sinon le rendu ne ressemblera pas aux vidéos.
