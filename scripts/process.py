"""CAPT - video -> Whisper (Groq) -> correction -> style client -> video sous-titree.

Usage : python3 scripts/process.py <video> <nom_video_origine> <client> <dossier_sortie> [style_choisi]
  - style_choisi : libelle du menu du formulaire (clients.json > menu). Vide ou "Automatique" = detection
    d'apres nom_video_origine et client (clients.json > clients).
  - sortie : subtitled.mp4, subtitles.ass, subtitles.srt, report.json
Variable d'environnement requise : GROQ_API_KEY
Option de test : MOTS_JSON=fichier.json saute la transcription.
"""
import json, os, re, subprocess, sys, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render

_ICI = os.path.dirname(os.path.abspath(__file__))
# Fonctionne avec des dossiers (scripts/, styles/, fonts/) comme avec tous les fichiers a plat a la racine.
RACINE = os.path.dirname(_ICI) if os.path.basename(_ICI) == "scripts" else _ICI
DOSSIER_STYLES = os.path.join(RACINE, "styles") if os.path.isdir(os.path.join(RACINE, "styles")) else RACINE
DOSSIER_POLICES = os.path.join(RACINE, "fonts") if os.path.isdir(os.path.join(RACINE, "fonts")) else RACINE


def trouver_police(chemin):
    """Retourne le chemin reel d'un fichier de police (dans fonts/ ou a la racine), ou None.
    Tolere les variantes de nom : Halyard_Display_Bold.otf = HalyardDisplay-Bold.otf."""
    if not chemin:
        return None
    def simple(n):
        return "".join(c for c in n.lower() if c.isalnum())
    cible = simple(os.path.basename(chemin))
    for dossier in (DOSSIER_POLICES, RACINE):
        try:
            for f in os.listdir(dossier):
                if f.lower().endswith((".otf", ".ttf")) and simple(f) == cible:
                    return os.path.join(dossier, f)
        except OSError:
            pass
    return None


LOW_LOGPROB = -0.55
MODELE_LLM = os.environ.get("MODELE_LLM", "llama-3.3-70b-versatile")


def sh(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def extract_audio(video, audio):
    sh(["ffmpeg", "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", audio])


def transcribe(audio):
    cmd = ["curl", "-sS", "--fail-with-body", "https://api.groq.com/openai/v1/audio/transcriptions",
           "-H", f"Authorization: Bearer {os.environ['GROQ_API_KEY']}",
           "-F", f"file=@{audio}", "-F", "model=whisper-large-v3",
           "-F", "response_format=verbose_json", "-F", "language=fr",
           "-F", "timestamp_granularities[]=word", "-F", "timestamp_granularities[]=segment"]
    return json.loads(sh(cmd))


def dimensions(video):
    out = sh(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:stream_tags=rotate",
              "-of", "json", video])
    s = json.loads(out)["streams"][0]
    w, h = s["width"], s["height"]
    if str(s.get("tags", {}).get("rotate", "0")) in ("90", "270"):
        w, h = h, w
    return w, h


def corriger_avec_llm(mots, style, nom_client):
    """Une seule requete : corrige les erreurs de transcription, signale les doutes, choisit les mots cles.
    Reponse par numero de mot : les timings de Whisper ne sont jamais touches."""
    cle = os.environ.get("GROQ_API_KEY")
    if not cle:
        return [], [], "GROQ_API_KEY absente : correction par IA sautee"
    glossaire = "\n".join(f"- ecrire « {g['ecrire']} », jamais : {', '.join(g.get('jamais', [])) or '(aucun)'}"
                          for g in style.get("glossaire", [])) or "(aucun glossaire)"
    mots_cles = style["mots_cles"]
    veut_cles = mots_cles.get("actif", False)
    consigne = f"""Tu corriges la transcription automatique (Whisper) d'une courte video francaise{f' pour le client {nom_client}' if nom_client else ''}.
Les mots sont numerotes. Tu ne reformules rien, tu ne resumes rien : le sous-titre suit exactement ce qui est dit.
Tu corriges uniquement : noms propres, sigles, jargon, homophones (a/à, sait/c'est, ou/où, et/est...), accords evidents.
Glossaire du client (fait foi) :
{glossaire}
Pour remplacer un mot, donne son numero et le nouveau texte. Pour fusionner plusieurs mots (ex. « F S I » -> « FSI »), mets le texte fusionne sur le premier numero et "" sur les suivants.
Si tu doutes d'un nom propre ou d'un mot, ne le change pas et ajoute-le a "doutes".
{"Mots cles : choisis des mots porteurs de sens (noms, chiffres, adjectifs forts, noms propres du client), jamais des mots de liaison, environ " + str(mots_cles.get('frequence', '1 bloc sur 3')) + ". Niveau « accent » pour le nom du metier/de l'equipe ou une image marquante, sinon « principal »." if veut_cles else "Ne choisis aucun mot cle (liste vide)."}
Reponds uniquement en JSON : {{"corrections":[{{"i":0,"nouveau":"..."}}],"doutes":[{{"i":0,"raison":"..."}}],"mots_cles":[{{"i":0,"niveau":"accent|principal"}}]}}"""
    texte = " ".join(f"{i}:{m['mot']}" for i, m in enumerate(mots))
    corps = json.dumps({"model": MODELE_LLM, "temperature": 0, "response_format": {"type": "json_object"},
                        "messages": [{"role": "system", "content": consigne}, {"role": "user", "content": texte}]}).encode()
    req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", data=corps,
                                 headers={"Authorization": f"Bearer {cle}", "Content-Type": "application/json",
                                          "User-Agent": "capt-sous-titres/1.0"})
    try:
        rep = json.loads(urllib.request.urlopen(req, timeout=90).read())
        data = json.loads(rep["choices"][0]["message"]["content"])
    except Exception as e:  # on ne bloque jamais le rendu a cause de la correction
        detail = ""
        if hasattr(e, "read"):
            try:
                detail = " : " + e.read().decode("utf-8", "ignore")[:200]
            except Exception:
                pass
        return [], [], f"correction par IA impossible ({type(e).__name__}{getattr(e, 'code', '')}{detail}) : transcription gardee telle quelle"
    corrections = []
    for c in data.get("corrections", []):
        i = c.get("i")
        if isinstance(i, int) and 0 <= i < len(mots) and isinstance(c.get("nouveau"), str):
            nouveau = c["nouveau"].strip()
            if nouveau != mots[i]["mot"]:
                corrections.append({"avant": mots[i]["mot"], "apres": nouveau, "a": round(mots[i]["debut"], 2),
                                    "source": "ia", "i": i})
                mots[i]["mot"] = nouveau
    doutes = [{"a": round(mots[d["i"]]["debut"], 2), "texte": f"{mots[d['i']]['mot']} : {d.get('raison', '')}".strip(" :")}
              for d in data.get("doutes", []) if isinstance(d.get("i"), int) and 0 <= d["i"] < len(mots)]
    if veut_cles:
        for k in data.get("mots_cles", []):
            i = k.get("i")
            if isinstance(i, int) and 0 <= i < len(mots) and mots[i]["mot"].strip():
                mots[i]["cle"] = "accent" if k.get("niveau") == "accent" else "principal"
    return corrections, doutes, None


def main(video, nom_video, client_saisi, sortie, style_choisi=""):
    os.makedirs(sortie, exist_ok=True)
    rapport = {"statut": "ok", "alertes": [], "corrections": [], "doutes": []}

    def ecrire_rapport():
        json.dump(rapport, open(os.path.join(sortie, "report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 1. style : choisi dans le formulaire, sinon detecte d'apres le nom du fichier et le champ client
    cfg = json.load(open(os.path.join(RACINE, "clients.json"), encoding="utf-8"))
    choix = next((m["style"] for m in cfg.get("menu", []) if m["label"] == style_choisi.strip()), None)
    if choix:
        nom, fichier_style, alias, erreur = None, choix, "choix manuel (formulaire)", None
        for cl in cfg["clients"]:
            if cl["style"] == choix:
                nom = cl["nom"]
        if not os.path.exists(os.path.join(DOSSIER_STYLES, choix + ".json")):
            erreur = f"style introuvable : {choix}"
    else:
        nom, fichier_style, alias, erreur = render.choisir_style(
            os.path.join(RACINE, "clients.json"), f"{nom_video} {client_saisi}")
    if erreur:
        rapport.update(statut="erreur", raison=erreur)
        ecrire_rapport()
        print("ERREUR :", erreur)
        sys.exit(1)
    style = json.load(open(os.path.join(DOSSIER_STYLES, fichier_style + ".json"), encoding="utf-8"))
    rapport.update(client=nom or client_saisi or "", style=fichier_style, alias_detecte=alias)
    if nom is None and not choix:
        rapport["alertes"].append("client non reconnu : style par defaut utilise")

    # 2. police
    p = style["police"]
    fichier_police = p.get("fichier")
    alternatif = p.get("fichier_alternatif")
    if trouver_police(fichier_police):
        nom_police = p["famille"] + (f" {p['graisse']}" if p.get("graisse") and p["graisse"] not in ("Regular", "Bold") else "")
    elif trouver_police(alternatif):
        nom_police = p["famille"]
        rapport["alertes"].append(f"police {p['famille']} {p.get('graisse', '')} absente : version de remplacement utilisee ({os.path.basename(alternatif)}), a verifier avec le client")
    else:
        nom_police = p.get("fallback", "Inter")
        if fichier_police:
            rapport["alertes"].append(f"police du client absente ({fichier_police}) : police de remplacement « {nom_police} » utilisee")

    # 3. transcription
    if os.environ.get("MOTS_JSON"):
        mots_whisper = json.load(open(os.environ["MOTS_JSON"], encoding="utf-8"))
        segments, duree = [], None
    else:
        audio = os.path.join(sortie, "audio.mp3")
        extract_audio(video, audio)
        resp = transcribe(audio)
        mots_whisper = [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in resp.get("words", [])]
        segments, duree = resp.get("segments", []), resp.get("duration")
    mots = [{"mot": w["word"].strip(), "debut": w["start"], "fin": w["end"], "cle": None}
            for w in mots_whisper if w["word"].strip()]

    # 4. corrections : glossaire (sur), puis IA (avec doutes et mots cles)
    mots, corr = render.appliquer_glossaire(mots, style.get("glossaire", []))
    corr_ia, doutes, alerte = corriger_avec_llm(mots, style, nom)
    # l'IA peut avoir fusionne des mots avec "" : on les retire en gardant debut/fin
    propres = []
    for m in mots:
        if m["mot"] == "" and propres:
            propres[-1]["fin"] = max(propres[-1]["fin"], m["fin"])
        elif m["mot"] != "":
            propres.append(m)
    mots = propres
    rapport["corrections"] = corr + corr_ia
    rapport["doutes"] = doutes
    if alerte:
        rapport["alertes"].append(alerte)
    for s in segments:
        if s.get("avg_logprob", 0) < LOW_LOGPROB:
            rapport["doutes"].append({"a": round(s["start"], 2), "texte": "passage peu sur : " + s["text"].strip()})
    rapport["low_confidence"] = [d for d in rapport["doutes"]]

    # 5. blocs, ASS, SRT
    largeur, hauteur = dimensions(video)
    if largeur > style["resolution"][0]:       # on ne depasse pas 1080 de large : fichier plus leger
        hauteur = int(round(hauteur * style["resolution"][0] / largeur / 2) * 2)
        largeur = style["resolution"][0]
    blocs = render.construire_blocs(mots, style)
    ass = render.generer_ass(blocs, style, largeur, hauteur, nom_police)
    open(os.path.join(sortie, "subtitles.ass"), "w", encoding="utf-8").write(ass)
    srt = render.generer_srt(blocs)
    open(os.path.join(sortie, "subtitles.srt"), "w", encoding="utf-8").write(srt)

    # 6. rendu
    dossier_polices = DOSSIER_POLICES
    filtre = f"scale={largeur}:{hauteur},ass={os.path.join(sortie, 'subtitles.ass')}:fontsdir={dossier_polices}"
    subprocess.run(["ffmpeg", "-y", "-i", video, "-vf", filtre, "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "24", "-pix_fmt", "yuv420p", "-c:a", "copy", os.path.join(sortie, "subtitled.mp4")],
                   check=True, capture_output=True)

    rapport.update(duration=duree, subtitles=len(blocs), text=" ".join(m["mot"] for m in mots))
    if rapport["alertes"]:
        rapport["statut"] = "ok_avec_alertes"
    ecrire_rapport()
    print(f"{len(blocs)} blocs, {len(rapport['corrections'])} corrections, {len(rapport['doutes'])} doutes, style {fichier_style}")


if __name__ == "__main__":
    if len(sys.argv) not in (5, 6):
        sys.exit(__doc__)
    main(*sys.argv[1:6])
