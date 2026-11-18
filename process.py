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
MODELE_LLM = os.environ.get("MODELE_LLM", "openai/gpt-oss-120b")


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


def charger_clients_supabase():
    """Lit la table clients (source de verite des styles). Retourne (lignes, erreur)."""
    url, cle = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not url or not cle:
        return None, "SUPABASE_URL / SUPABASE_KEY absents"
    en_tetes = {"apikey": cle, "User-Agent": "capt-sous-titres/1.0"}
    if cle.startswith("eyJ"):          # ancienne cle JWT ; les nouvelles cles (sb_secret_...) n'ont que apikey
        en_tetes["Authorization"] = f"Bearer {cle}"
    req = urllib.request.Request(url.rstrip("/") + "/rest/v1/clients?select=id,menu_label,code,alias,actif,style,glossaire&actif=eq.true",
                                 headers=en_tetes)
    try:
        lignes = json.loads(urllib.request.urlopen(req, timeout=30).read())
    except Exception as e:
        return None, f"Supabase injoignable ({type(e).__name__}{getattr(e, 'code', '')})"
    if not lignes:
        return None, "table clients vide"
    return lignes, None


def choisir_ligne(lignes, client_saisi, nom_video, style_choisi):
    """Retourne (ligne, alias_ou_raison, erreur, inconnu)."""
    choisi = (style_choisi or "").strip()
    if choisi and not choisi.lower().startswith("automatique"):
        for l in lignes:
            if l["menu_label"] == choisi:
                return l, "choix manuel (formulaire)", None, False
        return None, None, f"style introuvable : {choisi}", False
    code = render.normaliser(client_saisi or "")
    if code:
        for l in lignes:
            if l.get("code") and render.normaliser(l["code"]) == code:
                return l, f"code {l['code']}", None, False
    norm = " " + render.normaliser(f"{nom_video} {client_saisi}") + " "
    trouves = {}
    for l in lignes:
        for alias in sorted(l.get("alias") or [], key=len, reverse=True):
            if f" {render.normaliser(alias)} " in norm:
                trouves[l["menu_label"]] = (l, alias)
                break
    if len(trouves) > 1:
        return None, None, "client ambigu : " + ", ".join(trouves), False
    if len(trouves) == 1:
        l, alias = next(iter(trouves.values()))
        return l, alias, None, False
    defaut = next((l for l in lignes if render.normaliser(l["menu_label"]) == "par defaut"), None)
    if defaut:
        return defaut, None, None, True
    return None, None, f"client non reconnu : {nom_video}", False


def style_depuis_ligne(ligne):
    style = dict(ligne["style"])
    if ligne.get("glossaire"):
        style["glossaire"] = ligne["glossaire"]
    style.setdefault("glossaire", [])
    return style


ONOMATOPEES = {"euh", "heu", "euhh", "euuh", "hum", "hmm", "hmmm", "mmh", "mmm", "hem", "eh"}


def nettoyer_parasites(mots):
    """Filtre sur, sans IA : onomatopees d'hesitation et begaiements de petits mots (« je je je »)."""
    sortie, retires = [], []
    for m in mots:
        brut = re.sub(r"[^\wàâäéèêëîïôöùûüç]", "", m["mot"].lower())
        if brut in ONOMATOPEES:
            retires.append(m)
            if sortie:
                sortie[-1]["fin"] = max(sortie[-1]["fin"], m["fin"])
            continue
        if sortie and len(brut) <= 3 and brut and brut == re.sub(r"[^\wàâäéèêëîïôöùûüç]", "", sortie[-1]["mot"].lower()):
            sortie[-1]["fin"] = m["fin"]       # begaiement : on garde un seul mot, etire sur les deux
            retires.append(m)
            continue
        sortie.append(m)
    return sortie, [{"avant": r["mot"], "apres": "", "a": round(r["debut"], 2), "source": "regle"} for r in retires]


MOTS_VIDES = set("""le la les un une des du de d l au aux ce cet cette ces mon ma mes ton ta tes son sa ses notre nos votre vos leur leurs
et ou mais donc or ni car que qui quoi dont où si ne pas plus pour par sur sous dans avec sans chez vers entre
je tu il elle on nous vous ils elles me te se lui y en est es suis sont etait été etre être a as ai avons avez ont
ca ça cela c qu j m t s n très tout tous toute toutes bien bah ben euh ouais oui non ok alors donc comme aussi
fait faire faut peux peut veux veut vais va vas allez dit dire là ici quand quoi comment pourquoi""".split())


def mots_cles_secours(mots, style):
    """Si l'IA ne renvoie aucun mot cle : choix de secours par regles. Un mot cle par groupe de ~5 mots,
    un groupe sur deux ; le 3e est en accent (bleu). Le limiteur de blocs fait le reste."""
    import unicodedata
    def net(m):
        return "".join(ch for ch in unicodedata.normalize("NFD", m.lower()) if unicodedata.category(ch) != "Mn").strip(".,;:!?…\"'()« »-").replace("’", "'").split("'")[-1]
    groupes, cur = [], []
    for i, m in enumerate(mots):
        cur.append(i)
        if len(cur) >= 5 or m["mot"].rstrip().endswith((".", "?", "!", "…")):
            groupes.append(cur)
            cur = []
    if cur:
        groupes.append(cur)
    n = 0
    for gi, g in enumerate(groupes):
        if gi % 2:
            continue
        def score(i):
            w = net(mots[i]["mot"])
            if not w or w in MOTS_VIDES or net(mots[i]["mot"]) in MOTS_VIDES:
                return -1
            return (3 if any(ch.isdigit() for ch in w) else 0) + len(w) + (2 if mots[i]["mot"][:1].isupper() and i not in (g[0],) else 0)
        best = max(g, key=score)
        if score(best) < 4:
            continue
        mots[best]["cle"] = "accent" if n % 3 == 2 else "principal"
        n += 1
    return n


def corriger_avec_llm(mots, style, nom_client):
    """Une seule requete : corrige les erreurs de transcription, signale les doutes, choisit les mots cles.
    Reponse par numero de mot : les timings de Whisper ne sont jamais touches."""
    cle = os.environ.get("GROQ_API_KEY")
    if not cle:
        return [], [], "GROQ_API_KEY absente : correction par IA sautee"
    glossaire = "\n".join(f"- ecrire « {g['ecrire']} », jamais : {', '.join(g.get('jamais', [])) or '(aucun)'}"
                          + (f" ({g['note']})" if g.get("note") else "")
                          for g in style.get("glossaire", [])) or "(aucun glossaire)"
    regle_chiffres = ("\nEcris les nombres, heures et montants en chiffres (18h, 3 ans, 20 %)."
                      if style.get("contenu", {}).get("chiffres_en_chiffres") else "")
    mots_cles = style["mots_cles"]
    veut_cles = mots_cles.get("actif", False)
    consigne = f"""Tu corriges la transcription automatique (Whisper) d'une courte video francaise{f' pour le client {nom_client}' if nom_client else ''}.
Les mots sont numerotes. Tu ne reformules pas, tu ne resumes pas : le sous-titre garde exactement le sens et les mots importants de ce qui est dit.
Tu corriges : noms propres, sigles, jargon, homophones (a/à, sait/c'est, ou/où, et/est...), accords evidents.
Tu NETTOIES l'oral pour obtenir des sous-titres propres, en proposant de SUPPRIMER (liste "supprimer", par numero) :
- onomatopees et bruits : euh, hum, hmm, bah, ben, ah, oh, hein... quand ils ne portent aucun sens ;
- repetitions inutiles (« je, je, je pense » -> garde un seul « je ») et faux departs ;
- hesitations et tics de langage sans apport (« comment dire », « tu vois », « en fait », « du coup », « voilà », « donc », « alors ») SEULEMENT s'ils sont vides de sens ou excessifs ;
- fragments, syllabes isolees, mots mal transcrits sans sens.
Ne supprime JAMAIS un mot qui porte une information, une reponse (« oui », « non », « ok » comme reponse), un nom, un chiffre, une emotion voulue ou une repetition volontaire pour l'effet (« attends attends », « enchante enchante » quand c'est dit pour marquer). En cas de doute, garde le mot.
Tu ajoutes la PONCTUATION manquante (liste "ponctuation" : numero du mot et signe a mettre APRES lui : "," "." "?" "!" "…"). Fin de phrase = point ; question = « ? » ; virgule aux pauses naturelles. Ne cree pas de ponctuation deja presente.
Glossaire du client (fait foi) :
{glossaire}{regle_chiffres}
Pour remplacer un mot, donne son numero et le nouveau texte. Pour fusionner plusieurs mots (ex. « F S I » -> « FSI »), mets le texte fusionne sur le premier numero et "" sur les suivants.
Si tu doutes d'un nom propre ou d'un mot, ne le change pas et ajoute-le a "doutes".
{"Mots cles : choisis des mots porteurs de sens (noms, chiffres, adjectifs forts, noms propres du client), jamais des mots de liaison, environ " + str(mots_cles.get('frequence', '1 bloc sur 3')) + ". Deux niveaux : « principal » (blanc, gras, plus gros) pour les mots importants ; « accent » (couleur de la marque) UNIQUEMENT pour les mots que la personne appuie ou accentue vraiment : mot insiste, exclamation, chiffre ou idee cle, mot qui marque l'emotion ou l'humour. L'accent est rare : au plus un mot sur trois blocs a mots cles." if veut_cles else "Ne choisis aucun mot cle (liste vide)."}
Reponds uniquement en JSON : {{"corrections":[{{"i":0,"nouveau":"..."}}],"supprimer":[{{"i":0,"raison":"onomatopee|repetition|hesitation|tic|fragment"}}],"ponctuation":[{{"i":0,"signe":","}}],"doutes":[{{"i":0,"raison":"..."}}],"mots_cles":[{{"i":0,"niveau":"accent|principal"}}]}}"""
    texte = " ".join(f"{i}:{m['mot']}" for i, m in enumerate(mots))
    requete = {"model": MODELE_LLM, "temperature": 0, "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": consigne}, {"role": "user", "content": texte}]}
    if MODELE_LLM.startswith("openai/gpt-oss"):
        requete["reasoning_effort"] = "low"          # modele a raisonnement : on limite l'attente
    corps = json.dumps(requete).encode()
    req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", data=corps,
                                 headers={"Authorization": f"Bearer {cle}", "Content-Type": "application/json",
                                          "User-Agent": "capt-sous-titres/1.0"})
    try:
        rep = json.loads(urllib.request.urlopen(req, timeout=90).read())
        contenu = rep["choices"][0]["message"].get("content") or ""
        if not contenu.strip() and MODELE_LLM.startswith("openai/gpt-oss"):   # raisonnement a tout consomme : on reessaie plus large
            requete["reasoning_effort"] = "medium"
            req = urllib.request.Request(req.full_url, data=json.dumps(requete).encode(), headers=dict(req.header_items()))
            rep = json.loads(urllib.request.urlopen(req, timeout=120).read())
            contenu = rep["choices"][0]["message"].get("content") or ""
        data = json.loads(contenu)
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
    # suppressions (onomatopees, repetitions, tics) : plafonnees a 15 % des mots, jamais de mot d'un glossaire
    supprimes = 0
    plafond = max(3, int(len(mots) * 0.15))
    for sp in data.get("supprimer", []):
        i = sp.get("i") if isinstance(sp, dict) else sp
        if supprimes >= plafond:
            break
        if isinstance(i, int) and 0 <= i < len(mots) and mots[i]["mot"].strip():
            corrections.append({"avant": mots[i]["mot"], "apres": "", "a": round(mots[i]["debut"], 2),
                                "source": "ia-nettoyage", "raison": (sp.get("raison", "") if isinstance(sp, dict) else "")})
            mots[i]["mot"] = ""
            supprimes += 1
    for pn in data.get("ponctuation", []):
        i, signe = pn.get("i"), pn.get("signe")
        if isinstance(i, int) and 0 <= i < len(mots) and signe in (",", ".", "?", "!", "…") and mots[i]["mot"]:
            if not re.search(r"[.,;:!?…]$", mots[i]["mot"]):
                mots[i]["mot"] += signe
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

    # 1. style : Supabase (source de verite) ; sinon fichiers locaux (clients.json + styles/)
    lignes, err_sb = charger_clients_supabase()
    if lignes is not None:
        ligne, alias, erreur, inconnu = choisir_ligne(lignes, client_saisi, nom_video, style_choisi)
        if erreur:
            rapport.update(statut="erreur", raison=erreur)
            ecrire_rapport()
            print("ERREUR :", erreur)
            sys.exit(1)
        style = style_depuis_ligne(ligne)
        fichier_style = ligne["menu_label"]
        nom = None if inconnu else (style.get("client") or ligne["menu_label"])
        choix = None if inconnu else fichier_style
        rapport.update(style_source="supabase", style_version=style.get("version"))
    else:
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
        rapport.update(style_source="fichiers locaux")
        rapport["alertes"].append(f"{err_sb} : styles locaux utilises")
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
    mots, corr_nettoyage = nettoyer_parasites(mots)
    corr = corr + corr_nettoyage
    corr_ia, doutes, alerte = corriger_avec_llm(mots, style, nom)
    nb_cles_ia = sum(1 for m in mots if m.get("cle"))
    rapport["llm"] = {"modele": MODELE_LLM, "corrections": len(corr_ia), "doutes": len(doutes), "mots_cles": nb_cles_ia}
    if style["mots_cles"].get("actif") and nb_cles_ia == 0:
        n_sec = mots_cles_secours(mots, style)
        rapport["alertes"].append(f"IA : aucun mot cle renvoye, {n_sec} mots cles choisis par regles de secours")
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
    titres = []
    chemin_titres = os.environ.get("TITRES_JSON") or os.path.splitext(video)[0] + ".titres.json"
    if os.path.exists(chemin_titres):
        try:
            titres = json.load(open(chemin_titres, encoding="utf-8"))
            rapport["bandeaux"] = len(titres)
        except Exception as e:
            rapport["alertes"].append(f"titres ignores ({e})")
    ass = render.generer_ass(blocs, style, largeur, hauteur, nom_police, titres, DOSSIER_POLICES)
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
