import re
import redis
import json
from typing import Optional
from datetime import date as _date
from deep_translator import GoogleTranslator
import logging
logger = logging.getLogger("chat_logger")
_redis_client: Optional[redis.Redis] = None


ENTITY_TO_SLOT = {
    "LOC":     ["destination", "origine"],   
    "DATE":    ["date_depart", "date_retour"],
    "PRICE":   ["budget"],
    "FLIGHT":  ["vol"],
    "PERSONS": ["passagers"],
    "ORG":     ["compagnie"],
}

SLOTS_DEFAULT = {
    "destination":      "",
    "origine":          "",
    "budget":           "",
    "date_depart":      "",
    "date_retour":      "",
    "passagers":        "",
    "vol":              "",
    "compagnie":        "",
    "ton_voyage":       "",    
    "budget_implicite": "",    
}
LANG_MAP = {
    "query_ar":      "ar",
    "query_derja_l": "ar",   
    "query_derja_a": "ar",
    "query_en":      "en",
    "query_fr":      "fr", 
}

SKIP_TRANSLATION = {"PRICE", "FLIGHT", "PERSONS", "DATE_RANGE", "DURATION"}

GRAPH_STATE_DEFAULT = {
    "current_node":    "",
    "last_intent":     None,
    "waiting_for":     None,
    "slots_category":  "global",
    "ambiguous_queue": [],
}

SUBCAT_TO_SLOT_KEY = {
    "Flight Booking":         "flight",
    "Flight Management":      "flight",
    "Hotel Booking":          "hotel",
    "Hotel Management":       "hotel",
    "Car Rental Booking":     "car",
    "Car Rental Management":  "car",
    "Restaurant Reservation": "restaurant",
    "Event Ticket Booking":   "event",
    "Transportation Search":  "transport",
    "Tour/Excursion Booking": "tour",
    "Tour/Excursion Management": "tour",
    "Airport Logistics":      "flight",
    "Late Check-Out":         "hotel",
    "Check-In":               None,   
}

NON_PERTINENT_TYPES = {"status", "clarification", "rejected", "blocked"}


GEO_OVERRIDES_AR_FR: dict[str, str] = {

    # ──────────────────────────────────────────────
    # TUNISIE — Gouvernorats
    # ──────────────────────────────────────────────
    "تونس": "Tunis",
    "أريانة": "Ariana",
    "بن عروس": "Ben Arous",
    "منوبة": "Manouba",
    "نابل": "Nabeul",
    "زغوان": "Zaghouan",
    "بنزرت": "Bizerte",
    "باجة": "Béja",
    "جندوبة": "Jendouba",
    "الكاف": "Le Kef",
    "سليانة": "Siliana",
    "سوسة": "Sousse",
    "المنستير": "Monastir",
    "المهدية": "Mahdia",
    "صفاقس": "Sfax",
    "القيروان": "Kairouan",
    "القصرين": "Kasserine",
    "سيدي بوزيد": "Sidi Bouzid",
    "قابس": "Gabès",
    "مدنين": "Médenine",
    "تطاوين": "Tataouine",
    "قفصة": "Gafsa",
    "توزر": "Tozeur",
    "قبلي": "Kébili",

    # ──────────────────────────────────────────────
    # TUNISIE — Villes et délégations principales
    # ──────────────────────────────────────────────
    "الحمامات": "Hammamet",
    "جربة": "Djerba",
    "قرطاج": "Carthage",
    "سيدي بوسعيد": "Sidi Bou Saïd",
    "المرسى": "La Marsa",
    "حمام الأنف": "Hammam Lif",
    "بوسالم": "Bou Salem",
    "طبرقة": "Tabarka",
    "عين دراهم": "Aïn Draham",
    "الشابة": "Chebba",
    "الجم": "El Jem",
    "قرمبالية": "Grombalia",
    "النفيضة": "Enfidha",
    "هرقلة": "Hergla",
    "مساكن": "Msaken",
    "القلعة الكبرى": "Kairouan (Kalaa Kebira)",
    "الحوارب": "Haoureb",
    "سبيطلة": "Sbeitla",
    "تالة": "Thala",
    "مطماطة": "Matmata",
    "بني خداش": "Beni Khédache",
    "جرجيس": "Zarzis",
    "بنقردان": "Ben Guerdane",
    "حومة السوق": "Houmt Souk",
    "أجيم": "Ajim",
    "الشط": "Le Chatt",
    "دوز": "Douz",
    "نفطة": "Nefta",
    "تجنين": "Tijinin",
    "المكناسي": "Meknassy",
    "الرديف": "Redeyef",
    "ام العرائس": "Oum Larayes",

    # ──────────────────────────────────────────────
    # MAROC — Villes principales
    # ──────────────────────────────────────────────
    "الرباط": "Rabat",
    "الدار البيضاء": "Casablanca",
    "مراكش": "Marrakech",
    "فاس": "Fès",
    "مكناس": "Meknès",
    "أكادير": "Agadir",
    "طنجة": "Tanger",
    "وجدة": "Oujda",
    "تطوان": "Tétouan",
    "الصويرة": "Essaouira",
    "ورزازات": "Ouarzazate",
    "إفران": "Ifrane",
    "الداخلة": "Dakhla",
    "العيون": "Laâyoune",
    "القنيطرة": "Kénitra",
    "سلا": "Salé",
    "الجديدة": "El Jadida",
    "بني ملال": "Béni Mellal",
    "خريبكة": "Khouribga",

    # ──────────────────────────────────────────────
    # ALGÉRIE — Villes principales
    # ──────────────────────────────────────────────
    "الجزائر": "Alger",
    "وهران": "Oran",
    "قسنطينة": "Constantine",
    "عنابة": "Annaba",
    "بجاية": "Béjaïa",
    "سطيف": "Sétif",
    "تلمسان": "Tlemcen",
    "بسكرة": "Biskra",
    "ورقلة": "Ouargla",
    "تيزي وزو": "Tizi Ouzou",
    "البليدة": "Blida",
    "تيارت": "Tiaret",
    "مستغانم": "Mostaganem",
    "باتنة": "Batna",
    "تمنراست": "Tamanrasset",
    "غرداية": "Ghardaïa",

    # ──────────────────────────────────────────────
    # PAYS ARABES — Capitales et villes majeures
    # ──────────────────────────────────────────────
    "القاهرة": "Le Caire",
    "الإسكندرية": "Alexandrie",
    "الأسكندرية": "Alexandrie",
    "الخرطوم": "Khartoum",
    "بغداد": "Bagdad",
    "الموصل": "Mossoul",
    "البصرة": "Bassorah",
    "دمشق": "Damas",
    "حلب": "Alep",
    "حمص": "Homs",
    "بيروت": "Beyrouth",
    "عمان": "Amman",
    "القدس": "Jérusalem",
    "تل أبيب": "Tel Aviv",
    "الرياض": "Riyad",
    "جدة": "Djeddah",
    "مكة المكرمة": "La Mecque",
    "مكة": "La Mecque",
    "المدينة المنورة": "Médine",
    "أبوظبي": "Abou Dhabi",
    "أبو ظبي": "Abou Dhabi",
    "دبي": "Dubaï",
    "الدوحة": "Doha",
    "المنامة": "Manama",
    "مسقط": "Mascate",
    "صنعاء": "Sanaa",
    "عدن": "Aden",
    "طرابلس": "Tripoli",
    "بنغازي": "Benghazi",
    "نواكشوط": "Nouakchott",
    "الخليل": "Hébron",
    "غزة": "Gaza",
    "رام الله": "Ramallah",
    "نابلس": "Naplouse",
    "الكويت": "Koweït",

    # ──────────────────────────────────────────────
    # PAYS (noms de pays arabes)
    # ──────────────────────────────────────────────
    "تونس الدولة": "Tunisie",
    "المغرب": "Maroc",
    "الجزائر الدولة": "Algérie",
    "ليبيا": "Libye",
    "موريتانيا": "Mauritanie",
    "مصر": "Égypte",
    "السودان": "Soudan",
    "لبنان": "Liban",
    "سوريا": "Syrie",
    "الأردن": "Jordanie",
    "فلسطين": "Palestine",
    "العراق": "Irak",
    "السعودية": "Arabie saoudite",
    "الإمارات": "Émirats arabes unis",
    "قطر": "Qatar",
    "البحرين": "Bahreïn",
    "الكويت الدولة": "Koweït",
    "عمان الدولة": "Oman",
    "اليمن": "Yémen",
    "الصومال": "Somalie",
    "جيبوتي": "Djibouti",
    "جزر القمر": "Comores",


    # ──────────────────────────────────────────────
    # VARIANTES SANS ARTICLE ال
    # ──────────────────────────────────────────────
    "كاف": "Le Kef",
    "منستير": "Monastir",
    "مهدية": "Mahdia",
    "قيروان": "Kairouan",
    "قصرين": "Kasserine",
    "حمامات": "Hammamet",
    "مرسى": "La Marsa",
    "شابة": "Chebba",
    "جم": "El Jem",
    "نفيضة": "Enfidha",
    "قلعة الكبرى": "Kairouan (Kalaa Kebira)",
    "حوارب": "Haoureb",
    "شط": "Le Chatt",
    "مكناسي": "Meknassy",
    "رديف": "Redeyef",
    "رباط": "Rabat",
    "دار البيضاء": "Casablanca",
    "صويرة": "Essaouira",
    "داخلة": "Dakhla",
    "عيون": "Laâyoune",
    "قنيطرة": "Kénitra",
    "جديدة": "El Jadida",
    "جزائر": "Alger",
    "بليدة": "Blida",
    "قاهرة": "Le Caire",
    "إسكندرية": "Alexandrie",
    "أسكندرية": "Alexandrie",
    "خرطوم": "Khartoum",
    "موصل": "Mossoul",
    "بصرة": "Bassorah",
    "قدس": "Jérusalem",
    "رياض": "Riyad",
    "مدينة المنورة": "Médine",
    "دوحة": "Doha",
    "منامة": "Manama",
    "خليل": "Hébron",
    "كويت": "Koweït",
    "مغرب": "Maroc",
    "جزائر الدولة": "Algérie",
    "سودان": "Soudan",
    "أردن": "Jordanie",
    "عراق": "Irak",
    "سعودية": "Arabie saoudite",
    "إمارات": "Émirats arabes unis",
    "بحرين": "Bahreïn",
    "كويت الدولة": "Koweït",
    "يمن": "Yémen",
    "صومال": "Somalie",
}

def _parse_date_text(date_str: str) -> str:
    """Convertit '7 juillet', '4 juillet 2026', etc. → '2026-07-07'"""
    if not date_str:
        return date_str
    
    if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str.strip()):
        return date_str.strip()
    
    MOIS = {
        "janvier": "01", "février": "02", "mars": "03",
        "avril": "04", "mai": "05", "juin": "06",
        "juillet": "07", "août": "08", "septembre": "09",
        "octobre": "10", "novembre": "11", "décembre": "12",
    }
    
    date_lower = date_str.lower().strip()
    
    m = re.match(r'(\d{1,2})\s+(\w+)(?:\s+(\d{4}))?', date_lower)
    if m:
        day   = m.group(1).zfill(2)
        month = MOIS.get(m.group(2))
        year  = m.group(3) or "2026"
        if month:
            return f"{year}-{month}-{day}"
    
    return date_str 

def subcat_to_slot_key(sub_category: str, entities: list = None) -> str:
    key = SUBCAT_TO_SLOT_KEY.get(sub_category, "global")
    if key is None:  
        if entities:
            types = [e["entity"] for e in entities]
            if "FLIGHT" in types:
                return "flight"
            if "ORG" in types:
                return "hotel"
        return "global"
    return key

async def get_redis_client() -> Optional[redis.Redis]:
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis.Redis(
                host="localhost", port=6379, db=0, decode_responses=True
            )
            _redis_client.ping()
            print("Redis connecté")
        except redis.exceptions.ConnectionError:
            print("Redis non disponible")
            _redis_client = None
    return _redis_client
def normalize_entity(value, entity_type: str) -> str:
    if isinstance(value, dict):
        value = value.get("value", "")  

    if not isinstance(value, str):
        value = str(value)

    value = value.strip()
    value = value.strip(".,!?;:")
    
    if entity_type in ["LOC", "ORG"]:
        value = " ".join(w.capitalize() for w in value.split())

    elif entity_type == "PRICE":
        value = re.sub(r"euros?", "€", value, flags=re.IGNORECASE)
        value = re.sub(r"usd|us\$|dollars?", "$", value, flags=re.IGNORECASE)
        value = re.sub(r"(dinar(?: tunisien)?|dt|د\.ت|د)", "DT", value, flags=re.IGNORECASE)

    elif entity_type == "DATE":
        value = re.sub(r"\s+", " ", value)

    elif entity_type == "FLIGHT":
        value = value.upper()

    return value

def filter_entity(value: str, min_length: int = 2) -> bool:
    """
    Filtrage simple pour éliminer bruit et valeurs trop courtes
    """
    if not value or len(value.strip()) < min_length:
        return False
    noise_words = ["the", "a", "an", "de", "du", "of", "in", "to"]
    if value.lower() in noise_words:
        return False
    return True

def remove_subentities(values: list) -> list:
    """
    Supprime les sous-entités incluses 
    Exemple: ["avril", "20 avril"] -> ["20 avril"]
    """
    filtered = []
    for v in values:
        if not any(v != other and v.lower() in other.lower() for other in values):
            filtered.append(v)
    return filtered

def _translate_to_french(value: str, source_lang_key: str) -> str:
    source = LANG_MAP.get(source_lang_key, "auto")
    if source == "fr":
        return value

    cleaned = value.strip()
    if not cleaned or len(cleaned) < 2:
        return value
    if cleaned.isdigit():
        return value
    if re.match(r'^\d{4}-\d{2}-\d{2}$', cleaned):
        return value

    cleaned_normalized = " ".join(cleaned.split())

    # ── NOUVEAU : si déjà en caractères latins → pas de traduction ──
    if re.match(r'^[a-zA-ZÀ-ÿ\s\-\.]+$', cleaned_normalized):
        return cleaned_normalized  # ← "tunis", "Paris", "Rome" → gardés tels quels

    if source == "ar":
        if cleaned_normalized in GEO_OVERRIDES_AR_FR:
            result = GEO_OVERRIDES_AR_FR[cleaned_normalized]
            logger.info(f"[Redis:slots] 📖 GeoOverride: '{cleaned_normalized}' → '{result}'")
            return result
        if cleaned_normalized.startswith("ال"):
            without_article = cleaned_normalized[2:]
            if without_article in GEO_OVERRIDES_AR_FR:
                result = GEO_OVERRIDES_AR_FR[without_article]
                logger.info(f"[Redis:slots] 📖 GeoOverride (sans ال): '{without_article}' → '{result}'")
                return result

    try:
        translated = GoogleTranslator(
            source=source,
            target="fr"
        ).translate(cleaned_normalized)
        return translated if translated else value
    except Exception:
        return value


def store_entities_pipeline(
    redis_client,
    session_id:  str,
    entities:    list,
    tenant_id:   str,
    user_id:     str,
    raw_text:    str,
    language:    str = "query_fr",
    category:    str = "global", 
):
    prefix = f"{tenant_id}:{user_id}:{session_id}"

    logger.info(f"[Redis:slots] ── DÉBUT store_entities_pipeline ──")
    logger.info(f"[Redis:slots] prefix={prefix} | langue={language}")
    logger.info(f"[Redis:slots] {len(entities)} entités reçues : {entities}")

    # ── 1. Meta ───────────────────────────────────────────────────────────
    try:
        redis_client.hset(f"{prefix}:meta", mapping={
            "tenant_id":  tenant_id,
            "user_id":    user_id,
            "session_id": session_id,
            "raw_text":   raw_text,
            "language":   language,
        })
        redis_client.expire(f"{prefix}:meta", 3600)
        logger.info(f"[Redis:slots] ✅ Meta stocké")
    except Exception as e:
        logger.error(f"[Redis:slots] ❌ Meta échoué : {e}")
        return

    # ── 2. Traduction + clés éparses ──────────────────────────────────────
    loc_values  = []
    date_values = []
    date_range  = None

    for ent in entities:
        entity_type = ent["entity"]
        raw_value   = ent["value"]

        if entity_type not in SKIP_TRANSLATION:
            translated = _translate_to_french(str(raw_value), language)
            if translated != raw_value:
                logger.info(
                    f"[Redis:slots] 🌐 Traduction {entity_type} : "
                    f"'{raw_value}' → '{translated}'"
                )
            raw_value = translated

        if entity_type == "DATE":
            raw_value = _parse_date_text(raw_value)

        value = normalize_entity(raw_value, entity_type)

        if entity_type != "PERSONS" and not filter_entity(value):
            logger.debug(f"[Redis:slots] ⏭ Filtré : '{value}' ({entity_type})")
            continue

        if entity_type == "DATE_RANGE":
            if isinstance(ent["value"], dict):
                date_range = ent["value"]
                logger.info(f"[Redis:slots] 📅 DATE_RANGE : {date_range}")
            continue

        key = f"{prefix}:{entity_type}"
        try:
            existing_values = redis_client.lrange(key, 0, -1)
            if value in existing_values:
                redis_client.lrem(key, 0, value)
            redis_client.rpush(key, value)
            final_values = remove_subentities(redis_client.lrange(key, 0, -1))
            redis_client.delete(key)
            for v in final_values:
                redis_client.rpush(key, v)
            redis_client.expire(key, 3600)
            logger.info(f"[Redis:slots] ✅ Clé éparse [{entity_type}] = {final_values}")
        except Exception as e:
            logger.error(f"[Redis:slots] ❌ Clé éparse [{entity_type}] échouée : {e}")

        if entity_type == "LOC":
            loc_values.append(value)
        elif entity_type == "DATE":
            date_values.append(value)

    # ── 3. Slots unifiés ──────────────────────────────────────────────────
    slots_key = f"{prefix}:slots:{category}"
    try:
        current_slots = redis_client.hgetall(slots_key) or {}

        if not current_slots:
            current_slots = SLOTS_DEFAULT.copy()
            logger.info(f"[Redis:slots] 🆕 Nouveaux slots initialisés")
        else:
            logger.info(f"[Redis:slots] 🔄 Slots existants chargés : {current_slots}")

        # ── LOC ───────────────────────────────────────────────────────────
        if len(loc_values) == 1:
            from models.ner_xlm import detect_loc_context
            raw_text_meta = redis_client.hget(f"{prefix}:meta", "raw_text") or raw_text
            context = detect_loc_context(raw_text_meta, loc_values[0])
            
            if context == "origin":
                current_slots["origine"]     = loc_values[0]
                # ne pas écraser destination
                logger.info(f"[Redis:slots] 📍 CAS 1 → origine='{loc_values[0]}'")
            else:
                old_destination = current_slots.get("destination", "")
                current_slots["destination"] = loc_values[0]
                current_slots["origine"]     = ""
                if old_destination and old_destination != loc_values[0] and not date_values and not date_range:
                    current_slots["date_depart"] = ""
                    current_slots["date_retour"] = ""
                logger.info(f"[Redis:slots] 📍 CAS 1 → destination='{loc_values[0]}'")

        elif len(loc_values) >= 2:
            from models.ner_xlm import detect_loc_context
            ctx0 = detect_loc_context(raw_text, loc_values[0])
            ctx1 = detect_loc_context(raw_text, loc_values[1])

            if ctx0 == "origin" and ctx1 == "destination":
                origin_val = loc_values[0]
                dest_val   = loc_values[1]
            elif ctx0 == "destination" and ctx1 == "origin":
                origin_val = loc_values[1]
                dest_val   = loc_values[0]
            else:
                origin_val = loc_values[0]
                dest_val   = loc_values[1]

            current_slots["destination"] = dest_val
            current_slots["origine"]     = origin_val

        elif len(loc_values) == 0 and not date_values and not date_range:
            current_slots["destination"] = ""
            current_slots["origine"]     = ""
            current_slots["date_depart"] = ""
            current_slots["date_retour"] = ""
            logger.info(f"[Redis:slots] 🗑 Pas de LOC ni dates → reset complet → need_more_info")

        today = _date.today().isoformat()
        date_values = list(dict.fromkeys(d for d in date_values if d != today)) or date_values
        date_values = sorted(date_values)


        # ── DATE ──────────────────────────────────────────────────────────
        if date_range:
            if date_range.get("departure_date"):
                current_slots["date_depart"] = date_range["departure_date"]
            if date_range.get("return_date"):
                current_slots["date_retour"] = date_range["return_date"]
        elif date_values:
            normalized_dates = [_parse_date_text(d) for d in date_values]
            current_slots["date_depart"] = normalized_dates[0]
            current_slots["date_retour"] = normalized_dates[1] if len(normalized_dates) > 1 else ""
            logger.info(
                f"[Redis:slots] 📅 Dates → depart='{current_slots['date_depart']}' | "
                f"retour='{current_slots['date_retour']}'"
            )

        # ── PRICE, PERSONS, FLIGHT, ORG — toujours écraser ───────────────
        processed: dict[str, str] = {}
        for ent in entities:
            etype = ent["entity"]
            raw   = ent["value"]
            if etype not in SKIP_TRANSLATION:
                raw = _translate_to_french(str(raw), language)
            processed[etype] = normalize_entity(raw, etype)

        for ent in entities:
            etype = ent["entity"]
            val   = processed.get(etype, "")
            if etype != "PERSONS" and not filter_entity(val):
                continue
            if etype == "PRICE":
                current_slots["budget"]    = val
                logger.info(f"[Redis:slots] 💰 budget='{val}'")
            elif etype == "PERSONS":
                current_slots["passagers"] = val
                logger.info(f"[Redis:slots] 👥 passagers='{val}'")
            elif etype == "FLIGHT":
                current_slots["vol"]       = val
                logger.info(f"[Redis:slots] ✈️ vol='{val}'")
            elif etype == "ORG":
                current_slots["compagnie"] = val
                logger.info(f"[Redis:slots] 🏢 compagnie='{val}'")

        redis_client.hset(slots_key, mapping=current_slots)
        redis_client.expire(slots_key, 3600)
        logger.info(f"[Redis:slots] ✅ Slots finaux stockés : {current_slots}")

    except Exception as e:
        logger.error(f"[Redis:slots] ❌ Slots unifiés échoués : {e}")

    logger.info(f"[Redis:slots] ── FIN store_entities_pipeline ──")

def reset_slots_for_category(
    redis_client: redis.Redis,
    prefix:       str,
    category:     str,
    fields:       list = None,
) -> None:
    """
    Purge les champs LOC/DATE du slot spécifique ET du global.
    À appeler AVANT d'écrire les données du formulaire pour éviter
    que les anciennes valeurs bloquent l'écrasement.
    """
    LOCATION_DATE_FIELDS = ["destination", "origine", "date_depart", "date_retour", "passagers"]
    fields_to_reset = fields if fields else LOCATION_DATE_FIELDS

    for slot_key in [f"{prefix}:slots:{category}", f"{prefix}:slots:global"]:
        existing = redis_client.hgetall(slot_key)
        if not existing:
            continue
        for field in fields_to_reset:
            if field in existing:
                redis_client.hset(slot_key, field, "")
        redis_client.expire(slot_key, 3600)
        logger.info(f"[Redis:reset] 🗑 Purge {fields_to_reset} dans {slot_key}")
        
def get_slots_for_category(
    redis_client: redis.Redis,
    prefix:       str,
    category:     str,
) -> dict:
    """
    Merge slots:global + slots:{category}.
    
    RÈGLE : si le slot spécifique a déjà un contexte de localisation
    (destination OU origine remplie), les champs LOC du global sont ignorés
    — ils appartiennent à un autre segment.
    Pour les autres champs (dates, budget, passagers…), le global reste
    un fallback si le spécifique est vide.
    """
    global_slots   = redis_client.hgetall(f"{prefix}:slots:global") or {}
    specific_slots = redis_client.hgetall(f"{prefix}:slots:{category}") or {}

    if not specific_slots:
        return {**SLOTS_DEFAULT, **global_slots}

    has_specific_location = bool(
        specific_slots.get("destination") or specific_slots.get("origine")
    )

    merged = SLOTS_DEFAULT.copy()

    LOC_FIELDS = {"destination", "origine"}

    for key in SLOTS_DEFAULT:
        specific_val = specific_slots.get(key, "")
        global_val   = global_slots.get(key, "")

        if specific_val:
            merged[key] = specific_val

        elif key in LOC_FIELDS:

            if not has_specific_location and global_val:
                merged[key] = global_val

        else:
            if global_val:
                merged[key] = global_val

    return merged

def get_session_state(
    redis_client: redis.Redis,
    session_id:   str,
    tenant_id:    str,
    user_id:      str,
    category:     str = "global",
) -> dict:
    prefix = f"{tenant_id}:{user_id}:{session_id}"

    meta = redis_client.hgetall(f"{prefix}:meta")

    slots = get_slots_for_category(redis_client, prefix, category)

    pattern = f"{prefix}:*"
    keys    = redis_client.keys(pattern)
    entities = {}
    for key in keys:
        parts = key.split(":")
        entity_type = parts[-1]

        if entity_type in ("meta", "history", "state") or "slots" in parts:
            continue

        try:
            if redis_client.type(key) != "list":  
                continue
            entities[entity_type] = redis_client.lrange(key, 0, -1)
        except Exception as e:
            logger.warning(f"[State] ⏭ Clé ignorée {key} : {e}")

    return {
        "session_id": session_id,
        "tenant_id":  tenant_id,
        "user_id":    user_id,
        "raw_text":   meta.get("raw_text", ""),
        "entities":   entities,   
        "slots":      slots,     
    }
# ── History ───────────────────────────────────────────────────────────────


def is_pertinent(content: str, msg_type: str = None) -> bool:
    if msg_type in NON_PERTINENT_TYPES:
        return False
    if not content or len(content.strip()) < 5:
        return False
    return True


def append_history(
    redis_client: redis.Redis,
    prefix:       str,
    role:         str,       
    content:      str,
    msg_type:     str = None,
    intent:       str = None,
    max_turns:    int = 10,
) -> None:
    """
    Ajoute un message à l'historique si pertinent.
    Garde les 2*max_turns derniers messages (10 user + 10 assistant).
    """
    if not is_pertinent(content, msg_type):
        logger.debug(f"[History] ⏭ Message ignoré (non pertinent) : type={msg_type} | '{content[:30]}'")
        return

    history_key = f"{prefix}:history"

    message = json.dumps({
        "role":    role,
        "content": content.strip(),
        "intent":  intent,
    }, ensure_ascii=False)

    try:
        redis_client.rpush(history_key, message)

        redis_client.ltrim(history_key, -(max_turns * 2), -1)
        redis_client.expire(history_key, 3600)

        logger.info(f"[History] ✅ [{role}] stocké | intent={intent} | '{content[:40]}'")
    except Exception as e:
        logger.error(f"[History] ❌ Échec stockage : {e}")


def get_history(
    redis_client: redis.Redis,
    prefix:       str,
    max_turns:    int = 10,
) -> list:
    """
    Retourne les derniers échanges sous forme de liste de dicts.
    """
    history_key = f"{prefix}:history"
    try:
        raw_messages = redis_client.lrange(history_key, -(max_turns * 2), -1)
        return [json.loads(m) for m in raw_messages]
    except Exception as e:
        logger.error(f"[History] ❌ Échec lecture : {e}")
        return []

def save_graph_state(
    redis_client: redis.Redis,
    prefix:       str,
    current_node: str,
    last_intent:  object = None,
    waiting_for:  str    = None,
    slots_category: str  = "global",
    ambiguous_queue: list = None,
) -> None:
    """
    Sauvegarde l'étape courante du graphe dans Redis.
    Ne sauvegarde PAS si current_node = rejected
    pour ne pas écraser un état valide précédent.
    """
    if current_node == "rejected":
        logger.info("[Redis:state] ⏭ Rejet → state précédent conservé")
        return

    state_key = f"{prefix}:state"
    payload   = {
        "current_node":    current_node,
        "last_intent":     json.dumps(last_intent, ensure_ascii=False) if last_intent else "",
        "waiting_for":     waiting_for   or "",
        "slots_category":  slots_category or "global",
        "ambiguous_queue": json.dumps(ambiguous_queue or [], ensure_ascii=False),
    }
    try:
        redis_client.hset(state_key, mapping=payload)
        redis_client.expire(state_key, 3600)
        logger.info(
            f"[Redis:state] ✅ Sauvegardé | node={current_node} | "
            f"intent={last_intent} | waiting={waiting_for}"
        )
    except Exception as e:
        logger.error(f"[Redis:state] ❌ Échec sauvegarde : {e}")


def get_graph_state(
    redis_client: redis.Redis,
    prefix:       str,
) -> dict:
    """
    Lit l'étape courante du graphe depuis Redis.
    Retourne GRAPH_STATE_DEFAULT si rien trouvé.
    """
    state_key = f"{prefix}:state"
    try:
        raw = redis_client.hgetall(state_key)
        if not raw:
            return GRAPH_STATE_DEFAULT.copy()

        return {
            "current_node":    raw.get("current_node", ""),
            "last_intent":     json.loads(raw["last_intent"])   if raw.get("last_intent")     else None,
            "waiting_for":     raw.get("waiting_for")           or None,
            "slots_category":  raw.get("slots_category",        "global"),
            "ambiguous_queue": json.loads(raw["ambiguous_queue"]) if raw.get("ambiguous_queue") else [],
        }
    except Exception as e:
        logger.error(f"[Redis:state] ❌ Échec lecture : {e}")
        return GRAPH_STATE_DEFAULT.copy()
def debug_session(
    redis_client: redis.Redis,
    session_id:   str,
    tenant_id:    str,
    user_id:      str,
) -> None:
    """
    Affiche dans les logs l'état complet des 4 clés Redis pour une session.
    À appeler depuis orchestrator_node pour diagnostic.
    """
    prefix = f"{tenant_id}:{user_id}:{session_id}"

    logger.info(f"[Redis:debug] ══════════════════════════════════════")
    logger.info(f"[Redis:debug] SESSION : {prefix}")
    logger.info(f"[Redis:debug] ══════════════════════════════════════")

    # ── 1. SLOTS:GLOBAL ───────────────────────────────────────────────────
    global_slots = redis_client.hgetall(f"{prefix}:slots:global")
    logger.info(f"[Redis:debug] 🌍 slots:global → {global_slots or 'VIDE'}")

    # ── 2. SLOTS SPÉCIFIQUES ──────────────────────────────────────────────
    for cat in ("flight", "hotel", "car", "restaurant", "event", "transport", "tour"):
        specific = redis_client.hgetall(f"{prefix}:slots:{cat}")
        if specific:
            logger.info(f"[Redis:debug] 📦 slots:{cat} → {specific}")

    # ── 3. HISTORY ────────────────────────────────────────────────────────
    raw_history = redis_client.lrange(f"{prefix}:history", 0, -1)
    if raw_history:
        logger.info(f"[Redis:debug] 📜 history → {len(raw_history)} messages")
        for i, msg in enumerate(raw_history):
            try:
                parsed = json.loads(msg)
                logger.info(
                    f"[Redis:debug]   [{i+1}] {parsed.get('role','?'):10} | "
                    f"intent={parsed.get('intent','?'):20} | "
                    f"'{parsed.get('content','')[:50]}'"
                )
            except Exception:
                logger.info(f"[Redis:debug]   [{i+1}] {msg[:60]}")
    else:
        logger.info(f"[Redis:debug] 📜 history → VIDE")

    # ── 4. STATE GRAPHE ───────────────────────────────────────────────────
    graph_state = redis_client.hgetall(f"{prefix}:state")
    if graph_state:
        logger.info(f"[Redis:debug] 🔄 state → {graph_state}")
    else:
        logger.info(f"[Redis:debug] 🔄 state → VIDE")

    logger.info(f"[Redis:debug] ══════════════════════════════════════")