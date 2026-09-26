from transformers import AutoTokenizer, AutoModelForTokenClassification
import torch
import re
import dateparser
from collections import defaultdict
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
import os

BASE_DIR = os.getenv("MODELS_DIR", "trained_models")

# Remplace tous les chemins absolus par :
MODEL_DIR     = os.path.join(BASE_DIR, "mdeberta-ner")
# ── Chargement modèles ──
ner_tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR, local_files_only=True)
ner_model     = AutoModelForTokenClassification.from_pretrained(MODEL_DIR, local_files_only=True)
ner_model.to(DEVICE).eval()

ID2LABEL_NER = {
    0:"O", 1:"B-LOC", 2:"I-LOC", 3:"B-DATE", 4:"I-DATE",
    5:"B-FLIGHT", 6:"I-FLIGHT", 7:"B-PRICE", 8:"I-PRICE",
    9:"B-ORG", 10:"I-ORG"
}

MOTS_PARASITES_ORG = {
    # Français
    "pour", "de", "du", "des", "avec", "et", "ou",
    "un", "une", "le", "la", "les", "par", "sur",
    "dans", "au", "aux", "mon", "ton", "son",
    # Anglais
    "for", "and", "the", "a", "an", "to", "from",
    "with", "in", "on", "at", "of",
    # Mots communs voyage
    "hotel", "hôtel", "vol", "flight", "restaurant",
    "chambre", "voyage", "séjour", "billet"
}

def regex_entities(text):
    patterns = [
        # ===========================
        # ----- DATE EN -----
        # ===========================
        (r"\b(today|tonight)\b", "DATE"),
        (r"\b(tomorrow|tmr|tmrw)\b", "DATE"),
        (r"\b(day after tomorrow|overmorrow)\b", "DATE"),
        (r"\bnext (week|month|year|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", "DATE"),
        (r"\blast (week|month|year|night|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", "DATE"),
        (r"\bthis (week|month|year|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", "DATE"),
        (r"\bin \d+ (day|days|week|weeks|month|months|year|years)\b", "DATE"),
        (r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", "DATE"),
        (r"\b\d{1,2}\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{2,4}\b", "DATE"),
        (r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{1,2},?\s+\d{2,4}\b", "DATE"),

        # ===========================
        # ----- DATE FR -----
        # ===========================
        (r"\b(aujourd'hui|cet après-midi|ce soir)\b", "DATE"),
        (r"\bdemain\b", "DATE"),
        (r"\b(après-demain|après demain)\b", "DATE"),
        (r"\b(semaine prochaine|mois prochain|année prochaine)\b", "DATE"),
        (r"\b(semaine dernière|mois dernier|année dernière)\b", "DATE"),
        (r"\bcette semaine\b", "DATE"),
        (r"\bdans \d+ (jour|jours|semaine|semaines|mois|an|ans)\b", "DATE"),
        (r"\ble \d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", "DATE"),
        (r"\b\d{1,2}\s+(janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)\s+\d{2,4}\b", "DATE"),

        # ===========================
        # ----- DATE AR (Arabe) -----
        # ===========================
        (r"(اليوم|الليلة)", "DATE"),
        (r"(غدوة|غدا)", "DATE"),
        (r"(بعد غدوة|بعد غد)", "DATE"),
        (r"(الاسبوع القادم|الشهر القادم|العام القادم)", "DATE"),
        (r"(الاسبوع الماضي|الشهر الماضي|العام الماضي)", "DATE"),
        (r"هذا الاسبوع", "DATE"),
        (r"بعد (\d+) (ايام|اسابيع|شهور|سنوات)", "DATE"),
        (r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", "DATE"),

        # ===========================
        # ----- DATE Derja (Arabe) -----
        # ===========================
        (r"(السبوع الجاي|السيمانة الجاية|الشهر الجاي|العام الجاي)", "DATE"),
        (r"(السبوع الفايت|السيمانة الفايتة|الشهر الفايت|العام الفايت)", "DATE"),
        (r"(غدوة الصباح|غدوة الليل)", "DATE"),
        (r"(بعد غدوة|بعد بكرة)", "DATE"),
        (r"في (السبوع|السيمانة|الشهر|العام) الجاي", "DATE"),

        # ===========================
        # ----- DATE Derja (Latin) -----
        # ===========================
        (r"\b(lyoum|liia?ma)\b", "DATE"),                    # aujourd'hui
        (r"\b(ghodwa|ghoudwa|bekra)\b", "DATE"),              # demain
        (r"\b(b[ae]d ghodwa|ba[cd] ghodwa|b[ae]d bekra)\b", "DATE"),  # après-demain
        (r"\b(essbe[mn]o? el j[ae]y|simaana el j[ae]ya)\b", "DATE"),   # la semaine prochaine
        (r"\b(echhar ejj[ae]y|el[ -]?[ae]m el j[ae]y)\b", "DATE"),      # le mois prochain / l'année prochaine
        (r"\b(essbe[mn]o? el f[ae]t?et|simaana el f[ae]t?ya)\b", "DATE"),  # la semaine dernière
        (r"\b(echhar el f[ae]t|el[ -]?[ae]m el f[ae]t)\b", "DATE"),        # le mois dernier / l'année dernière
        (r"\b(ghodwa s[sz]beh?|ghodwa sba[hh])\b", "DATE"),                # demain matin
        (r"\b(ghodwa l[iy]l|ghodwa liil)\b", "DATE"),                      # demain soir
        (r"\b(b[ae]d ([\d]+|[\w]+) (iy[a]m|s[wk]ana?|chhour|snin|snine|sanawa?t))\b", "DATE"),  # après X jours/semaines/mois/ans
        (r"\b([\d]+) (iy[a]m|s[wk]ana?|chhour|snin|snine) (lou[zw]el|j[ae]y[a]?)\b", "DATE"),  # X premiers jours
        (r"\bfi (simaana|echhar|el[ -]?am) ejj[ae]y\b", "DATE"),           # dans la semaine/mois/année prochaine
        (r"\b(lekh[dr]a|le5ra|el okhra)\b", "DATE"),                       # prochain (féminin)
        (r"\b(el okher|lekh[dr])\b", "DATE"),                              # prochain (masculin)
        (r"\b(tawwa|tawa|dnow?ba)\b", "DATE"),                             # maintenant / bientôt
        (r"\b(bar[cs]a|bekri)\b", "DATE"),                                 # tôt
        (r"\b(m[ae]ta nhar|chhal fi nhar)\b", "DATE"),                     # quel jour
        
        # Jours de la semaine en Derja (latin)
        (r"\b(lethn[iy]n|tne[n]|tenin)\b", "DATE"),                        # lundi
        (r"\b(ethl[ae]th[a]?|tlata|tlatha)\b", "DATE"),                    # mardi
        (r"\b(larbo[uw][cd]?|arb[o]a|arb3a)\b", "DATE"),                   # mercredi
        (r"\b(lekhmi[cs]|khem[cs]|5mis)\b", "DATE"),                       # jeudi
        (r"\b(ejjom[uo]?a|jom3a|jmou3a)\b", "DATE"),                       # vendredi
        (r"\b(essebt|sebt|sibt)\b", "DATE"),                               # samedi
        (r"\b(el[ -]?[ah]ad|[ah]ad)\b", "DATE"),                           # dimanche
        
        # Mois en Derja (latin)
        (r"\b(j[ae]nfi|j[ae]nvi)\b", "DATE"),                              # janvier
        (r"\b(fi[uv]ri|fevri|f[ée]vrier)\b", "DATE"),                      # février
        (r"\b(ma[ar]s|maars)\b", "DATE"),                                  # mars
        (r"\b(avril|avri|avreel)\b", "DATE"),                              # avril
        (r"\b(maei?|may|mai)\b", "DATE"),                                  # mai
        (r"\b(ju[oei]n|jwe?n)\b", "DATE"),                                 # juin
        (r"\b(ju[oe]i[l]?|jwi[l]?a?|jwe?l[ae]?)\b", "DATE"),               # juillet
        (r"\b(ou[ts]|awuss?|awu[s$]s?)\b", "DATE"),                        # août
        (r"\b(septemb[re]?|septembi|s[oe]pt)\b", "DATE"),                  # septembre
        (r"\b(octob[re]?|oktob[re]?|oct)\b", "DATE"),                      # octobre
        (r"\b(novemb[re]?|nov)\b", "DATE"),                                # novembre
        (r"\b(d[ée]cemb[re]?|dec)\b", "DATE"),                             # décembre

        # ===========================
        # ----- DATE Nombres (Multi-lang) -----
        # ===========================
        (r"\b(in|dans|بعد)\s+(one|two|three|four|five|six|seven|eight|nine|ten)\s+(day|days|week|weeks)\b", "DATE"),
        (r"\b(dans|après|after)\s+(un|deux|trois|quatre|cinq|six|sept|huit|neuf|dix)\s+(jour|jours|semaine|semaines)\b", "DATE"),
        (r"\b(b[ae]d)\s+(we[hc]ed|thn[iy]n|thl[ae]tha|arb[o]a|khamsa|s[ei]tta|s[eo]b[o]a|thm[ae]nya|tes[o]ud|e[cs]hra)\s+(iy[a]m|s[wk]ana?)\b", "DATE"),  # Derja: après X jours/semaines

        # ===========================
        # ----- PRICE (Toutes devises) -----
        # ===========================
        # Euro
        (r"\b\d+(?:[.,]\d{1,2})?\s*(?:€|EUR|euros?)\b", "PRICE"),
        # Dollar
        (r"\b\d+(?:[.,]\d{1,2})?\s*(?:USD|US\$|\$|dollars?)\b", "PRICE"),
        # Dinar tunisien (arabe et latin)
        (r"\b\d+(?:[.,]\d{1,2})?\s*(?:TND|DT|دينار(?: تونسي)?|د\.ت|د)\b", "PRICE"),
        (r"\b\d+(?:[.,]\d{1,2})?\s*(dinar(?: tunisien)?|dinars? tunisiens?)\b", "PRICE"),
        (r"\b\d+(?:[.,]\d{1,2})?\s*(din[ae]r|dinaar|dinar)\b", "PRICE"),  # Derja
        # Prix formatés
        (r"\b\d+(?:[.,]\d{3})*(?:[.,]\d{2})?\s*(?:€|\$|د.ت|DT|dinars?)\b", "PRICE"),
        # Prix avec centimes
        (r"\b\d+[.,]\d{1,2}\s*(?:€|EUR|dollars?|USD|TND|DT|دينار|dinars?)\b", "PRICE"),
        # Fourchettes de prix
        (r"\b(?:de|entre|from|between|min|max)\s+\d+(?:[.,]\d{1,2})?\s*(?:€|\$|DT|dinars?)?\s*(?:à|to|and|-|w|ou)\s*\d+(?:[.,]\d{1,2})?\s*(?:€|\$|DT|dollars?|dinars?)\b", "PRICE"),
        # Prix avec préfixes
        (r"\b(?:à partir de|à|at|for|only|seulement|just|b[ei]h?|b[ie]|bch)\s+\d+(?:[.,]\d{1,2})?\s*(?:€|\$|DT|dinar|euro|dollar|dinars?)\b", "PRICE"),
        # Prix avec suffixes
        (r"\b\d+(?:[.,]\d{1,2})?\s*(?:€|\$|DT|دينار|dinars?)\s*(?:par personne|per person|p\.p\.|HT|TTC|incl\.|excl\.|l[ui] we[hc]ed|loul[d])\b", "PRICE"),

        # ===========================
        # ----- LOCATION (Lieux) -----
        # ===========================
        # Villes tunisiennes (français/anglais/arabe/latin)
        (r"\b(Tunis|Sfax|Sousse|Nabeul|Hammamet|Monastir|Mahdia|Bizerte|Gabès|Gafsa|Kairouan|Médenine|Tozeur|Tataouine|Béja|Jendouba|Kef|Kasserine|Kébili|Manouba|Siliana|Zaghouan|Ariana|Ben Arous)\b", "LOC"),
        # Villes en Derja (latin)
        (r"\b(T[uo]nes|Sfa[hk]s|S[ou]ssa|Nab[eo]l|Hamm[ae]met|Mn[ae]stir|Mahdia|Benzert|Gab[èe]s|Gafsa|Q[ae]yraw[ae]n|M[ée]denine|T[ou]zeur|Tataouine|Baja|Jandouba|Kef|Qasrin|Kebili|Manouba|Silyana|Zaghw[ae]n|Aryana|Ben [Aa]rous)\b", "LOC"),
        # Aéroports
        (r"\b(aéroport|airport|aeroport|مطار|mat[ae]r?)\s+(?:international\s+)?(?:de\s+)?(Tunis Carthage|Monastir|Sfax Thyna|Tozeur Nefta|Djerba|Tabarka|Hou[ae]r?ia? Boumedienne)\b", "LOC"),
        # Pays (multi-langue)
        (r"\b(Tunisie|France|Algérie|Maroc|Libye|Italie|Espagne|Allemagne|Turquie|Émirats|Qatar|Arabie Saoudite)\b", "LOC"),
        (r"\b(T[ou]nisia|Faransa|Dzayer|Maghreb|L[iy]bya|Italia|Spanya|Almanya|Turkiya|Emirates|Qatar|Sa[uo]dia)\b", "LOC"),

        # ===========================
        # ----- ORGANISATIONS -----
        # ===========================
        # Compagnies aériennes
        (r"\b(Tunisair|Nouvelair|Syphax Airlines|Air France|Lufthansa|Emirates|Qatar Airways|Turkish Airlines|Royal Air Maroc|Alitalia|Transavia|Ryanair|EasyJet|British Airways)\b", "ORG"),
        # Agences de voyage
        (r"\b(Carlson Wagonlit|Havas Voyages|Nouvelles Frontières|Thomas Cook|Expedia|Booking\.com|Lastminute|Go Voyages)\b", "ORG"),
        # Hôtels
        (r"\b(Hotel|Hôtel|Fonda?k?)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b", "ORG"),

        # ===========================
        # ----- FLIGHT (Vols) -----
        # ===========================
        # Numéros de vol
        (r"\b(vol|flight|رحلة|tayara?|tyara)\s+([A-Z]{2}\d{1,4})\b", "FLIGHT"),
        (r"\b([A-Z]{2}\d{1,4})\s+(?:à destination de|to|vers|arrivant|arriving|mtae?|l[èe])\b", "FLIGHT"),
        # Horaires de vol
        (r"\b(départ|departure|اقلاع|i[qg]la[e]?[c]?)\s+(\d{1,2}[:h]\d{2})\b", "FLIGHT"),
        (r"\b(arrivée|arrival|وصول|w[su]o?l)\s+(\d{1,2}[:h]\d{2})\b", "FLIGHT"),
        # Types de vol
        (r"\b(vol\s+direct|direct\s+flight|connecting\s+flight|vol\s+avec\s+escale|tayara mba[cs]hra|tayara b[ie] s[c]cala)\b", "FLIGHT"),
        # ===========================
        # ----- PERSONS (Voyageurs) -----
        # ===========================
        # Français
        (r"\b(\d+)\s*(?:personnes?|adultes?|voyageurs?|passagers?|pers\.?)\b", "PERSONS"),
        (r"\bpour\s+(\d+)\s*(?:personnes?|adultes?|voyageurs?|passagers?)?\b", "PERSONS"),
        (r"\b(\d+)\s*(?:enfants?|bébés?|juniors?)\b", "PERSONS"),

        # Anglais
        (r"\b(\d+)\s*(?:persons?|adults?|travelers?|passengers?|guests?|people)\b", "PERSONS"),
        (r"\bfor\s+(\d+)\s*(?:persons?|adults?|travelers?|passengers?|guests?|people)?\b", "PERSONS"),
        (r"\b(\d+)\s*(?:children?|kids?|babies?|infants?)\b", "PERSONS"),

        # Arabe
        (r"\b(\d+)\s*(?:أشخاص|شخص|بالغ|بالغين|مسافر|مسافرين|راكب|ركاب)\b", "PERSONS"),

        # Derja latin
        (r"\b(\d+)\s*(?:pers?|ashk[hâa][sc]|n[ae][sc]|nes)\b", "PERSONS"),
        (r"\b(we[hc]ed|thn[iy]n|thl[ae]tha|arb[o]a|khamsa|s[ei]tta|s[eo]b[o]a|thm[ae]nya|tes[o]ud|e[cs]hra)\s*(?:pers?|ashk[hâa][sc]|n[ae][sc]|nes)\b", "PERSONS"),
        (r"\b(we[hc]ed|tnin|tlata|arba3a|khamsa|s[ei]tta|sba3a|thmanya|ts3od|3achra)\s*(?:pers?|ashk[hâa][sc])?\b", "PERSONS"),

        # Derja arabe
        (r"\b(\d+)\s*(?:ناس|شخص|أشخاص|بالغين)\b", "PERSONS"),
    ]
    matches = []
    for p, label in patterns:
        for m in re.finditer(p, text, flags=re.IGNORECASE):
            matches.append({"entity": label, "value": m.group()})
    return matches
def normalize_date_with_context(fragment, context=None):
    if context and not re.search(r"\d{4}", fragment):
        fragment = f"{fragment} {context}"
    dt = dateparser.parse(fragment, languages=['fr','en','ar'])
    return dt.strftime("%Y-%m-%d") if dt else fragment

def resolve_date(fragment, normalized_dates, context=None):
    # Si on a un contexte (ex: "avril"), on concatène
    if context and not re.search(r"(jan|feb|mar|apr|mai|juin|juil|août|sep|oct|nov|dec)", fragment, re.IGNORECASE):
        fragment = f"{fragment} {context}"
    dt = dateparser.parse(fragment, languages=['fr','en','ar'])
    if dt:
        return dt.strftime("%Y-%m-%d")
    return None

def normalize_price(text):
    match = re.search(r"([\d.,]+)\s*([^\d\s]+)?", text)
    if match:
        amount = float(match.group(1).replace(',', '.'))
        currency = match.group(2) if match.group(2) else None
        return {"amount": amount, "currency": currency}
    return {"amount": text, "currency": None}

def normalize_location(text):
    return  text.strip()

def normalize_flight(text):
    return text.strip().upper()

def normalize_organization(text):
    return text.strip()
# =========================================================================
# NORMALISATION DES PERSONS (nombre de voyageurs)
# =========================================================================

def normalize_persons(text: str) -> str:
    """
    Extrait le nombre de personnes depuis des expressions comme:
    - "2 personnes", "pour 3", "5 voyageurs"
    - Support aussi les mots en darija: wehced, tnin, tlata, etc.
    """
    WORDS_MAP = {
        # 1
        "wehed": 1, "wa7ed": 1, "wahid": 1, "wehced": 1, "wa7ed": 1,
        "un": 1, "une": 1, "واحد": 1,
        # 2
        "tnin": 2, "thnin": 2, "zouz": 2, "zoz": 2, "zuz": 2,
        "deux": 2, "اثنين": 2, "زوز": 2,
        # 3
        "tlata": 3, "thlatha": 3, "trois": 3, "ثلاثة": 3,
        # 4
        "arba3a": 4, "arba": 4, "arbaa": 4, "quatre": 4, "أربعة": 4,
        # 5
        "khamsa": 5, "5amsa": 5, "cinq": 5, "خمسة": 5,
        # 6
        "sitta": 6, "sita": 6, "setta": 6, "six": 6, "ستة": 6,
        # 7
        "sba3a": 7, "sebaa": 7, "sept": 7, "سبعة": 7,
        # 8
        "thmanya": 8, "thmania": 8, "tmenya": 8, "huit": 8, "ثمانية": 8,
        # 9
        "ts3a": 9, "tes3a": 9, "neuf": 9, "تسعة": 9,
        # 10
        "3achra": 10, "achra": 10, "dix": 10, "عشرة": 10,
    }
    text_lower = text.lower().strip()
    
    # 1. Vérifier les mots en darija/français
    for word, num in WORDS_MAP.items():
        if word in text_lower:
            return str(num)
    
    # 2. Chercher un nombre suivi de "personnes", "voyageurs", "pax", etc.
    patterns = [
        r"(\d+)\s*(?:personnes?|voyageurs?|pax|adultes?|passagers?)",
        r"(?:pour|de|à)\s*(\d+)\s*(?:personnes?)?",
        r"(\d+)\s*(?:pers|px)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            return match.group(1)
    
    # 3. Chercher juste un nombre isolé (fallback)
    match = re.search(r"(\d+)", text)
    if match:
        return match.group(1)
    
    # 4. Valeur par défaut
    return "1"

NER_NORMALIZERS = {
    "DATE": normalize_date_with_context,
    "PRICE": normalize_price,
    "LOC": normalize_location,
    "FLIGHT": normalize_flight,
    "ORG": normalize_organization,
    "PERSONS": normalize_persons,
}
def predict_ner(text):
    tokens   = text.split()
    inputs   = ner_tokenizer(tokens, return_tensors="pt", is_split_into_words=True,
                             truncation=True, max_length=128).to(DEVICE)
    word_ids = ner_tokenizer(tokens, is_split_into_words=True,
                             truncation=True, max_length=128).word_ids()
    with torch.no_grad():
        logits = ner_model(**inputs).logits
    preds = torch.argmax(logits, dim=-1)[0].cpu().numpy()

    # ---------- NER depuis le modèle ----------
    entities, current, seen = [], None, set()
    for wid, pred in zip(word_ids, preds):
        if wid is None or wid in seen: 
            continue
        seen.add(wid)
        label = ID2LABEL_NER.get(pred, "O")
        word  = tokens[wid] if wid < len(tokens) else ""
        if label.startswith("B-"):
            if current: entities.append(current)
            current = {"entity": label[2:], "value": word}
        elif label.startswith("I-") and current:
            current["value"] += " " + word
        else:
            if current: entities.append(current)
            current = None
    if current: entities.append(current)

    # ---------- Fusion avec regex ----------
    regex_extra = regex_entities(text)
    for r in regex_extra:
        if not any(e["value"].lower() == r["value"].lower() for e in entities):
            entities.append(r)

    # ---------- Post-filtre ORG ----------
    entities = [
        e for e in entities
        if not (
            e["entity"] == "ORG" and
            any(mot in MOTS_PARASITES_ORG for mot in e["value"].lower().split())
        )
    ]

    # ---------- Normalisation ----------
    payload = defaultdict(list)
    for ent in entities:
        ent_type = ent['entity']
        if ent_type == "DATE":
            payload[ent_type].append(ent['value'])
        else:
            normalizer = NER_NORMALIZERS.get(ent_type)
            norm_value = normalizer(ent['value']) if normalizer else ent['value']
            payload[ent_type].append(norm_value)

    # ---------- Normalisation des dates ----------
    normalized_dates = []
    for d in payload.get("DATE", []):
        dt = normalize_date_with_context(d)
        if dt:
            normalized_dates.append(dt)
    if normalized_dates:
        payload["DATE"] = normalized_dates

    # ---------- Extraction de date range ----------
    date_range = extract_date_range(text)
    if date_range:
        payload["DATE_RANGE"] = [date_range]
        payload["DATE"] = [date_range["departure_date"], date_range["return_date"]]

    if "PERSONS" in payload:
        persons_values = []
        for v in payload["PERSONS"]:
            try:
                persons_values.append(int(v))
            except (ValueError, TypeError):
                pass
        if persons_values:
            # Garder la valeur la plus grande = total voyageurs
            payload["PERSONS"] = [str(max(persons_values))]
    return dict(payload)


# ---------- Nouvelle fonction extract_date_range ----------
def extract_date_range(text):
    patterns = [
        # ── Français / Anglais ──────────────────────────────────────────
        # S'arrête AVANT "pour", "avec", "et", ponctuation
        r"(?:du|de|from)\s+(.+?)\s+(?:au|à|to)\s+(.+?)(?=\s+(?:pour|avec|et|afin|,|\.)|[,.]|$)",
        # ── Arabe ─────────────────────────────────────────────────────
        r"\bمن\s+(.+?)\s+إلى\s+(.+?)(?=[,.]|$)",
    ]

    MONTH_FR = {
        1: "janvier",  2: "février",  3: "mars",     4: "avril",
        5: "mai",      6: "juin",     7: "juillet",   8: "août",
        9: "septembre",10: "octobre", 11: "novembre", 12: "décembre"
    }

    # Tous les noms de mois en français, anglais et abréviations
    MONTH_RE = re.compile(
        r"(jan|fév|févr|mar|avr|mai|juin|juil|août|sep|sept|oct|nov|déc"
        r"|january|february|march|april|may|june|july|august"
        r"|september|october|november|december"
        r"|janvier|février|mars|avril|juillet|août"
        r"|septembre|octobre|novembre|décembre)",
        re.IGNORECASE
    )

    # dateparser settings : toujours préférer une date future
    DP_SETTINGS = {
        "PREFER_DATES_FROM": "future",
        "RETURN_AS_TIMEZONE_AWARE": False,
    }

    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue

        start_raw = m.group(1).strip()
        end_raw   = m.group(2).strip()

        # ── Nettoyer end_raw : supprimer tout ce qui suit un mot non-date ──
        # ex: "15 mai pour 2 personnes" → "15 mai"
        end_clean = re.split(
            r"\s+(?:pour|avec|et|afin|car|donc|soit|à partir|incluant)\b",
            end_raw, flags=re.IGNORECASE
        )[0].strip()

        # ── Parser la date de fin en premier (elle contient souvent le mois) ──
        return_date = dateparser.parse(
            end_clean, languages=["fr", "en", "ar"], settings=DP_SETTINGS
        )
        if not return_date:
            continue

        # ── Si start_raw n'a pas de mois, injecter celui de la date de fin ──
        # ex: "10" → "10 mai 2026"
        if not MONTH_RE.search(start_raw):
            mois_fr    = MONTH_FR[return_date.month]
            start_raw  = f"{start_raw} {mois_fr} {return_date.year}"

        departure = dateparser.parse(
            start_raw, languages=["fr", "en", "ar"], settings=DP_SETTINGS
        )
        if not departure:
            continue

        # ── Sanity check : départ avant retour ────────────────────────────
        if departure > return_date:
            # Cas pathologique : les deux dans le même mois mais mois mal inféré
            # → on incrémente le mois du retour
            continue

        return {
            "departure_date": departure.strftime("%Y-%m-%d"),
            "return_date":    return_date.strftime("%Y-%m-%d"),
        }

    return None
