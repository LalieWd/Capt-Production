"""CAPT - rendu des sous-titres : style client -> blocs -> fichier ASS -> SRT.

Les mots arrivent sous la forme [{"mot": "Dans", "debut": 1.24, "fin": 1.41, "cle": None}, ...].
Les timings ne sont jamais inventes : ils viennent de Whisper.
"""
import json, os, re, unicodedata

NO_CUT_AFTER = {"le", "la", "les", "l'", "un", "une", "des", "de", "du", "d'", "au", "aux", "a", "à",
                "en", "dans", "sur", "pour", "avec", "sans", "par", "ce", "cette", "ces", "mon", "ma",
                "mes", "ton", "ta", "tes", "son", "sa", "ses", "notre", "votre", "leur", "que", "qui", "et"}
PAUSE_COUPE = 0.45     # pause (s) qui permet de couper un bloc
MIN_MOTS_BLOC = 3      # on evite les blocs de 1-2 mots (sauf fin de phrase)
MIN_DUREE = 0.30       # duree minimale d'un bloc (s)


# ---------- utilitaires ----------
def normaliser(texte):
    t = unicodedata.normalize("NFD", texte.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9']+", " ", t.replace("_", " ")).strip()


def couleur_ass(hexa, alpha=0):
    """#RRGGBB -> &HAABBGGRR (ASS est en BGR)."""
    h = hexa.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def temps_ass(t):
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def temps_srt(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


# ---------- choix du client ----------
def choisir_style(clients_path, texte_source):
    """Retourne (nom_client, fichier_style, alias, erreur). texte_source = nom de la video + champ client."""
    cfg = json.load(open(clients_path, encoding="utf-8"))
    norm = " " + normaliser(texte_source) + " "
    trouves = {}
    for c in cfg["clients"]:
        for alias in sorted(c["alias"], key=len, reverse=True):
            if f" {normaliser(alias)} " in norm:
                trouves[c["nom"]] = (c["style"], alias)
                break
    if len(trouves) > 1:
        return None, None, None, "client ambigu : " + ", ".join(trouves)
    if len(trouves) == 1:
        nom, (style, alias) = next(iter(trouves.items()))
        return nom, style, alias, None
    if cfg.get("si_client_inconnu", "erreur") == "defaut":
        return None, cfg.get("defaut", "default"), None, None
    connus = ", ".join(c["nom"] for c in cfg["clients"])
    return None, None, None, f"client non reconnu dans le nom : {texte_source} (clients connus : {connus})"


# ---------- corrections par glossaire ----------
def appliquer_glossaire(mots, glossaire):
    """Remplace des suites de mots par la graphie du glossaire. Garde debut du premier et fin du dernier.
    Une entree dont la note contient « mot-cle bleu » force ce mot en mot cle bleu (et fusionne aussi sa
    graphie correcte, ex. « Forvis » « Mazars » -> un seul mot)."""
    corrections = []
    regles = []
    for g in glossaire:
        forcee = "accent" if "mot cle bleu" in normaliser(g.get("note", "")) else None
        faux_liste = list(g.get("jamais", []))
        if forcee:
            faux_liste.append(g["ecrire"])
        for faux in faux_liste:
            regles.append((normaliser(faux).split(), g["ecrire"], forcee))
    regles.sort(key=lambda r: -len(r[0]))
    i = 0
    sortie = []
    while i < len(mots):
        applique = False
        for faux, juste, forcee in regles:
            n = len(faux)
            if not faux or i + n > len(mots):
                continue
            fenetre = [normaliser(m["mot"]) for m in mots[i:i + n]]
            if fenetre == faux:
                texte_avant = " ".join(m["mot"] for m in mots[i:i + n])
                ponct = re.search(r"[.,;:!?…]+$", mots[i + n - 1]["mot"])
                nouveau = juste + (ponct.group(0) if ponct else "")
                if texte_avant != nouveau:
                    corrections.append({"avant": texte_avant, "apres": nouveau, "a": round(mots[i]["debut"], 2),
                                        "source": "glossaire"})
                mot = {"mot": nouveau, "debut": mots[i]["debut"], "fin": mots[i + n - 1]["fin"], "cle": None}
                if forcee:
                    mot["cle_forcee"] = forcee
                sortie.append(mot)
                i += n
                applique = True
                break
        if not applique:
            sortie.append(dict(mots[i]))
            i += 1
    return sortie, corrections


# ---------- decoupage en blocs ----------
def taille_em(p):
    """Taille reelle du texte (em, en px). taille_px historique = taille ASS (= em x 1.377 pour Halyard)."""
    return p["taille_em_px"] if "taille_em_px" in p else p["taille_px"] / 1.377


def taille_ass(p):
    return p["taille_em_px"] * 1.377 if "taille_em_px" in p else p["taille_px"]


def largeur_estimee(texte, taille):
    return len(texte) * taille * 0.52


_DOSSIER_POLICES = "fonts"
_MESURES = {}


def police_mesure(style):
    """Fichier de police du style (pour mesurer le texte), ou None."""
    p = style["police"]
    candidats = []
    if p.get("fichier"):
        candidats.append(os.path.basename(p["fichier"]))
    candidats.append(f"{p['famille'].replace(' ', '')}-{p.get('graisse', 'Regular')}.otf")
    for nom in candidats:
        f = os.path.join(_DOSSIER_POLICES, nom)
        if os.path.exists(f):
            return f
    return None


def mesurer(texte, style):
    """Largeur reelle (px de la grille du style) d'un texte avec la police du style, ou None si impossible."""
    f = police_mesure(style)
    if not f:
        return None
    cle = (f, taille_ass(style["police"]))
    if cle not in _MESURES:
        from PIL import ImageFont
        ft = ImageFont.truetype(f, 200)
        asc, desc = ft.getmetrics()
        _MESURES[cle] = (ft, taille_ass(style["police"]) / ((asc + desc) / 200) / 200)   # px par unite de ft.getlength
    ft, k = _MESURES[cle]
    return ft.getlength(texte) * k


def mesure_active(style):
    """Mesure reelle du texte avec la police du style. Explicite via « mesure_exacte » ; sinon automatique des que
    la police n'a pas le meme rapport taille ASS / taille reelle que Halyard (1,377), sur lequel l'estimation historique
    est calee : sans cela, Circular Std ou Inter Tight sont sous-estimees de 15 a 40 % et les lignes debordent."""
    f = police_mesure(style)
    if not f:
        return False
    if style.get("mesure_exacte") is not None:
        return bool(style["mesure_exacte"])
    from PIL import ImageFont
    ft = ImageFont.truetype(f, 200)
    asc, desc = ft.getmetrics()
    return abs((asc + desc) / 200 - 1.377) > 0.05


def largeur_ligne(mots, style, taille):
    """Largeur d'une ligne : mesure reelle si active (voir mesure_active), sinon estimation historique."""
    if mesure_active(style):
        maj = style["police"].get("casse") == "majuscules"
        total = 0.0
        for i, w in enumerate(mots):
            texte = w["mot"].upper() if (maj or "capitales" in (w.get("impact") or [])) else w["mot"]
            facteur = w.get("impact_echelle", 1.0) if w.get("impact") else (style["mots_cles"]["echelle"] if w.get("cle") else 1.0)
            if "gras" in (w.get("impact") or []):
                facteur *= 1.05
            total += (mesurer(texte, style) or 0) * facteur
            if i:
                total += (mesurer(" ", style) or 0)
        return total
    return largeur_mots(mots, taille)


def largeur_mots(mots, taille):
    """Largeur estimee d'une suite de mots ; un mot mis en impact (agrandi, capitales) compte plus large."""
    total = 0.0
    for i, w in enumerate(mots):
        total += (len(w["mot"]) + (1 if i else 0)) * taille * 0.52 * w.get("facteur_largeur", 1.0)
    return total


def peut_couper_apres(mot):
    m = normaliser(mot["mot"]).split()
    return not (m and m[-1] in NO_CUT_AFTER)


def cible_mots(ml):
    """(mini, cible_max, plafond) de mots par bloc. « mots_cibles_par_bloc »: "3-6" ; plafond = mots_max_par_bloc."""
    m = re.match(r"\s*(\d+)\D+(\d+)", str(ml.get("mots_cibles_par_bloc", "")))
    mini, cible = (int(m.group(1)), int(m.group(2))) if m else (MIN_MOTS_BLOC, ml["mots_max_par_bloc"])
    return mini, cible, max(ml["mots_max_par_bloc"], cible)


def decouper(mots, style):
    ml = style["mise_en_page"]
    taille = taille_em(style["police"]) * 1.056  # = ancien 66 : decoupes inchangees
    mini, max_mots, plafond = cible_mots(ml)
    largeur_totale = ml["largeur_max_px"] * ml["lignes_max"]
    blocs, cur = [], []
    for w in mots:
        if cur:
            texte = " ".join(x["mot"] for x in cur + [w])
            trop_large = largeur_estimee(texte, taille) > largeur_totale * 0.92
            trop_long = len(cur) >= max_mots or trop_large
            dernier = cur[-1]["mot"].rstrip()[-1:]
            fin_phrase = dernier in ".?!…"
            virgule = dernier in ",;:" and len(cur) >= mini
            pause = w["debut"] - cur[-1]["fin"] > PAUSE_COUPE and len(cur) >= mini
            if (trop_long or fin_phrase or virgule or pause) and (peut_couper_apres(cur[-1]) or fin_phrase or trop_large or len(cur) >= plafond):
                blocs.append(cur)
                cur = []
        cur.append(w)
    if cur:
        blocs.append(cur)
    return fusionner_petits_blocs(blocs, plafond)


def fusionner_petits_blocs(blocs, max_mots):
    """Un bloc d'un seul mot est rattache au bloc voisin (le plus proche dans le temps) s'il reste de la place."""
    i = 0
    while i < len(blocs):
        b = blocs[i]
        if len(b) == 1:
            fin_phrase = lambda x: x[-1]["mot"].rstrip()[-1:] in ".?!…"
            ecart_av = b[0]["debut"] - blocs[i - 1][-1]["fin"] if i > 0 and not fin_phrase(blocs[i - 1]) else 9
            ecart_ap = blocs[i + 1][0]["debut"] - b[0]["fin"] if i + 1 < len(blocs) and not fin_phrase(b) else 9
            if ecart_av <= ecart_ap and ecart_av < 1.0 and len(blocs[i - 1]) < max_mots:
                blocs[i - 1].extend(b); del blocs[i]; continue
            if ecart_ap < 1.0 and len(blocs[i + 1]) < max_mots:
                blocs[i + 1] = b + blocs[i + 1]; del blocs[i]; continue
        i += 1
    return blocs


def repartir_lignes(bloc, style):
    """Retourne une liste de lignes (listes de mots) : 1 ligne si ca tient, sinon coupe equilibree."""
    ml = style["mise_en_page"]
    taille = taille_em(style["police"]) * 1.056  # = ancien 66 : decoupes inchangees
    if ml["lignes_max"] < 2 or len(bloc) < 2 or largeur_ligne(bloc, style, taille) <= ml["largeur_max_px"]:
        return [bloc]
    meilleur, score = None, None
    for k in range(1, len(bloc)):
        if not peut_couper_apres(bloc[k - 1]):
            continue
        a = largeur_ligne(bloc[:k], style, taille)
        b = largeur_ligne(bloc[k:], style, taille)
        s = max(a, b)
        if score is None or s < score:
            meilleur, score = k, s
    if meilleur is None:
        meilleur = len(bloc) // 2
    return [bloc[:meilleur], bloc[meilleur:]]


def horodater(blocs):
    """Debut du bloc = debut de son 1er mot. Fin = debut du suivant si ecart < 1 s, sinon fin du dernier mot."""
    res = []
    for i, b in enumerate(blocs):
        debut = b[0]["debut"]
        fin = b[-1]["fin"]
        if i + 1 < len(blocs):
            suivant = blocs[i + 1][0]["debut"]
            if suivant - fin < 1.0:
                fin = suivant
        fin = max(fin, debut + MIN_DUREE)
        # temps de lecture : ~0,45 s + 0,05 s par caractere, dans la limite du debut du bloc suivant
        nb_car = len(" ".join(w["mot"] for w in b))
        lecture = debut + 0.45 + 0.05 * nb_car
        if lecture > fin and i + 1 < len(blocs):
            fin = min(lecture, blocs[i + 1][0]["debut"] - 0.04)
        elif lecture > fin:
            fin = lecture
        fin = max(fin, b[-1]["fin"])
        if i + 1 < len(blocs):
            fin = min(fin, max(blocs[i + 1][0]["debut"], debut + 0.1))
        res.append({"debut": debut, "fin": fin, "mots": b})
    # jamais de chevauchement : Whisper donne parfois des mots dont le debut recule
    for i in range(1, len(res)):
        prev, cur = res[i - 1], res[i]
        if cur["debut"] < prev["fin"] + 0.02:
            if cur["debut"] - 0.02 >= prev["debut"] + 0.15:
                prev["fin"] = cur["debut"] - 0.02
            else:
                cur["debut"] = prev["fin"] + 0.02
                cur["fin"] = max(cur["fin"], cur["debut"] + MIN_DUREE)
    return res


def limiter_mots_cles(blocs, style):
    """max_par_bloc mots cles par bloc dont au plus bleu_max_par_bloc en bleu (« accent »).
    Les mots forces par le glossaire (ex. Forvis Mazars) passent en premier."""
    mc = style["mots_cles"]
    for b in blocs:
        mots = b["mots"]
        if not mc.get("actif", False):
            for w in mots:
                w["cle"] = None
                w.pop("cle_forcee", None)
            continue
        for w in mots:
            if w.get("cle_forcee"):
                w["cle"] = w["cle_forcee"]
        marques = [w for w in mots if w.get("cle")]
        marques.sort(key=lambda w: (0 if w.get("cle_forcee") else 1, mots.index(w)))
        for w in marques[mc.get("max_par_bloc", 1):]:
            w["cle"] = None
        bleu_max = mc.get("bleu_max_par_bloc")
        if bleu_max is not None:
            n = 0
            for w in [x for x in marques[:mc.get("max_par_bloc", 1)]]:
                if w.get("cle") == "accent":
                    n += 1
                    if n > bleu_max and not w.get("cle_forcee"):
                        w["cle"] = "principal"


# ---------- gamme « animation » : impact ----------
# Un mot ou une courte phrase peut etre mis en impact par-dessus le style de la marque : meme police que le
# texte de la marque et sa couleur de marque, avec en plus gras, agrandi et/ou capitales.
# Les reglages viennent de la table Supabase Animations (defauts) puis de Clients.style.impact (par marque).
GENRES = ("corporate", "impact", "karaoke")
GRIS_INACTIF = "#B8B8B8"


def style_genre(style, genre, modele=None):
    """Style de la marque habille d'un genre (corporate / impact / karaoke) choisi par l'agent pour un sous-titre.
    La police, les couleurs et la position de la marque sont conservees ; le genre change l'animation, la casse,
    la graisse et la taille. `modele` = style generique du genre (table Clients) : sert de reference."""
    if genre not in GENRES:
        return style
    import copy
    st = copy.deepcopy(style)
    modele = modele or {}
    mp, ma = modele.get("police", {}), modele.get("animation", {})
    st["animation"] = dict(st["animation"], mode=ma.get("mode") or {"corporate": "progressive", "impact": "pop", "karaoke": "karaoke"}[genre],
                           duree_fade_ms=ma.get("duree_fade_ms", st["animation"].get("duree_fade_ms", 150)))
    p = st["police"]
    if genre == "corporate":
        p["casse"] = "phrase"
    elif genre == "impact":
        p["casse"] = "majuscules"
        p["gras"] = True
        ratio = 1.1
        if "taille_em_px" in p:
            p["taille_em_px"] = p["taille_em_px"] * ratio
        else:
            p["taille_px"] = p["taille_px"] * ratio
    else:      # karaoke : le mot prononce s'allume ; les mots a venir sont grises
        c = st["couleurs"]
        if c.get("texte_inactif", c["texte"]).upper() == c["texte"].upper():
            c["texte_inactif"] = GRIS_INACTIF
        c["mot_cle_accent"] = couleur_impact(st, reglages_impact(st))
    return st


IMPACT_DEFAUT = {
    "effets_autorises": ["gras", "agrandi", "capitales"],
    "effets_defaut": ["agrandi"],
    "echelle": 1.3,            # taille d'un mot « agrandi » par rapport au texte
    "couleur": "marque",       # "marque" = couleurs.mot_cle_accent du style, ou un #RRGGBB
    "animation": "pop",        # "pop" (petit coup d'echelle quand le mot est dit) ou "aucune"
    "duree_ms": 140,
    "max_par_bloc": 1,         # nombre de passages en impact par sous-titre
}
EFFETS_IMPACT = ("gras", "agrandi", "capitales")


def reglages_impact(style):
    cfg = dict(IMPACT_DEFAUT)
    cfg.update(style.get("impact") or {})
    return cfg


def effets_impact(demandes, cfg):
    """Effets demandes par l'IA, limites a ceux que la marque autorise ; effets par defaut si rien de valide."""
    autorises = [e for e in EFFETS_IMPACT if e in (cfg.get("effets_autorises") or [])]
    voulus = [e for e in EFFETS_IMPACT if e in (demandes or []) and e in autorises]
    if not voulus:
        voulus = [e for e in (cfg.get("effets_defaut") or []) if e in autorises]
    return voulus


def couleur_impact(style, cfg):
    c = cfg.get("couleur")
    if isinstance(c, str) and c.startswith("#"):
        return c
    return style["couleurs"].get("mot_cle_accent") or style["couleurs"]["texte"]


def limiter_impact(blocs, style):
    """Garde au plus max_par_bloc passages en impact par bloc (les premiers) ; un mot en impact n'est plus un mot cle."""
    cfg = reglages_impact(style)
    maxi = int(cfg.get("max_par_bloc", 1))
    for b in blocs:
        groupes = []
        for w in b["mots"]:
            if w.get("impact"):
                g = w.get("impact_groupe", 0)
                if g not in groupes:
                    groupes.append(g)
        gardes = set(groupes[:maxi])
        for w in b["mots"]:
            if w.get("impact") and w.get("impact_groupe", 0) not in gardes:
                w.pop("impact", None)
                w.pop("impact_groupe", None)
            if w.get("impact"):
                w["cle"] = None


def preparer_impact_ligne(ligne, style, taille):
    """Fixe l'echelle et le poids de largeur des mots en impact d'une ligne ; reduit l'agrandissement si la ligne deborde."""
    cfg = reglages_impact(style)
    casse_maj = style["police"].get("casse") == "majuscules"
    for w in ligne:
        eff = w.get("impact")
        if not eff:
            w["impact_echelle"] = 1.0
            w["facteur_largeur"] = 1.0
            continue
        w["impact_echelle"] = float(cfg.get("echelle", 1.3)) if "agrandi" in eff else 1.0
        w["facteur_largeur"] = w["impact_echelle"] * (1.2 if "capitales" in eff and not casse_maj else 1.0) * (1.05 if "gras" in eff else 1.0)
    largeur = largeur_ligne(ligne, style, taille)
    maxi = style["mise_en_page"]["largeur_max_px"]
    if largeur > maxi:
        exces = largeur / maxi
        for w in ligne:
            if w.get("impact") and w["impact_echelle"] > 1.0:
                reduit = max(1.0, w["impact_echelle"] / exces)
                w["facteur_largeur"] *= reduit / w["impact_echelle"]
                w["impact_echelle"] = reduit


def _tient(mots, style_bloc, taille):
    """Vrai si le bloc tient dans lignes_max lignes de largeur_max_px (impacts et mots cles compris)."""
    ml = style_bloc["mise_en_page"]
    for w in mots:
        w.setdefault("facteur_largeur", 1.0)
    preparer_impact_ligne(mots, dict(style_bloc, mise_en_page=dict(ml, largeur_max_px=10 ** 6)), taille)
    if len(mots) > cible_mots(ml)[2]:
        return False
    return all(largeur_ligne(l, style_bloc, taille) <= ml["largeur_max_px"] for l in repartir_lignes(mots, style_bloc)) \
        and len(repartir_lignes(mots, style_bloc)) <= ml["lignes_max"]


def couper_blocs_trop_longs(blocs, style):
    """Un sous-titre qui ne tient pas dans le cadre (largeur_max_px x lignes_max, ou plus de mots que le plafond du style)
    est coupe en deux, a la ponctuation ou a la pause la plus proche du milieu, jusqu'a ce que chaque morceau tienne.
    Le temps de chaque mot est conserve."""
    taille = taille_em(style["police"]) * 1.056
    res = []

    def traiter(b):
        st = style_genre(style, b.get("genre")) if b.get("genre") in GENRES else style
        mots = b["mots"]
        if len(mots) < 2 or _tient(mots, st, taille):
            res.append(b)
            return
        meilleur, score = None, None
        for k in range(1, len(mots)):
            a, c = mots[:k], mots[k:]
            larg = max(largeur_ligne(a, st, taille), largeur_ligne(c, st, taille))
            fin_phrase = a[-1]["mot"].rstrip()[-1:] in ".?!…,;:"
            s = larg * (0.9 if fin_phrase else 1.0) * (1.0 if peut_couper_apres(a[-1]) else 1.35)
            if len(a) == 1 or len(c) == 1:
                s *= 1.15
            if score is None or s < score:
                meilleur, score = k, s
        a, c = mots[:meilleur], mots[meilleur:]
        for morceau in (a, c):
            traiter({"debut": morceau[0]["debut"], "fin": morceau[-1]["fin"], "mots": morceau, "genre": b.get("genre")})

    for b in blocs:
        traiter(b)
    return res


# ---------- lisibilite : contraste automatique ----------
LISIBILITE_DEFAUT = {"auto": True, "seuil_1": 0.42, "seuil_2": 0.62, "contour_1_px": 3, "contour_2_px": 5}


def tags_lisibilite(style, bloc, k):
    """Contour sombre ajoute automatiquement quand le fond de la video, derriere le texte, est clair.
    bloc["lum"] = luminosite (0 a 1) du fond derriere le texte, mesuree par le robot. Regle valable pour toutes les marques :
    fond moyen (> seuil_1) contour 3 px ; fond clair (> seuil_2) contour 5 px. Jamais moins que le contour du style."""
    cfg = dict(LISIBILITE_DEFAUT)
    cfg.update(style.get("lisibilite") or {})
    lum = bloc.get("lum")
    if not cfg.get("auto") or lum is None:
        return ""
    if style.get("_texte_noir"):                  # texte noir : contour BLANC quand le fond est sombre
        lm = bloc.get("lum_moy", lum)
        px = cfg["contour_2_px"] if lm <= 0.2 else cfg["contour_1_px"] if lm <= 0.35 else 0
        return f"\\bord{px * k:.1f}\\3c&HFFFFFF&" if px else ""
    px = cfg["contour_2_px"] if lum >= cfg["seuil_2"] else cfg["contour_1_px"] if lum >= cfg["seuil_1"] else 0
    base = (style.get("contour") or {}).get("epaisseur_px", 0)
    if px <= base:
        return ""
    return f"\\bord{px * k:.1f}\\3c&H000000&"


# ---------- sortie ASS ----------
def generer_ass(blocs, style, largeur, hauteur, nom_police, titres=None, dossier_polices="fonts", modeles_genres=None):
    global _DOSSIER_POLICES
    _DOSSIER_POLICES = dossier_polices
    k = hauteur / style["resolution"][1]          # echelle verticale
    kx = largeur / style["resolution"][0]
    p, c, ml, an, ombre = style["police"], style["couleurs"], style["mise_en_page"], style["animation"], style["ombre"]
    contour = style.get("contour")
    taille = taille_ass(p) * k
    cx, cy = ml["centre_x_px"] * kx, ml["centre_bloc_y_px"] * k
    mode = an.get("mode", "statique")
    fade = int(an.get("duree_fade_ms", 150))
    gras = -1 if p.get("gras", p.get("graisse", "").lower() in ("bold", "black", "heavy")) else 0
    epaisseur = (contour["epaisseur_px"] * k) if contour else 0
    couleur_contour = couleur_ass(contour["couleur"]) if contour else couleur_ass("#000000")
    if ombre.get("active"):
        decalage = ombre.get("decalage_px", 2) * k
        alpha_ombre = int(round((1 - ombre.get("opacite", 0.6)) * 255))
        back = couleur_ass(ombre.get("couleur", "#000000"), alpha_ombre)
        a_ombre = f"&H{alpha_ombre:02X}&"
    else:
        decalage, back = 0, couleur_ass("#000000", 255)
        a_ombre = "&HFF&"

    entete = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {largeur}
PlayResY: {hauteur}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Defaut,{nom_police},{taille:.1f},{couleur_ass(c['texte'])},{couleur_ass(c['texte'])},{couleur_contour},{back},{gras},0,0,0,100,100,0,0,1,{epaisseur:.1f},{decalage:.1f},5,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lignes = []
    style_marque = style
    for b in blocs:
        b0 = b["debut"]
        genre = b.get("genre")
        style = style_genre(style_marque, genre, (modeles_genres or {}).get(genre)) if genre in GENRES else style_marque
        p, c, an = style["police"], style["couleurs"], style["animation"]
        mode = an.get("mode", "statique")
        fade = int(an.get("duree_fade_ms", 150))
        gras = -1 if p.get("gras", p.get("graisse", "").lower() in ("bold", "black", "heavy")) else 0
        ft = taille_ass(p) / taille_ass(style_marque["police"])      # taille du genre / taille de la marque
        for w in b["mots"]:
            w.setdefault("facteur_largeur", 1.0)
        taille_calc = taille_em(style["police"]) * 1.056
        for w in b["mots"]:      # 1re estimation de largeur (avant le decoupage en lignes)
            preparer_impact_ligne([w], dict(style, mise_en_page=dict(ml, largeur_max_px=10 ** 6)), taille_calc)
        mise_en_page = repartir_lignes(b["mots"], style)
        morceaux, echelles = [], []
        reduction = 1.0      # styles « mesure_exacte » : le bloc est reduit (70 % au plus) s'il deborde encore
        for ligne in mise_en_page:
            preparer_impact_ligne(ligne, style, taille_calc)
            if mesure_active(style):
                reduction = min(reduction, max(0.7, ml["largeur_max_px"] / max(largeur_ligne(ligne, style, taille_calc), 1)))
        for ligne in mise_en_page:
            parts = []
            echelles.append(reduction * ft * max([(w.get("impact_echelle", 1.0) if w.get("impact") else (style["mots_cles"]["echelle"] if w.get("cle") else 1)) for w in ligne]))
            for w in ligne:
                texte = w["mot"].upper() if p.get("casse") == "majuscules" else w["mot"]
                texte = texte.replace("{", "(").replace("}", ")")
                if style.get("contenu", {}).get("espace_insecable_avant"):
                    signes = re.escape(style["contenu"]["espace_insecable_avant"])
                    texte = re.sub(r"(?<=[^\s" + signes + r"])([" + signes + r"])", r"\\h\1", texte)
                a = int(max(0, (w["debut"] - b0) * 1000))
                fin_mot = int(max(a + 1, (min(w["fin"], b["fin"]) - b0) * 1000))
                cle = w.get("cle")
                imp = w.get("impact")
                col = c["texte"]
                echelle = 100
                if cle:
                    col = c.get("mot_cle_" + cle, c["mot_cle_principal"])
                    echelle = int(round(style["mots_cles"]["echelle"] * 100))
                police_impact = ""
                if imp:      # impact : couleur de marque ; agrandi / capitales / gras selon les effets
                    cfg = reglages_impact(style)
                    col = couleur_impact(style, cfg)
                    echelle = int(round(100 * w.get("impact_echelle", 1.0)))
                    if "capitales" in imp:
                        texte = texte.upper()
                    if "gras" in imp:
                        pg = cfg.get("police_gras") or {}
                        police_impact = f"\\fn{pg.get('famille') or nom_police}\\b{pg.get('b', 1)}"
                echelle = int(round(echelle * reduction * ft))
                tags = f"\\1c{couleur_ass(col)}\\fscx{echelle}\\fscy{echelle}"
                if p.get("famille_mot_cle") and not imp:   # mot cle en gras (police dediee) seulement si mots_cles.gras ; sinon meme police
                    if cle and style["mots_cles"].get("gras", True):
                        tags += f"\\fn{p['famille_mot_cle']}\\b1"
                    else:
                        tags += f"\\fn{nom_police}\\b{abs(gras)}"
                if mode == "progressive":
                    tags += f"\\1a&HFF&\\3a&HFF&\\4a&HFF&\\t({a},{a + fade},\\1a&H00&\\3a&H00&\\4a{a_ombre})"
                elif mode == "pop":
                    tags = (f"\\1c{couleur_ass(col)}\\1a&HFF&\\3a&HFF&\\4a&HFF&\\fscx{int(echelle * .6)}\\fscy{int(echelle * .6)}"
                            f"\\t({a},{a + 1},\\1a&H00&\\3a&H00&\\4a{a_ombre})\\t({a},{a + fade + 60},\\fscx{echelle}\\fscy{echelle})")
                elif mode == "karaoke":
                    inactif = couleur_ass(c.get("texte_inactif", c["texte"]))
                    actif = couleur_ass(col if imp else (c.get("mot_cle_accent", "#FFD400") if cle else c["texte"]))
                    tags = (f"\\1c{inactif}\\fscx{echelle}\\fscy{echelle}"
                            f"\\t({a},{a + 1},\\1c{actif})\\t({fin_mot},{fin_mot + 1},\\1c{inactif})")
                if genre in GENRES:
                    tags += f"\\b{abs(gras)}"
                    if genre == "impact" and not imp:
                        pg = reglages_impact(style).get("police_gras") or {}
                        if pg.get("famille"):
                            tags += f"\\fn{pg['famille']}"
                if imp:
                    tags += police_impact
                    if reglages_impact(style).get("animation") == "pop" and mode in ("statique", "progressive"):
                        d = int(reglages_impact(style).get("duree_ms", 140))
                        bump = int(echelle * 1.15)
                        tags += (f"\\t({a},{a + d // 2},\\fscx{bump}\\fscy{bump})"
                                 f"\\t({a + d // 2},{a + d},\\fscx{echelle}\\fscy{echelle})")
                parts.append("{" + tags + "}" + texte)
            morceaux.append(" ".join(parts))
        if ml.get("ancrage") == "premiere_ligne" and "baseline_ligne1_y_px" in ml:
            # chaque ligne est posee a sa propre baseline : 1re ligne fixe, suivantes a ecart constant
            for li, (morceau, ech) in enumerate(zip(morceaux, echelles)):
                baseline = (ml["baseline_ligne1_y_px"] + li * ml.get("ecart_baselines_px", 72)) * k
                y = baseline - 0.763 * taille * ech
                tag = f"\\an8\\pos({cx:.0f},{y:.0f})" + tags_lisibilite(style_marque, b, k)
                if mode == "statique":
                    tag += f"\\fad({fade},{int(fade * .7)})"
                lignes.append(f"Dialogue: 0,{temps_ass(b['debut'])},{temps_ass(b['fin'])},Defaut,,0,0,0,,{{{tag}}}{morceau}")
            continue
        corps = "\\N".join(morceaux)
        debut_tag = f"\\an5\\pos({cx:.0f},{cy:.0f})" + tags_lisibilite(style_marque, b, k)
        if mode == "statique":
            debut_tag += f"\\fad({fade},{int(fade * .7)})"
        if ml.get("interligne", 1) != 1:
            pass  # l'interligne est gere par la police ; valeur gardee pour usage futur
        lignes.append(f"Dialogue: 0,{temps_ass(b['debut'])},{temps_ass(b['fin'])},Defaut,,0,0,0,,{{{debut_tag}}}{corps}")
    lignes = generer_bandeaux(titres, style_marque, largeur, hauteur, dossier_polices) + lignes
    return entete + "\n".join(lignes) + "\n"


def _rect_arrondi(w, h, r):
    """Chemin ASS (\\p1) d'un rectangle a coins arrondis, origine en haut a gauche."""
    r = min(r, w / 2, h / 2)
    k = 0.5523 * r
    return (f"m {r:.0f} 0 l {w - r:.0f} 0 b {w - r + k:.0f} 0 {w:.0f} {r - k:.0f} {w:.0f} {r:.0f} "
            f"l {w:.0f} {h - r:.0f} b {w:.0f} {h - r + k:.0f} {w - r + k:.0f} {h:.0f} {w - r:.0f} {h:.0f} "
            f"l {r:.0f} {h:.0f} b {r - k:.0f} {h:.0f} 0 {h - r + k:.0f} 0 {h - r:.0f} "
            f"l 0 {r:.0f} b 0 {r - k:.0f} {r - k:.0f} 0 {r:.0f} 0")


def generer_bandeaux(titres, style, largeur, hauteur, dossier_polices):
    """Lignes Dialogue ASS des bandeaux-titres (fond arrondi + texte gras + citation optionnelle).
    titres : [{"debut": s, "fin": s, "titre": str, "citation": str|None}]"""
    b = style.get("bandeau")
    if not titres or not b or not b.get("actif", True):
        return []
    from PIL import ImageFont
    k, kx = hauteur / style["resolution"][1], largeur / style["resolution"][0]
    fichier = os.path.join(dossier_polices, os.path.basename(style["police"].get("fichier_alternatif", "HalyardDisplay-Bold.otf")))
    taille = b["taille_px"] * k
    police = ImageFont.truetype(fichier, int(round(taille))) if os.path.exists(fichier) else None
    famille = style["police"].get("famille_mot_cle", style["police"]["famille"])
    cx = style["mise_en_page"]["centre_x_px"] * kx
    cy = b["centre_y_px"] * k
    h = b["hauteur_px"] * k
    out = []
    for t in titres:
        texte = t["titre"].replace("{", "(").replace("}", ")")
        larg_txt = police.getlength(texte) if police else largeur_estimee(texte, taille)
        w = min(larg_txt + 2 * b["padding_x_px"] * kx, largeur * 0.92)
        d0, d1 = temps_ass(t["debut"]), temps_ass(t["fin"])
        fad = f"\\fad({b.get('fade_ms', 120)},{b.get('fade_ms', 120)})"
        out.append(f"Dialogue: 0,{d0},{d1},Defaut,,0,0,0,,{{\\an7\\pos({cx - w / 2:.0f},{cy - h / 2:.0f}){fad}"
                   f"\\1c{couleur_ass(b['couleur_fond'])}\\bord0\\shad0\\p1}}{_rect_arrondi(w, h, b['rayon_px'] * k)}")
        out.append(f"Dialogue: 1,{d0},{d1},Defaut,,0,0,0,,{{\\an5\\pos({cx:.0f},{cy:.0f}){fad}\\fn{famille}\\b1"
                   f"\\fs{taille:.0f}\\1c{couleur_ass(b['couleur_texte'])}\\bord0\\shad0}}{texte}")
        cit = t.get("citation")
        if cit:
            c = b.get("citation", {})
            cit = cit.replace("{", "(").replace("}", ")")
            out.append(f"Dialogue: 1,{d0},{d1},Defaut,,0,0,0,,{{\\an8\\pos({cx:.0f},{c.get('y_px', 1295) * k:.0f}){fad}"
                       f"\\fn{style['police']['famille']}\\b0\\i1\\fs{c.get('taille_px', 50) * k:.0f}"
                       f"\\1c{couleur_ass(c.get('couleur', '#FFFFFF'))}\\bord0\\shad{max(1, 2 * k):.0f}}}\u201c{cit}\u201d")
    return out


def generer_srt(blocs):
    sortie = []
    for i, b in enumerate(blocs, 1):
        texte = " ".join(w["mot"] for w in b["mots"])
        sortie.append(f"{i}\n{temps_srt(b['debut'])} --> {temps_srt(b['fin'])}\n{texte}\n")
    return "\n".join(sortie)


def construire_blocs(mots, style):
    mots = [m for m in mots if m["mot"].strip()]
    if not style.get("contenu", {}).get("ponctuation", True):
        for m in mots:
            m["mot"] = re.sub(r"[.,;:!?…]+$", "", m["mot"])
    blocs = horodater(decouper(mots, style))
    limiter_impact(blocs, style)
    limiter_mots_cles(blocs, style)
    return blocs
