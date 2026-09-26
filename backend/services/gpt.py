# services/gpt.py

import re
import json
import time
import logging
from typing import Optional
import httpx
from core.config import settings

logger = logging.getLogger(__name__)

# =========================================================
# HELPER OPENAI
# =========================================================

def _call_openai_sync(prompt: str, max_tokens: int = 500, json_mode: bool = False) -> str:
    headers = {
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": "gpt-4o-mini",
        "max_tokens": max_tokens,
        "temperature": 0.1,
        "messages": [{"role": "user", "content": prompt}],
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    response = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        json=body,
        timeout=20,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


# =========================================================
# TRANSLATION FUNCTION
# =========================================================
def translate_to_en(text: str, retries: int = 2) -> str:
    if not text or not text.strip():
        return text

    prompt = f"""Translate the following text to English.

RULES:
- Return ONLY the English translation
- No explanation
- No quotes
- No markdown
- Preserve meaning naturally

TEXT:
{text}
"""

    for attempt in range(retries + 1):
        try:
            raw = _call_openai_sync(prompt, max_tokens=256)
            raw = re.sub(r"^```text\s*", "", raw)
            raw = re.sub(r"^```\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            translated = raw.strip()
            if translated:
                logger.info(f"[GPT][translate] success attempt={attempt+1}")
                return translated
        except Exception as e:
            logger.warning(f"[GPT][translate] attempt={attempt+1} failed: {e}")
            time.sleep(0.5 * (attempt + 1))

    logger.error("[GPT][translate] FAILED — returning original text")
    return text


# =========================================================
# SUBCATS REFERENCE
# =========================================================
SUBCATS_MAP = {
    "Requirements":     ["Luggage & Safety", "Special Accessibility Needs",
                         "Vaccination & Health Requirements", "Visa & Passport"],
    "Destination Info": ["Weather & Best Seasons", "Internet & Connectivity",
                         "Cultural Norms", "Gastronomy"],
    "Reservation":      ["Car Rental Booking", "Restaurant Reservation",
                         "Event Ticket Booking", "Flight Booking", "Hotel Booking",
                         "Transportation Search", "Tour/Excursion Booking"],
    "Booking Changes":  ["Car Rental Management", "Hotel Management",
                         "Flight Management", "Tour/Excursion Management"],
    "Security":         ["Emergency Contacts", "Fraud Prevention",
                         "Lost Or Stolen Items", "Insurance & Refund Policies"],
    "Claims":           ["Flight Disruption", "Overbooking",
                         "Baggage Damage", "Complaint Submission"],
    "Logistics":        ["Airport Logistics", "Check-In", "Late Check-Out"],
    "Special Requests": ["Child Services", "Pet Policy"],
    "General Information": [
        "Agency Details & Contact",
        "Chatbot Capabilities",
        "Human Handoff",
        "Feedback & Reviews",
    ],
}


# =========================================================
# LANGUE → INSTRUCTION
# =========================================================
_LANG_INSTRUCTION = {
    "query_fr":      "Réponds en français.",
    "query_ar":      "أجب باللغة العربية.",
    "query_derja_l": "أجب باللغة العربية.",
    "query_derja_a": "أجب باللغة العربية.",
    "query_en":      "Reply in English.",
}


def _lang_instr(language: str) -> str:
    return _LANG_INSTRUCTION.get(language, "Reply in English.")


# =========================================================
# ASK GEMINI CLARIFICATION
# =========================================================
def ask_gpt_clarification(
    segment: str,
    topk: list[dict],
    language: str,
    retries: int = 2
) -> dict:
    subcat_a = topk[0].get("label", "")
    subcat_b = topk[1].get("label", "") if len(topk) > 1 else ""
    lang_instr = _lang_instr(language)

    prompt = (
        f"{lang_instr}\n\n"
        f"User request: \"{segment}\"\n"
        f"Topic A: {subcat_a}\n"
        f"Topic B: {subcat_b}\n\n"
        f"Output EXACTLY 2 lines, no intro, no numbering:\n"
        f"For each question must be SHORT.\n"
        f"Line 1: user request reformulated for Topic A\n"
        f"Line 2: user request reformulated for Topic B\n\n"
        f"Example:\n"
        f"Votre question concerne la gastronomie ?\n"
        f"Votre question concerne l'hébergement ?"
    )

    phrase_a = subcat_a
    phrase_b = subcat_b

    for attempt in range(retries + 1):
        try:
            raw = _call_openai_sync(prompt, max_tokens=200)
            if not raw:
                raise ValueError("Empty response")

            lines = [l.strip() for l in raw.splitlines() if l.strip()]
            if len(lines) >= 1 and lines[0]:
                phrase_a = lines[0]
            if len(lines) >= 2 and lines[1]:
                phrase_b = lines[1]

            if phrase_a:
                break

        except Exception as e:
            logger.warning(f"[GPT][clarification] attempt {attempt+1} failed: {e}")
            time.sleep(0.3 * (attempt + 1))

    main_questions = {
        "query_fr":      "Que recherchez-vous exactement ?",
        "query_ar":      "ماذا تبحث عن بالضبط؟",
        "query_en":      "What exactly are you looking for?",
        "query_derja_l": "ماذا تبحث عن بالضبط؟",
        "query_derja_a": "ماذا تبحث عن بالضبط؟",
    }

    return {
        "question": main_questions.get(language, main_questions["query_en"]),
        "phrase_a": phrase_a,
        "phrase_b": phrase_b,
    }


def ask_gpt_correct_classification(
    segment: str,
    top1: str,
    top2: str,
    score: float,
    all_subcats: list[dict],
    language: str,
    retries: int = 2
) -> dict:
    """
    Correcteur GPT — vérifie et corrige la classification du modèle.
    
    Retourne :
    {
        "action": "accept" | "correct" | "clarify" | "reject",
        "subcat1": {"id": ..., "label": ...},   # top1 corrigé ou original
        "subcat2": {"id": ..., "label": ...},   # top2 corrigé (si clarify)
        "confidence": "high" | "low"
    }
    """
    subcats_text = "\n".join(
        f'{s["id"]}:{s["label"]}'
        for s in all_subcats
    )

    is_ambiguous = score < 0.8
    OUTPUT_FORMAT = '''
    {
    "action": "accept" | "correct" | "clarify" | "reject",
    "subcat1": {"id": 0, "label": "..."},
    "subcat2": {"id": 0, "label": "..."}
    }
    '''
    prompt = f"""You are a travel intent classifier corrector. Your job is to verify and fix the model's classification.

USER MESSAGE: "{segment}"
MODEL TOP1: "{top1}" (score: {score:.2f})
MODEL TOP2: "{top2}"
IS_AMBIGUOUS: {is_ambiguous}

AVAILABLE SUBCATEGORIES:
{subcats_text}

STEP 1 — IDENTIFY THE INTENT:
Read the message and extract:
- Action verb (book, cancel, modify, search, complain, ask...)
- Object (flight, hotel, car, restaurant, reservation, order...)
- Context (destination, date, problem type...)

STEP 2 — DECIDE:
- If intent is CLEAR and TOP1 is correct → action="accept"
- If intent is CLEAR but TOP1 is wrong → action="correct", give the right subcat1
- If intent is AMBIGUOUS (cannot determine without asking) → action="clarify", give top 2 most likely subcats
- If message is NOT travel-related → action="reject"

CLARIFY ONLY WHEN the object is missing or generic:
- Action + no object → "modifier ma réservation" (flight? hotel? car?) → clarify
- Problem + no type → "j'ai un problème" (flight? baggage? hotel?) → clarify  
- Generic term that fits multiple subcategories equally → clarify

DO NOT CLARIFY WHEN the object is explicit or the context makes it obvious:
- Explicit transport → "mon vol", "my flight", "le vol" → Flight subcategory
- Explicit accommodation → "mon hôtel", "my hotel", "la chambre" → Hotel subcategory  
- Explicit vehicle → "ma voiture", "my car", "location" → Car subcategory
- Explicit food → "restaurant", "table", "réserver à manger" → Restaurant Reservation
- Clear action → "parler à un agent" → Human Handoff
- Information request → "infos sur l'agence" → Agency Details & Contact

STEP 3 — OUTPUT:
Return ONLY valid JSON, no explanation, no markdown.

{OUTPUT_FORMAT}
"""

    for attempt in range(retries + 1):
        try:
            raw = _call_openai_sync(prompt, max_tokens=200, json_mode=False)
            raw = raw.strip()
            raw = re.sub(r"^```json\s*", "", raw)
            raw = re.sub(r"^```\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            result = json.loads(raw)

            if "action" not in result:
                raise ValueError("Missing action")
            if result["action"] not in ("accept", "correct", "clarify", "reject"):
                raise ValueError(f"Invalid action: {result['action']}")

            logger.info(f"[GPT][correct] action={result['action']} | subcat1={result.get('subcat1', {}).get('label')}")
            return result

        except Exception as e:
            logger.warning(f"[GPT][correct] attempt={attempt+1} failed: {e}")
            time.sleep(0.3 * (attempt + 1))

    # Fallback — accepter le top1 du modèle
    return {"action": "accept", "subcat1": {"id": -1, "label": top1}, "subcat2": None}
# =========================================================
# ASK GEMINI RESOLVE
# =========================================================
def ask_gpt_resolve(
    user_reply: str,
    all_subcats: list[dict],
    retries: int = 3
) -> Optional[dict]:

    subcats_text = "\n".join(
        f'{s["id"]}:{s.get("label", s["id"])}'
        for s in all_subcats
    )

    prompt = f"""You are a travel intent classifier.
Respond ONLY with valid raw JSON.

USER MESSAGE:
"{user_reply}"

SUBCATEGORIES (id:label):
{subcats_text}

RULES:
- If the message has MULTIPLE travel intents (example: hotel + flight), split them.
- If there is ONE intent, classify it.
- If not travel-related, respond ONLY with: null
- NEVER add markdown
- NEVER add explanation
- Output valid JSON only

OUTPUT FORMAT:

Single intent:
{{"segment":"...", "subcat":{{"id":0,"label":"..."}}}}

Multiple intents:
{{"segments":[
  {{"segment":"...", "subcat":{{"id":0,"label":"..."}}}},
  {{"segment":"...", "subcat":{{"id":4,"label":"..."}}}}
]}}

Not travel:
null
"""

    for attempt in range(retries + 1):
        raw = ""
        try:
            raw = _call_openai_sync(prompt, max_tokens=500, json_mode=True)

            raw = re.sub(r"^```json\s*", "", raw)
            raw = re.sub(r"^```\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            raw = raw.strip()

            logger.info(f"[GPT][resolve_full] RAW => {raw}")

            if raw.lower() == "null":
                return None

            result = json.loads(raw)

            if "segments" in result:
                if not isinstance(result["segments"], list):
                    raise ValueError("'segments' must be a list")
                for seg in result["segments"]:
                    if "segment" not in seg:
                        raise ValueError("Missing segment")
                    if "subcat" not in seg:
                        raise ValueError("Missing subcat")
                    subcat = seg["subcat"]
                    if "id" not in subcat or "label" not in subcat:
                        raise ValueError("Invalid subcat structure")

            elif "segment" in result:
                if "subcat" not in result:
                    raise ValueError("Missing subcat")
                subcat = result["subcat"]
                if "id" not in subcat or "label" not in subcat:
                    raise ValueError("Invalid subcat structure")

            else:
                raise ValueError("Unexpected JSON structure")

            logger.info(f"[GPT][resolve_full] SUCCESS attempt={attempt+1}")
            return result

        except json.JSONDecodeError as e:
            logger.warning(f"[GPT][resolve_full] JSON error attempt={attempt+1}: {e} | raw={raw}")
        except Exception as e:
            logger.warning(f"[GPT][resolve_full] attempt={attempt+1} failed: {e}")

        time.sleep(0.5 * (attempt + 1))

    logger.error("[GPT][resolve_full] FAILED after retries")
    return None