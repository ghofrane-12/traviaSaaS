# services/duration.py
"""
Résolution DURATION → dates (departure_date / return_date).

3 cas :
  CAS 1 — DURATION + date déjà dans le message  → calcul direct
  CAS 2 — DURATION seul + réservation en DB      → type inverse (hotel↔flight)
  CAS 3 — DURATION seul + rien en DB             → today + N jours
"""

import re
import logging
from datetime import date, timedelta
from typing import Optional

import asyncpg

logger = logging.getLogger("chat_logger")


# ── Mapping unités → jours ────────────────────────────────────────────────

_UNIT_TO_DAYS: dict[str, int] = {
    # nuit / night  (1 nuit = 1 jour)
    "nuit": 1, "nuits": 1, "night": 1, "nights": 1,
    "lila": 1, "lyal": 1, "lyali": 1, "layali": 1, "liali": 1,
    "ليلة": 1, "ليالي": 1,

    # jour / day
    "jour": 1, "jours": 1, "day": 1, "days": 1,
    "iyam": 1, "yom": 1, "nhar": 1, "nharat": 1,
    "يوم": 1, "أيام": 1,

    # semaine / week
    "semaine": 7, "semaines": 7, "week": 7, "weeks": 7,
    "semana": 7, "simaana": 7, "smaana": 7,
    "أسبوع": 7, "أسابيع": 7, "simaane": 7,

    # mois / month
    "mois": 30, "month": 30, "months": 30,
    "chhour": 30, "chhor": 30, "chher": 30,
    "شهر": 30, "أشهر": 30,
}


def parse_duration_to_days(value: str) -> int:
    """
    Convertit une string DURATION en nombre de jours entier.

    Exemples :
      "15 jours"    → 15
      "2 semaines"  → 14
      "1 mois"      → 30
      "10 nuits"    → 10
      "3 weeks"     → 21
      "٥ أيام"      → 5
      "5 lyali"     → 5

    Retourne 0 si le parsing échoue.
    """
    if not value:
        return 0

    text = value.strip().lower()

    # ── Normalisation chiffres arabes → latins ────────────────────────────
    arabic_digits = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    text = text.translate(arabic_digits)

    # ── Extraction (nombre, unité) ────────────────────────────────────────
    m = re.search(r"(\d+)\s*([^\d\s]+(?:\s+[^\d\s]+)?)", text)
    if not m:
        # Pas de nombre trouvé → 0
        logger.warning(f"[Duration] ❌ Pas de nombre dans '{value}'")
        return 0

    try:
        count = int(m.group(1))
    except ValueError:
        return 0

    raw_unit = m.group(2).strip().rstrip(".,!?")

    # Chercher l'unité en priorité exacte, puis en préfixe
    multiplier = _UNIT_TO_DAYS.get(raw_unit)

    if multiplier is None:
        # Cherche par préfixe (ex: "semaine" dans "semaana")
        for unit_key, mult in _UNIT_TO_DAYS.items():
            if raw_unit.startswith(unit_key) or unit_key.startswith(raw_unit[:4]):
                multiplier = mult
                break

    if multiplier is None:
        logger.warning(f"[Duration] ⚠️ Unité inconnue '{raw_unit}' dans '{value}' → 1 jour par défaut")
        multiplier = 1

    days = count * multiplier
    logger.info(f"[Duration] ✅ '{value}' → {count} × {multiplier} = {days} jours")
    return days


# ── Requête DB (CAS 2) ────────────────────────────────────────────────────

_INVERSE_TYPE: dict[str, str] = {
    "hotel":  "flight",
    "flight": "hotel",
}

_DATE_FIELD: dict[str, str] = {
    "flight": "departure_date",
    "hotel":  "check_in",
}

_QUERY_DB = """
    SELECT departure_date, check_in, booking_type
    FROM user_bookings
    WHERE user_id     = $1
    AND   tenant_id   = $2
    AND   booking_type = $3
    AND   status      = 'completed'
    ORDER BY created_at DESC
    LIMIT 1
"""


async def _fetch_date_from_db(
    pool:             asyncpg.Pool,
    user_id:          str,
    tenant_id:        str,
    search_type:      str,          # type à chercher en DB (inverse de current)
) -> Optional[date]:
    """
    Cherche la dernière réservation du type `search_type` en DB.
    Retourne la date pertinente ou None.
    """
    try:
        import uuid
        row = await pool.fetchrow(
            _QUERY_DB,
            uuid.UUID(user_id),
            uuid.UUID(tenant_id),
            search_type,
        )
        if not row:
            logger.info(f"[Duration] DB → aucune réservation '{search_type}' trouvée")
            return None

        date_field = _DATE_FIELD.get(search_type)
        raw_date   = row[date_field] if date_field else None

        if raw_date is None:
            logger.info(f"[Duration] DB → champ '{date_field}' vide pour '{search_type}'")
            return None

        if isinstance(raw_date, date):
            logger.info(f"[Duration] DB ✅ '{search_type}'.{date_field} = {raw_date}")
            return raw_date

        # Fallback string
        from datetime import datetime
        parsed = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
        logger.info(f"[Duration] DB ✅ '{search_type}'.{date_field} = {parsed} (parsed)")
        return parsed

    except Exception as e:
        logger.error(f"[Duration] ❌ DB fetch échoué : {e}")
        return None


# ── Fonction principale ───────────────────────────────────────────────────

async def resolve_duration_dates(
    duration_days:    int,
    departure_date:   Optional[str],        # déjà extrait par NER (peut être None)
    pool:             asyncpg.Pool,
    user_id:          str,
    tenant_id:        str,
    current_category: str,                  # "hotel" ou "flight"
) -> dict:
    """
    Résout une durée en paire (departure_date, return_date).

    CAS 1 — departure_date fourni → return_date = departure_date + N jours
    CAS 2 — DB avec type inverse  → departure_date DB + N jours
    CAS 3 — fallback today        → today + N jours

    Retourne :
      {"departure_date": "YYYY-MM-DD", "return_date": "YYYY-MM-DD"}
    """
    if duration_days <= 0:
        logger.warning("[Duration] duration_days <= 0 → pas de résolution")
        return {"departure_date": None, "return_date": None}

    # ── CAS 1 : date déjà dans le message ────────────────────────────────
    if departure_date:
        try:
            from datetime import datetime
            dep = datetime.strptime(departure_date[:10], "%Y-%m-%d").date()
            ret = dep + timedelta(days=duration_days)
            logger.info(
                f"[Duration] CAS 1 | dep={dep} + {duration_days}j → ret={ret}"
            )
            return {
                "departure_date": dep.strftime("%Y-%m-%d"),
                "return_date":    ret.strftime("%Y-%m-%d"),
            }
        except Exception as e:
            logger.warning(f"[Duration] CAS 1 parse échoué '{departure_date}' : {e}")

    # ── CAS 2 : chercher en DB (type inverse) ─────────────────────────────
    search_type = _INVERSE_TYPE.get(current_category)

    if search_type and pool and user_id and tenant_id:
        db_date = await _fetch_date_from_db(pool, user_id, tenant_id, search_type)
        if db_date:
            # Ne pas utiliser une date passée
            today = date.today()
            dep   = db_date if db_date >= today else today
            ret   = dep + timedelta(days=duration_days)
            logger.info(
                f"[Duration] CAS 2 | DB({search_type})={db_date} "
                f"→ dep={dep} + {duration_days}j → ret={ret}"
            )
            return {
                "departure_date": dep.strftime("%Y-%m-%d"),
                "return_date":    ret.strftime("%Y-%m-%d"),
            }

    # ── CAS 3 : fallback today ────────────────────────────────────────────
    today = date.today()
    ret   = today + timedelta(days=duration_days)
    logger.info(
        f"[Duration] CAS 3 | today={today} + {duration_days}j → ret={ret}"
    )
    return {
        "departure_date": today.strftime("%Y-%m-%d"),
        "return_date":    ret.strftime("%Y-%m-%d"),
    }