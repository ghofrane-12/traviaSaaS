# models/ner_xlm.py
from transformers import AutoTokenizer, AutoModelForTokenClassification
import torch
import re
import dateparser
from collections import defaultdict
import os
from typing import Optional

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# ── Lazy loading ──────────────────────────────────────────────
_ner_tokenizer = None
_ner_model     = None

def _get_model_dir(model_name: str, default_base: str = "trained_models") -> str:
    """Retourne le meilleur chemin disponible pour un modèle."""
    tmp_path = f"/tmp/models_cache/{model_name}"
    if os.path.exists(tmp_path):
        return tmp_path
    base_dir = os.getenv("MODELS_DIR", default_base)
    return os.path.join(base_dir, model_name)

def _load_ner():
    global _ner_tokenizer, _ner_model
    if _ner_model is None:
        import time, logging
        logger = logging.getLogger("chat_logger")
        model_dir = _get_model_dir("xlm-roberta-large-final")
        t0 = time.time()
        logger.info(f"[NER] 🔄 Chargement depuis {model_dir}...")
        _ner_tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
        _ner_model     = AutoModelForTokenClassification.from_pretrained(model_dir, local_files_only=True)
        _ner_model.to(DEVICE).eval()
        logger.info(f"[NER] ✅ Chargé en {time.time()-t0:.2f}s sur {DEVICE}")
    return _ner_tokenizer, _ner_model

# ── Label map nouveau modèle ──────────────────────────────────
ID2LABEL_NER = {
    0:  "O",
    1:  "B-LOC",
    2:  "I-LOC",
    3:  "B-FLIGHT",
    4:  "I-FLIGHT",
    5:  "B-ORGANIZATION",
    6:  "I-ORGANIZATION",
    7:  "B-DATE",
    8:  "I-DATE",
    9:  "B-DURATION",
    10: "I-DURATION",
    11: "B-AMOUNT",
    12: "I-AMOUNT",
    13: "B-CURRENCY",
    14: "I-CURRENCY",
}

MOTS_PARASITES_ORG = {
    "pour", "de", "du", "des", "avec", "et", "ou",
    "un", "une", "le", "la", "les", "par", "sur",
    "dans", "au", "aux", "mon", "ton", "son",
    "for", "and", "the", "a", "an", "to", "from",
    "with", "in", "on", "at", "of",
    "hotel", "hôtel", "vol", "flight", "restaurant",
    "chambre", "voyage", "séjour", "billet"
}


# ── Regex entities ─────────────────────────────────
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
        (r"\b\d{1,2}\s+(january|february|march|april|may|june|july|august|september|october|november|december)\b", "DATE"),
        (r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}\b", "DATE"),
        (r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{1,2}\b", "DATE"),

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
        (r"\b\d{1,2}\s+(janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)\b", "DATE"),
        (r"\ble\s+\d{1,2}\s+(janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)\b", "DATE"),

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
        (r"\b\d{1,2}\s+(يناير|فبراير|مارس|أبريل|مايو|يونيو|يوليو|أغسطس|سبتمبر|أكتوبر|نوفمبر|ديسمبر)\b", "DATE"),
        (r"\b\d{1,2}\s+(جانفي|فيفري|مارس|أفريل|ماي|جوان|جويلية|أوت|سبتمبر|أكتوبر|نوفمبر|ديسمبر)\b", "DATE"),

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
        (r"\b(lyoum|liia?ma)\b", "DATE"),
        (r"\b(ghodwa|ghoudwa|bekra)\b", "DATE"),
        (r"\b(b[ae]d ghodwa|ba[cd] ghodwa|b[ae]d bekra)\b", "DATE"),
        (r"\b(essbe[mn]o? el j[ae]y|simaana el j[ae]ya)\b", "DATE"),
        (r"\b(echhar ejj[ae]y|el[ -]?[ae]m el j[ae]y)\b", "DATE"),
        (r"\b(essbe[mn]o? el f[ae]t?et|simaana el f[ae]t?ya)\b", "DATE"),
        (r"\b(echhar el f[ae]t|el[ -]?[ae]m el f[ae]t)\b", "DATE"),
        (r"\b(ghodwa s[sz]beh?|ghodwa sba[hh])\b", "DATE"),
        (r"\b(ghodwa l[iy]l|ghodwa liil)\b", "DATE"),
        (r"\b(b[ae]d ([\d]+|[\w]+) (iy[a]m|s[wk]ana?|chhour|snin|snine|sanawa?t))\b", "DATE"),
        (r"\b([\d]+) (iy[a]m|s[wk]ana?|chhour|snin|snine) (lou[zw]el|j[ae]y[a]?)\b", "DATE"),
        (r"\bfi (simaana|echhar|el[ -]?am) ejj[ae]y\b", "DATE"),
        (r"\b(lekh[dr]a|le5ra|el okhra)\b", "DATE"),
        (r"\b(el okher|lekh[dr])\b", "DATE"),
        (r"\b(tawwa|tawa|dnow?ba)\b", "DATE"),
        (r"\b(bar[cs]a|bekri)\b", "DATE"),
        (r"\b(m[ae]ta nhar|chhal fi nhar)\b", "DATE"),

        # Jours de la semaine en Derja (latin)
        (r"\b(lethn[iy]n|tne[n]|tenin)\b", "DATE"),
        (r"\b(ethl[ae]th[a]?|tlata|tlatha)\b", "DATE"),
        (r"\b(larbo[uw][cd]?|arb[o]a|arb3a)\b", "DATE"),
        (r"\b(lekhmi[cs]|khem[cs]|5mis)\b", "DATE"),
        (r"\b(ejjom[uo]?a|jom3a|jmou3a)\b", "DATE"),
        (r"\b(essebt|sebt|sibt)\b", "DATE"),
        (r"\b(el[ -]?[ah]ad|[ah]ad)\b", "DATE"),

        # Mois en Derja (latin)
        (r"\b(j[ae]nfi|j[ae]nvi)\b", "DATE"),
        (r"\b(fi[uv]ri|fevri|f[ée]vrier)\b", "DATE"),
        (r"\b(avril|avri|avreel)\b", "DATE"),
        (r"\b(maei?|may|mai)\b", "DATE"),
        (r"\b(ju[oei]n|jwe?n)\b", "DATE"),
        (r"\b(ju[oe]i[l]?|jwi[l]?a?|jwe?l[ae]?)\b", "DATE"),
        (r"\b(ou[ts]|awuss?|awu[s$]s?)\b", "DATE"),
        (r"\b(septemb[re]?|septembi|s[oe]pt)\b", "DATE"),
        (r"\b(octob[re]?|oktob[re]?|oct)\b", "DATE"),
        (r"\b(novemb[re]?|nov)\b", "DATE"),
        (r"\b(d[ée]cemb[re]?|dec)\b", "DATE"),
        (r"\b\d{1,2}\s+(janfi|janvi|fivri|fevri|mars|avril|maei|juin|jwin|juillet|jwila|out|awus|septembre|octobre|novembre|décembre)\b", "DATE"),


        # ===========================
        # ----- DATE Nombres (Multi-lang) -----
        # ===========================
        (r"\b(in|dans|بعد)\s+(one|two|three|four|five|six|seven|eight|nine|ten)\s+(day|days|week|weeks)\b", "DATE"),
        (r"\b(dans|après|after)\s+(un|deux|trois|quatre|cinq|six|sept|huit|neuf|dix)\s+(jour|jours|semaine|semaines)\b", "DATE"),
        (r"\b(b[ae]d)\s+(we[hc]ed|thn[iy]n|thl[ae]tha|arb[o]a|khamsa|s[ei]tta|s[eo]b[o]a|thm[ae]nya|tes[o]ud|e[cs]hra)\s+(iy[a]m|s[wk]ana?)\b", "DATE"),

        # ===========================
        # ----- PRICE (Toutes devises) -----
        # ===========================
        (r"\b\d+(?:[.,]\d{1,2})?\s*(?:€|EUR|euros?)\b", "PRICE"),
        (r"\b\d+(?:[.,]\d{1,2})?\s*(?:USD|US\$|\$|dollars?)\b", "PRICE"),
        (r"\b\d+(?:[.,]\d{1,2})?\s*(?:TND|DT|دينار(?: تونسي)?|د\.ت|د)\b", "PRICE"),
        (r"\b\d+(?:[.,]\d{1,2})?\s*(dinar(?: tunisien)?|dinars? tunisiens?)\b", "PRICE"),
        (r"\b\d+(?:[.,]\d{1,2})?\s*(din[ae]r|dinaar|dinar)\b", "PRICE"),
        (r"\b\d+(?:[.,]\d{3})*(?:[.,]\d{2})?\s*(?:€|\$|د.ت|DT|dinars?)\b", "PRICE"),
        (r"\b\d+[.,]\d{1,2}\s*(?:€|EUR|dollars?|USD|TND|DT|دينار|dinars?)\b", "PRICE"),
        (r"\b(?:de|entre|from|between|min|max)\s+\d+(?:[.,]\d{1,2})?\s*(?:€|\$|DT|dinars?)?\s*(?:à|to|and|-|w|ou)\s*\d+(?:[.,]\d{1,2})?\s*(?:€|\$|DT|dollars?|dinars?)\b", "PRICE"),
        (r"\b(?:à partir de|à|at|for|only|seulement|just|b[ei]h?|b[ie]|bch)\s+\d+(?:[.,]\d{1,2})?\s*(?:€|\$|DT|dinar|euro|dollar|dinars?)\b", "PRICE"),
        (r"\b\d+(?:[.,]\d{1,2})?\s*(?:€|\$|DT|دينار|dinars?)\s*(?:par personne|per person|p\.p\.|HT|TTC|incl\.|excl\.|l[ui] we[hc]ed|loul[d])\b", "PRICE"),

        # ===========================
        # ----- LOCATION (Lieux) -----
        # ===========================
        (r"\b(Tunis|Sfax|Sousse|Nabeul|Hammamet|Monastir|Mahdia|Bizerte|Gabès|Gafsa|Kairouan|Médenine|Tozeur|Tataouine|Béja|Jendouba|Kef|Kasserine|Kébili|Manouba|Siliana|Zaghouan|Ariana|Ben Arous)\b", "LOC"),
        (r"\b(T[uo]nes|Sfa[hk]s|S[ou]ssa|Nab[eo]l|Hamm[ae]met|Mn[ae]stir|Mahdia|Benzert|Gab[èe]s|Gafsa|Q[ae]yraw[ae]n|M[ée]denine|T[ou]zeur|Tataouine|Baja|Jandouba|Kef|Qasrin|Kebili|Manouba|Silyana|Zaghw[ae]n|Aryana|Ben [Aa]rous)\b", "LOC"),
        (r"\b(aéroport|airport|aeroport|مطار|mat[ae]r?)\s+(?:international\s+)?(?:de\s+)?(Tunis Carthage|Monastir|Sfax Thyna|Tozeur Nefta|Djerba|Tabarka|Hou[ae]r?ia? Boumedienne)\b", "LOC"),
        (r"\b(Tunisie|France|Algérie|Maroc|Libye|Italie|Espagne|Allemagne|Turquie|Émirats|Qatar|Arabie Saoudite)\b", "LOC"),
        (r"\b(T[ou]nisia|Faransa|Dzayer|Maghreb|L[iy]bya|Italia|Spanya|Almanya|Turkiya|Emirates|Qatar|Sa[uo]dia)\b", "LOC"),

        # ===========================
        # ----- ORGANISATIONS -----
        # ===========================
        (r"\b(Tunisair|Nouvelair|Syphax Airlines|Air France|Lufthansa|Emirates|Qatar Airways|Turkish Airlines|Royal Air Maroc|Alitalia|Transavia|Ryanair|EasyJet|British Airways)\b", "ORG"),
        (r"\b(Carlson Wagonlit|Havas Voyages|Nouvelles Frontières|Thomas Cook|Expedia|Booking\.com|Lastminute|Go Voyages)\b", "ORG"),
        (r"\b(Hotel|Hôtel|Fonda?k?)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b", "ORG"),

        # ===========================
        # ----- FLIGHT (Vols) -----
        # ===========================
        (r"\b(vol|flight|رحلة|tayara?|tyara)\s+([A-Z]{2}\d{1,4})\b", "FLIGHT"),
        (r"\b([A-Z]{2}\d{1,4})\s+(?:à destination de|to|vers|arrivant|arriving|mtae?|l[èe])\b", "FLIGHT"),
        (r"\b(départ|departure|اقلاع|i[qg]la[e]?[c]?)\s+(\d{1,2}[:h]\d{2})\b", "FLIGHT"),
        (r"\b(arrivée|arrival|وصول|w[su]o?l)\s+(\d{1,2}[:h]\d{2})\b", "FLIGHT"),
        (r"\b(vol\s+direct|direct\s+flight|connecting\s+flight|vol\s+avec\s+escale|tayara mba[cs]hra|tayara b[ie] s[c]cala)\b", "FLIGHT"),

        # ===========================
        # ----- PERSONS (Voyageurs) -----
        # ===========================
        (r"\b(\d+)\s*(?:personnes?|adultes?|voyageurs?|passagers?|pers\.?)\b", "PERSONS"),
        (r"\bpour\s+(\d+)\s*(?:personnes?|adultes?|voyageurs?|passagers?)?\b", "PERSONS"),
        (r"\b(\d+)\s*(?:enfants?|bébés?|juniors?)\b", "PERSONS"),
        (r"\b(\d+)\s*(?:persons?|adults?|travelers?|passengers?|guests?|people)\b", "PERSONS"),
        (r"\bfor\s+(\d+)\s*(?:persons?|adults?|travelers?|passengers?|guests?|people)?\b", "PERSONS"),
        (r"\b(\d+)\s*(?:children?|kids?|babies?|infants?)\b", "PERSONS"),
        (r"\b(\d+)\s*(?:أشخاص|شخص|بالغ|بالغين|مسافر|مسافرين|راكب|ركاب)\b", "PERSONS"),
        (r"\b(\d+)\s*(?:pers?|ashk[hâa][sc]|n[ae][sc]|nes)\b", "PERSONS"),
        (r"\b(we[hc]ed|thn[iy]n|thl[ae]tha|arb[o]a|khamsa|s[ei]tta|s[eo]b[o]a|thm[ae]nya|tes[o]ud|e[cs]hra)\s*(?:pers?|ashk[hâa][sc]|n[ae][sc]|nes)\b", "PERSONS"),
        (r"\b(we[hc]ed|tnin|tlata|arba3a|khamsa|s[ei]tta|sba3a|thmanya|ts3od|3achra)\s*(?:pers?|ashk[hâa][sc])?\b", "PERSONS"),
        (r"\b(\d+)\s*(?:ناس|شخص|أشخاص|بالغين)\b", "PERSONS"),

        # ===========================
        # ----- DURATION -----
        # ===========================

        # ── FRANÇAIS ────────────────────────────────────────
        (r"\b(\d+)\s*nuits?\b",                                    "DURATION"),
        (r"\b(\d+)\s*jours?\b",                                    "DURATION"),
        (r"\b(\d+)\s*semaines?\b",                                 "DURATION"),
        (r"\b(\d+)\s*mois\b",                                      "DURATION"),
        (r"\bpour\s+(\d+)\s*(?:jours?|nuits?|semaines?|mois)\b",  "DURATION"),
        (r"\bséjour\s+de\s+(\d+)\s*(?:jours?|nuits?|semaines?|mois)\b", "DURATION"),

        # ── ANGLAIS ─────────────────────────────────────────
        (r"\b(\d+)\s*nights?\b",                                   "DURATION"),
        (r"\b(\d+)\s*days?\b",                                     "DURATION"),
        (r"\b(\d+)\s*weeks?\b",                                    "DURATION"),
        (r"\b(\d+)\s*months?\b",                                   "DURATION"),
        (r"\bfor\s+(\d+)\s*(?:nights?|days?|weeks?|months?)\b",   "DURATION"),
        (r"\bstay\s+of\s+(\d+)\s*(?:nights?|days?|weeks?|months?)\b", "DURATION"),

        # ── ARABE STANDARD ──────────────────────────────────
        (r"\b(\d+)\s*(?:ليلة|ليالي)\b",                           "DURATION"),
        (r"\b(\d+)\s*(?:يوم|أيام)\b",                             "DURATION"),
        (r"\b(\d+)\s*(?:أسبوع|أسابيع)\b",                         "DURATION"),
        (r"\b(\d+)\s*(?:شهر|أشهر)\b",                             "DURATION"),
        (r"\bلمدة\s+(\d+)\s*(?:ليلة|ليالي|يوم|أيام|أسبوع|أسابيع|شهر|أشهر)\b", "DURATION"),

        # ── DERJA LATIN ─────────────────────────────────────
        (r"\b(\d+)\s*(?:lyal?i|lil?a|layali)\b",                  "DURATION"),
        (r"\b(\d+)\s*(?:iyam|yom|nharat?)\b",                     "DURATION"),
        (r"\b(\d+)\s*(?:s[e]?mana?|simaana?)\b",                  "DURATION"),
        (r"\b(\d+)\s*(?:chh?our|chhour)\b",                       "DURATION"),
        (r"\bbch\s+(\d+)\s*(?:lyal?i|iyam|s[e]?mana?|chhour)\b", "DURATION"),

        # ── DERJA ARABE ─────────────────────────────────────
        (r"\b(\d+)\s*(?:ليلة|ليالي|يوم|أيام|سيمانة|شهر)\b",     "DURATION"),
        (r"\bبش\s+(\d+)\s*(?:ليلة|ليالي|يوم|أيام)\b",            "DURATION"),
    ]

    matches = []
    for p, label in patterns:
        for m in re.finditer(p, text, flags=re.IGNORECASE):
            matches.append({"entity": label, "value": m.group()})
    return matches


# ── Normalisation ─────────────────────────────────────────────
def normalize_date(fragment, context=None):
    if context and not re.search(r"\d{4}", fragment):
        fragment = f"{fragment} {context}"
    dt = dateparser.parse(fragment, languages=['fr', 'en', 'ar'])
    return dt.strftime("%Y-%m-%d") if dt else fragment

def normalize_price(text):
    match = re.search(r"([\d.,]+)\s*([^\d\s]+)?", text)
    if match:
        amount   = float(match.group(1).replace(',', '.'))
        currency = match.group(2) if match.group(2) else None
        return {"amount": amount, "currency": currency}
    return {"amount": text, "currency": None}

def extract_implicit_persons(text: str) -> Optional[int]:
    """
    Détecte le nombre de personnes implicites dans le texte.
    Retourne le nombre ou None si pas détecté.
    """
    text_lower = text.lower().strip()

    # ── Garde : ignorer si c'est une durée ──────────────────────────────
    duration_keywords = r"(?:jour|jours|day|days|semaine|semaines|week|weeks|mois|month|months|an|ans|year|years|nuit|nuits|night|nights|heure|heures|hour|hours)"
    if re.search(r"\d+\s*" + duration_keywords, text_lower, re.IGNORECASE):
        return None

    # ── CAS : voyage seul ────────────────────────────────────────────────
    SOLO_PATTERNS = [
        # FR
        r"\b(je\s+voyage\s+seul|je\s+pars\s+seul[e]?|juste\s+moi|pour\s+moi\s+seul[e]?|moi\s+seul[e]?|tout\s+seul[e]?)\b",
        # EN
        r"\b(traveling\s+alone|just\s+me|by\s+myself|solo\s+trip|i\s+am\s+alone)\b",
        # AR
        r"\b(وحدي|بمفردي|سأسافر\s+وحدي)\b",
        # Derja
        r"\b(wa7di|wa7dik|seul[e]?)\b",
    ]
    for p in SOLO_PATTERNS:
        if re.search(p, text_lower, re.IGNORECASE):
            return 1

    # ── CAS : nous sommes N / on est N ──────────────────────────────────
    WE_ARE_N = [
        r"\b(?:nous\s+sommes|on\s+est|on\s+sera|nous\s+serons|on\s+voyage\s+à)\s+(\d+)\b",
        r"\b(?:we\s+are|there\s+are|group\s+of)\s+(\d+)\b",
        r"\b(?:نحن|كنا)\s+(\d+)\b",
        r"\b(en\s+(?:groupe\s+de|comité\s+de))\s+(\d+)\b",
    ]
    for p in WE_ARE_N:
        m = re.search(p, text_lower, re.IGNORECASE)
        if m:
            # Prendre le dernier groupe capturé (le nombre)
            num = m.group(m.lastindex)
            try:
                return int(num)
            except:
                pass

    # ── CAS : nous deux/trois/etc ────────────────────────────────────────
    NOUS_N = {
        r"\b(nous\s+deux|on\s+est\s+deux|on\s+voyage\s+à\s+deux|à\s+deux|en\s+couple)\b": 2,
        r"\b(nous\s+trois|on\s+est\s+trois|à\s+trois)\b": 3,
        r"\b(nous\s+quatre|on\s+est\s+quatre|à\s+quatre)\b": 4,
        r"\b(nous\s+cinq|on\s+est\s+cinq|à\s+cinq)\b": 5,
        r"\b(we\s+two|the\s+two\s+of\s+us|just\s+the\s+two)\b": 2,
        r"\b(the\s+three\s+of\s+us)\b": 3,
        r"\b(نحن\s+اثنان|نحن\s+ثلاثة|نحن\s+أربعة)\b": 2,
        r"\b(zouz|thnin|tnin)\b": 2,
        r"\b(tlata|thlatha)\b": 3,
        r"\b(arba3a|arba)\b": 4,
    }
    for p, n in NOUS_N.items():
        if re.search(p, text_lower, re.IGNORECASE):
            return n

    # ── CAS : relations implicites (moi = base 1 + relations) ───────────
    # Détecter si "moi/je/ana/أنا" est présent
    HAS_SELF = bool(re.search(
        r"\b(moi|je|pour moi|ana|أنا|ena|je\s+suis)\b",
        text_lower, re.IGNORECASE
    ))

    count = 1 if HAS_SELF else 0

    # Relations +1 chacune
    RELATIONS_PLUS_1 = [
        # Conjoint FR
        r"\b(ma\s+femme|mon\s+mari|mon\s+épouse|mon\s+époux|ma\s+moitié|mon\s+compagnon|ma\s+compagne|mon\s+partenaire|ma\s+partenaire|mon\s+petit\s+ami|ma\s+petite\s+amie|mon\s+fiancé[e]?)\b",
        # Parent FR
        r"\b(mon\s+père|ma\s+mère|mon\s+papa|ma\s+maman)\b",
        # Fratrie FR
        r"\b(mon\s+frère|ma\s+sœur|ma\s+soeur)\b",
        # Ami FR (singulier)
        r"\b(mon\s+ami[e]?|ma\s+copine|mon\s+copain|un\s+ami[e]?)\b",
        # EN
        r"\b(my\s+wife|my\s+husband|my\s+partner|my\s+girlfriend|my\s+boyfriend|my\s+fiancee?)\b",
        r"\b(my\s+father|my\s+mother|my\s+dad|my\s+mom)\b",
        r"\b(my\s+brother|my\s+sister)\b",
        r"\b(my\s+friend|a\s+friend)\b",
        # AR
        r"\b(زوجتي|زوجي|خطيبي|خطيبتي)\b",
        r"\b(أمي|أبي|والدي|والدتي)\b",
        r"\b(أخي|أختي)\b",
        r"\b(صديقي|صديقتي)\b",
        # Derja
        r"\b(mrti|rajli|khouya|okhti|ommi|baba|sa7bi|sahbi|sa7bti)\b",
    ]

    for p in RELATIONS_PLUS_1:
        if re.search(p, text_lower, re.IGNORECASE):
            count += 1

    # Parents (les deux) → +2
    if re.search(r"\b(mes\s+parents|my\s+parents|والديّ|waliday)\b", text_lower, re.IGNORECASE):
        count += 2

    # Frères et sœurs (pluriel) → +2
    if re.search(r"\b(mes\s+frères\s+et\s+sœurs|mes\s+frères|mes\s+sœurs)\b", text_lower, re.IGNORECASE):
        count += 2

    # Enfants avec nombre → +N
    children_with_num = re.search(
        r"\b(?:mes|my|أطفالي|wladi)\s+(\d+)\s*(?:enfants?|fils|filles?|kids?|children|أطفال|أبناء)\b",
        text_lower, re.IGNORECASE
    )
    if children_with_num:
        count += int(children_with_num.group(1))
    elif re.search(r"\b(mon\s+fils|ma\s+fille|mon\s+enfant|my\s+son|my\s+daughter|my\s+child|ابني|بنتي|wledi|benti)\b", text_lower, re.IGNORECASE):
        count += 1

    # Amis avec nombre → +N
    friends_with_num = re.search(
        r"\b(?:mes|my)\s+(\d+)\s*(?:amis?|ami[e]s?|friends?|copains?|copines?)\b",
        text_lower, re.IGNORECASE
    )
    if friends_with_num:
        count += int(friends_with_num.group(1))

    # Si count > 1 → on a détecté des relations
    if count > 1:
        return count

    # ── Fallback → None (pas de personnes implicites détectées) ─────────
    return None


def normalize_persons(text: str) -> Optional[str]:
    """
    Extrait le nombre de personnes depuis le texte.
    Retourne None si c'est une durée ou pas détectable.
    """
    # ── Garde : ignorer si c'est une durée ──────────────────────────────
    duration_keywords = r"(?:jour|jours|day|days|semaine|semaines|week|weeks|mois|month|months|an|ans|year|years|nuit|nuits|night|nights)"
    if re.search(r"\d+\s*" + duration_keywords, text, re.IGNORECASE):
        return None

    WORDS_MAP = {
        "wehed": 1, "wa7ed": 1, "wahid": 1, "wehced": 1,
        "un": 1, "une": 1, "واحد": 1,
        "tnin": 2, "thnin": 2, "zouz": 2, "zoz": 2, "zuz": 2,
        "deux": 2, "اثنين": 2, "زوز": 2,
        "tlata": 3, "thlatha": 3, "trois": 3, "ثلاثة": 3,
        "arba3a": 4, "arba": 4, "arbaa": 4, "quatre": 4, "أربعة": 4,
        "khamsa": 5, "5amsa": 5, "cinq": 5, "خمسة": 5,
        "sitta": 6, "sita": 6, "setta": 6, "six": 6, "ستة": 6,
        "sba3a": 7, "sebaa": 7, "sept": 7, "سبعة": 7,
        "thmanya": 8, "thmania": 8, "tmenya": 8, "huit": 8, "ثمانية": 8,
        "ts3a": 9, "tes3a": 9, "neuf": 9, "تسعة": 9,
        "3achra": 10, "achra": 10, "dix": 10, "عشرة": 10,
    }

    text_lower = text.lower().strip()

    for word, num in WORDS_MAP.items():
        if word in text_lower:
            return str(num)

    patterns = [
        r"(\d+)\s*(?:personnes?|voyageurs?|pax|adultes?|passagers?)",
        r"(?:pour|de|à)\s*(\d+)\s*(?:personnes?)?",
        r"(\d+)\s*(?:pers|px)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            return match.group(1)

    # Nombre isolé — seulement si accompagné de contexte voyage
    if re.search(r"(\d+)\s*(?:personnes?|adultes?|voyageurs?|passagers?|pax)", text_lower):
        m = re.search(r"(\d+)", text)
        if m:
            return m.group(1)

    return None  # ← plus de fallback isolé

def extract_date_range(text):
    patterns = [
        r"(?:du|de|from)\s+(.+?)\s+(?:au|à|to)\s+(.+?)(?=\s+(?:pour|avec|et|afin|,|\.)|[,.]|$)",
        r"\bمن\s+(.+?)\s+إلى\s+(.+?)(?=[,.]|$)",
    ]
    DP_SETTINGS = {"PREFER_DATES_FROM": "future", "RETURN_AS_TIMEZONE_AWARE": False}
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue
        start_raw = m.group(1).strip()
        end_raw   = re.split(r"\s+(?:pour|avec|et|afin)\b", m.group(2).strip(), flags=re.IGNORECASE)[0].strip()
        return_date = dateparser.parse(end_raw, languages=["fr", "en", "ar"], settings=DP_SETTINGS)
        if not return_date:
            continue
        departure = dateparser.parse(start_raw, languages=["fr", "en", "ar"], settings=DP_SETTINGS)
        if not departure or departure > return_date:
            continue
        return {
            "departure_date": departure.strftime("%Y-%m-%d"),
            "return_date":    return_date.strftime("%Y-%m-%d"),
        }
    return None


# ── Fusion AMOUNT + CURRENCY → PRICE ─────────────────────────
def merge_amount_currency(payload: dict) -> dict:
    amounts    = payload.pop("AMOUNT", [])
    currencies = payload.pop("CURRENCY", [])

    if amounts:
        prices = []
        for i, amount in enumerate(amounts):
            currency = currencies[i] if i < len(currencies) else None
            if isinstance(amount, dict):
                prices.append(amount)
            else:
                prices.append({"amount": amount, "currency": currency})
        payload["PRICE"] = prices

    return payload

def detect_loc_context(text: str, loc_value: str) -> str:
    """
    Détecte si un LOC est une origine ou destination
    en cherchant les mots clés proches dans le texte.
    Retourne "origin" ou "destination"
    """
    text_lower = text.lower()
    loc_lower  = loc_value.lower()
    
    # Position du LOC dans le texte
    loc_pos = text_lower.find(loc_lower)
    if loc_pos == -1:
        return "destination"  # défaut
    
    # Fenêtre de recherche : 30 caractères avant le LOC
    window_before = text_lower[max(0, loc_pos - 30): loc_pos]
    
    ORIGIN_KEYWORDS = [
        # FR
        "depuis", "partir de", "au départ de", "quitter",
        "en provenance de", "décollage de", "embarquement de",
        "vol depuis", "de",
        # EN
        "from", "leaving", "departing from", "flying from",
        "coming from", "out of", "originating from",
        # AR
        "من", "انطلاقا من", "مغادرة", "قادم من", "إقلاع من",
        # Derja latin
        "men", "mni",
    ]
    
    DESTINATION_KEYWORDS = [
        # FR
        "à", "vers", "pour", "aller à", "arriver à",
        "destination", "se rendre à", "en direction de",
        "à destination de", "vol pour", "voyager à",
        "rejoindre", "cap sur", "direction",
        # EN
        "to", "towards", "going to", "arriving at",
        "heading to", "bound for", "flying to",
        "traveling to", "reaching",
        # AR
        "إلى", "نحو", "وجهة", "باتجاه", "للسفر إلى",
        # Derja latin
        "lel", "fel", "bach nemchi", "bach nrou7",
    ]
    
    # Chercher mots clés ORIGINE en priorité (plus spécifiques)
    for kw in ORIGIN_KEYWORDS:
        if kw in window_before:
            return "origin"
    
    # Chercher mots clés DESTINATION
    for kw in DESTINATION_KEYWORDS:
        if kw in window_before:
            return "destination"
    
    # Défaut → destination
    return "destination"
# ── Prédiction principale ─────────────────────────────────────
def predict_ner(text: str) -> dict:
    tokenizer, model = _load_ner()
    tokens   = text.split()
    inputs   = tokenizer(
        tokens, return_tensors="pt",
        is_split_into_words=True,
        truncation=True, max_length=128
    ).to(DEVICE)
    word_ids = tokenizer(
        tokens, is_split_into_words=True,
        truncation=True, max_length=128
    ).word_ids()

    with torch.no_grad():
        logits = model(**inputs).logits

    preds = torch.argmax(logits, dim=-1)[0].cpu().numpy()

    # ── Extraction entités depuis le modèle ──
    entities, current, seen = [], None, set()
    for wid, pred in zip(word_ids, preds):
        if wid is None or wid in seen:
            continue
        seen.add(wid)
        label = ID2LABEL_NER.get(pred, "O")
        word  = tokens[wid] if wid < len(tokens) else ""
        if label.startswith("B-"):
            if current:
                entities.append(current)
            current = {"entity": label[2:], "value": word}
        elif label.startswith("I-") and current:
            current["value"] += " " + word
        else:
            if current:
                entities.append(current)
            current = None
    if current:
        entities.append(current)

    # ── Fusion avec regex ──
    regex_extra = regex_entities(text)
    for r in regex_extra:
        if not any(e["value"].lower() == r["value"].lower() for e in entities):
            entities.append(r)

    # ── Post-filtre ORGANIZATION ──
    entities = [
        e for e in entities
        if not (
            e["entity"] == "ORGANIZATION" and
            any(mot in MOTS_PARASITES_ORG for mot in e["value"].lower().split())
        )
    ]

    # ── Normalisation ──
    payload = defaultdict(list)
    for ent in entities:
        ent_type = ent["entity"]
        if ent_type == "DATE":
            payload["DATE"].append(ent["value"])
        elif ent_type in ("AMOUNT", "CURRENCY"):
            payload[ent_type].append(ent["value"])
        elif ent_type == "PERSONS":
            val_str = str(ent["value"]).strip()
            num_match = re.search(r"\d+", val_str)
            num_str = num_match.group() if num_match else val_str  # ex: "5"

            duration_context = re.search(
                rf"\b{re.escape(num_str)}\s*(?:"  # ← num_str pas val_str
                r"nuit|nuits|jour|jours|semaine|semaines|mois|"
                r"night|nights|day|days|week|weeks|month|months|"
                r"lyal|lyali|lila|iyam|yom|nharat|semana|simaana|chhour|"
                r"ليلة|ليالي|يوم|أيام|أسبوع|أسابيع|شهر|أشهر|"
                r"سيمانة|نهار"
                r")\b",
                text, re.IGNORECASE
            )
            if not duration_context:
                val = normalize_persons(ent["value"])
                if val is not None:
                    payload["PERSONS"].append(val)
        elif ent_type == "ORGANIZATION":
            payload["ORG"].append(ent["value"].strip())
        elif ent_type == "DURATION":
            payload["DURATION"].append(ent["value"].strip())
        else:
            payload[ent_type].append(ent["value"].strip())

    # ── Personnes implicites ──────────────────────────────────────────────
    if not payload.get("PERSONS"):
        implicit = extract_implicit_persons(text)
        if implicit is not None:
            payload["PERSONS"] = [str(implicit)]

    # ── Normalisation dates ──
    normalized_dates = []
    for d in payload.get("DATE", []):
        dt = normalize_date(d)
        if dt:
            normalized_dates.append(dt)
    if normalized_dates:
        payload["DATE"] = normalized_dates

    # ── Date range ──
    date_range = extract_date_range(text)
    if date_range:
        payload["DATE_RANGE"] = [date_range]
        payload["DATE"] = [date_range["departure_date"], date_range["return_date"]]

    # ── PERSONS : garder max ──
    if "PERSONS" in payload:
        persons_values = []
        for v in payload["PERSONS"]:
            try:
                persons_values.append(int(v))
            except (ValueError, TypeError):
                pass
        if persons_values:
            payload["PERSONS"] = [str(max(persons_values))]

    # ── Fusion AMOUNT + CURRENCY → PRICE ──
    result = merge_amount_currency(dict(payload))

    return result