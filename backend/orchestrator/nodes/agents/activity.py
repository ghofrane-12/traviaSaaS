# activity.py
import asyncio
import json
import re
import uuid
import hashlib
from typing import Optional, Any
from datetime import datetime
from core.config import settings
from orchestrator.rag.rag_engine import RAGEngine
import httpx
import time as _time
UNSPLASH_ACCESS_KEY = settings.UNSPLASH_ACCESS_KEY


_gemini_cache: dict[str, tuple] = {}
_llm_cache: dict[str, tuple] = {}
_CACHE_TTL = 3600


def _cache_set_ttl(key: str, value, ttl: int):
    _gemini_cache[key] = (value, _time.time() - (_CACHE_TTL - ttl))

def _cache_set(key: str, value, use_llm: bool = False):
    target = _llm_cache if use_llm else _gemini_cache
    if not use_llm and len(_gemini_cache) > 200:
        now = _time.time()
        expired = [k for k, (_, ts) in _gemini_cache.items() if now - ts > _CACHE_TTL]
        for k in expired:
            del _gemini_cache[k]
    target[key] = (value, _time.time())

def _cache_get(key: str):
    for cache in (_llm_cache, _gemini_cache):
        entry = cache.get(key)
        if entry is not None:
            value, ts = entry
            if _time.time() - ts > _CACHE_TTL:
                del cache[key]
                return None
            return value
    return None


SUBCAT_DISPATCH = {
    "Tour/Excursion Booking": "_book_tour",
    "Event Ticket Booking": "_book_event",
    "Tour/Excursion Management": "_modify_tour",
}

GEO_KEYS = {
    "city", "geoid", "geo_id", "location_id", "locationid",
    "lat", "lon", "latitude", "longitude", "place_id",
    "destination", "origin", "address", "region", "country",
}


class ActivityAgent:

    def __init__(self, tenant_id: str, user_id: str = "", session_id: str = ""):
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.session_id = session_id
        self.tenant_config = {}
        self.entities = {}
        self.language = ""
        self.api_keys = {}
        self.sub_category = ""
        self.agent_type = "activity"
        self.response_type = "api"
        self.raw_text = ""
        self.subcat_config = {}

        self.rag = RAGEngine(tenant_id, self.agent_type, user_id, session_id)

    def _clean_city(self, city: str) -> str:
        if not city:
            return city
        if any('\u0600' <= c <= '\u06ff' for c in city):
            from services.redis import _translate_to_french
            return _translate_to_french(city, "query_ar")
        return city

    async def _call_openai(self, prompt: str, timeout: float = 60.0) -> str:
        async with httpx.AsyncClient(
            verify=False,
            timeout=httpx.Timeout(timeout, connect=10.0)
        ) as h:
            response = await h.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "max_tokens": 4096,
                    "temperature": 0,
                    "messages": [{"role": "user", "content": prompt}],
                }
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"] or ""

    async def run(self, entities, language, tenant_config, sub_category=None,
                tenant_id="", user_id="", session_id="", response_type="api",
                subcat_config=None, raw_text="", **kwargs) -> dict:
        self.tenant_id = tenant_id or self.tenant_id
        self.tenant_config = tenant_config
        self.user_id = user_id or self.user_id
        self.session_id = session_id or self.session_id
        self.entities = entities
        self.language = language
        self.sub_category = sub_category or ""
        self.response_type = response_type
        self.raw_text = raw_text
        self.subcat_config = subcat_config or {}

        self.api_keys = await self._load_api_keys(self.tenant_id)

        method_name = SUBCAT_DISPATCH.get(sub_category)
        if not method_name and sub_category:
            subcat_only = sub_category.split(":")[-1].strip()
            method_name = next(
                (v for k, v in SUBCAT_DISPATCH.items() if k.endswith(subcat_only))
            )
        method = getattr(self, method_name)
        return await method(entities)

    async def _resolve_rag_only(self, entities: dict) -> dict:
        locs  = entities.get("LOC", [])
        dates = entities.get("DATE", [])

        tour_city_list = entities.get("tour_city", [])
        tour_city = tour_city_list[0] if tour_city_list else ""
        city = tour_city or entities.get("destination") or (locs[0] if locs else "")
        city = self._clean_city(city)

        tour_date_list = entities.get("tour_departure_date", [])
        date = tour_date_list[0] if tour_date_list else (dates[0] if dates else "")

        orgs = entities.get("ORG", [])
        rag_query = f"activités événements {city} attractions visites"
        if orgs:
            rag_query += f" {orgs[0]}"

        rag_result = await self.rag.search_with_subcategory(
            query=rag_query,
            sub_category=self.sub_category,
            k=10,
            filter_by_loc=None
        )

        if rag_result and rag_result.get("context"):
            activities_list = await self._gemini_parse_rag_context(
                rag_result["context"], city, date
            )

            if not isinstance(activities_list, list):
                activities_list = []
            for activity in activities_list:
                if not activity.get("city") or len(activity.get("city", "")) <= 2:
                    activity["city"] = city

            return {
                    "status": "rag_only",
                    "source": "RAG+GPT-4o-mini",
                    "type": "activity_search_results",
                    "results": activities_list,
                    "activities": activities_list,
                    "user_id": self.user_id,
                    "session_id": self.session_id
                }

        return {
            "status": "rag_only",
            "type": "info_results",
            "message": f"Aucune information trouvée{f' pour {city}' if city else ''}",
            "results": [],
            "user_id": self.user_id,
            "session_id": self.session_id
        }

    async def _gemini_parse_rag_context(self, rag_text: str, city: str, travel_date: str) -> list:
        sc = self.sub_category.lower()
        if any(k in sc for k in ("tour", "excursion", "attraction")):
            item_type = "tour"
            date_note = "date peut être vide pour les tours"
        else:
            item_type = "event"
            date_note = "date obligatoire pour les événements"

        rag_hash  = hashlib.sha256(rag_text[:6000].encode()).hexdigest()[:16]
        cache_key = f"rag_activity_{item_type}_{city}_{rag_hash}"

        cached = _cache_get(cache_key)
        if cached is not None:
            for activity in cached:
                if not activity.get("city") or len(activity.get("city", "")) <= 2:
                    activity["city"] = city
            return cached

        activity_format = """{
        "activities": [
            {
            "id": "identifiant exact du texte ou vide",
            "name": "nom exact trouvé",
            "type": "activity",
            "description": "description trouvée ou vide",
            "image": "premiere url image ou vide",
            "images": ["url1", "url2"],
            "date": "YYYY-MM-DD ou vide",
            "time": "HH:MM ou vide",
            "venue": "lieu de rendez-vous ou vide",
            "address": "adresse ou vide",
            "city": "CITY_PLACEHOLDER",
            "category": "categorie ou vide",
            "duration": "duree ou vide",
            "price_min": 0.0,
            "price_max": 0.0,
            "currency": "devise si connue sinon vide",
            "booking_url": "url ou vide",
            "languages": [],
            "whats_included": [],
            "whats_not_included": [],
            "highlights": [],
            "meeting_point": "lieu_rdv ou vide",
            "cancellation_policy": "",
            "accessibility_info": "",
            "artists": [],
            "actions": [
                {"label": "Reserver", "action": "book", "url": ""},
                {"label": "Voir details", "action": "details", "url": ""}
            ]
            }
        ]
        }""".replace("CITY_PLACEHOLDER", city)

        prompt = f"""Tu reçois un texte brut issu d'une base de données d'activités.
Le texte peut utiliser n'importe quelle langue ou structure.

MISSION : Extraire UNIQUEMENT les {item_type}s situés à : {city}
Note importante : "{city}" peut apparaître sous différentes formes (variantes orthographiques, noms arabes, noms locaux, régions proches). Inclus toute activité dont la ville, région ou lieu ressemble à "{city}".
Période : {travel_date if travel_date else "toute date"}
Note : {date_note}

RÈGLES CRITIQUES :
1. Si aucun {item_type} ne correspond à "{city}" ou une variante proche → retourne une liste vide
2. Ne retourne PAS deux activités avec le même nom ET la même heure
3. N'invente AUCUNE valeur
4. Cherche sous tous les noms : nom/name/titre, ville/city, duree/duration,
   prix/price/tarif, lieu/venue/lieu_rdv, horaire/time/heure,
   inclus/included, points_forts/highlights, images/image

EXTRACTION DES PRIX :
- Si prix est un objet avec adulte/enfant → price_min=enfant, price_max=adulte
- Si prix est un nombre → price_min=prix, price_max=prix*1.5
- Si prix absent → price_min=0, price_max=0

EXTRACTION DES IMAGES :
- N'invente AUCUN url d'image
- Cherche sous : image, images, photo, photos, img, picture
- Si c'est une liste → copie toutes les URLs dans "images" ET mets la première dans "image"
- Si c'est une string → mets dans "image" ET dans "images": [url]

EXTRACTION DU LIEU :
- Cherche sous : venue, lieu, lieu_rdv, place, location, endroit, rdv
- Copie la valeur dans "venue" ET dans "meeting_point"

EXTRACTION DE LA CATÉGORIE :
- Cherche sous : category, categorie, type, genre, kind

RÉPONDS UNIQUEMENT avec ce JSON (sans texte avant ou après, sans backticks) :
{activity_format}

TEXTE SOURCE :
{rag_text[:6000]}"""

        try:
            raw = await self._call_openai(prompt, timeout=60.0)

            if not raw or not raw.strip():
                return []

            try:
                mapping = json.loads(raw)
            except json.JSONDecodeError:
                return []

            activities_raw = mapping.get("activities", [])
            if not isinstance(activities_raw, list):
                return []

            seen = set()
            deduplicated = []
            for activity in activities_raw:
                if not isinstance(activity, dict):
                    continue
                name      = (activity.get("name") or "").strip().lower()
                time_val  = (activity.get("time") or "").strip()
                venue     = (activity.get("venue") or "").strip().lower()
                strict_key = (name, time_val)
                soft_key   = (name, venue)
                if strict_key in seen or soft_key in seen:
                    continue
                seen.add(strict_key)
                seen.add(soft_key)
                deduplicated.append(activity)

            for activity in deduplicated:
                activity["city"] = city if not activity.get("city") or len(activity.get("city", "")) <= 2 else activity["city"]

            for activity in deduplicated:
                act_id = (activity.get("id") or "").strip()
                if act_id:
                    _cache_set_ttl(f"rag_detail:{act_id}", activity, ttl=86400)

            _cache_set(cache_key, deduplicated)
            return deduplicated

        except asyncio.TimeoutError:
            return []
        except Exception as e:
            print(f"[RAGActivityParser] ❌ {e}")
            return []

    def _format_rag_activity_as_details(self, activity: dict) -> dict:
        def _s(v, d=""):
            return str(v).strip() if v is not None else d

        def _f(v):
            try:    return float(str(v).replace(",", "."))
            except: return 0.0

        item_id     = _s(activity.get("id"))
        booking_url = _s(activity.get("booking_url"))
        price_min   = _f(activity.get("price_min"))
        price_max   = _f(activity.get("price_max")) or (price_min * 1.5 if price_min else 0.0)
        currency    = _s(activity.get("currency"), "TND")
        has_date    = bool(activity.get("date") and activity.get("date") != "")
        has_time    = bool(activity.get("time") and activity.get("time") != "")

        seen_urls = set()
        images    = []

        def _add_image(url):
            url = _s(url)
            if url and url.startswith("http") and url not in seen_urls:
                seen_urls.add(url)
                images.append({
                    "ratio": "16_9", "url": url,
                    "width": 1024, "height": 576, "fallback": False
                })

        _add_image(activity.get("image"))
        for img in (activity.get("images") or []):
            if isinstance(img, dict):
                _add_image(img.get("url"))
            elif isinstance(img, str):
                _add_image(img)

        venue_name = _s(
            activity.get("venue") or
            activity.get("meeting_point") or
            activity.get("lieu_rdv") or
            activity.get("place") or
            activity.get("address") or ""
        )

        category = _s(
            activity.get("category") or
            activity.get("type_activite") or
            activity.get("genre") or ""
        )

        whats_included     = activity.get("whats_included")     or []
        whats_not_included = activity.get("whats_not_included") or []
        highlights         = activity.get("highlights")         or []
        languages          = activity.get("languages")          or ["Français"]

        return {
            "id":     item_id,
            "name":   _s(activity.get("name"), "Activité"),
            "type":   "activity",
            "locale": "fr-fr",
            "url":    booking_url,
            "status": "success",
            "source": "rag",
            "description":         _s(activity.get("description")),
            "additionalInfo":      _s(activity.get("description")),
            "description_long":    _s(activity.get("description")),
            "info":                "\n".join(whats_included) if whats_included else "",
            "pleaseNote":          "",
            "highlights":          highlights,
            "whats_included":      whats_included,
            "whats_not_included":  whats_not_included,
            "meeting_point":       venue_name,
            "cancellation_policy": _s(activity.get("cancellation_policy")),
            "accessibility_info":  _s(activity.get("accessibility_info")),
            "duration":            _s(activity.get("duration")),
            "languages":           languages,
            "artists":             activity.get("artists") or [],
            "images": images,
            "dates": {
                "start": {
                    "localDate":      _s(activity.get("date")),
                    "localTime":      _s(activity.get("time")),
                    "dateTime":       None,
                    "dateTBD":        not has_date,
                    "dateTBA":        not has_date,
                    "timeTBA":        not has_time,
                    "noSpecificTime": False,
                },
                "timezone":         "Europe/Paris",
                "spanMultipleDays": False,
            },
            "priceRanges": [{
                "type":     "standard",
                "currency": currency,
                "min":      price_min,
                "max":      price_max,
            }],
            "sales": {"public": {
                "startDateTime": None, "endDateTime": None,
                "startTBD": False,     "startTBA": False,
            }},
            "venue": {
                "name":       venue_name,
                "address":    _s(activity.get("address")),
                "city":       _s(activity.get("city")),
                "state":      None, "country": None,
                "postalCode": None, "location": None,
            },
            "classifications": [{
                "primary": True,
                "segment": {"name": category or "Activité"},
                "genre":   {"name": category or ""},
            }],
            "actions": activity.get("actions") or [
                {"label": "Réserver",     "action": "book",    "url": booking_url},
                {"label": "Voir détails", "action": "details", "url": f"/activity/{item_id}"},
            ],
            "category":    category,
            "subcategory": "",
            "promoter": {}, "promoters": [], "seatmap": {},
            "accessibility": {}, "ticketLimit": {},
            "_links": {}, "_embedded": {"venues": [], "attractions": []},
        }

    async def _resolve_llm_only(self, entities: dict, prompt_override: str = "") -> dict:
        locs   = entities.get("LOC", [])
        dates  = entities.get("DATE", [])
        city   = locs[0] if locs else ""
        user_query      = self.raw_text or ""
        target_currency = self.tenant_config.get("currency", "EUR")

        override = prompt_override or self.subcat_config.get("llm_prompt_override", "")

        if override:
            query = override
            replacements = {
                "{city}":       city or "non spécifié",
                "{user_query}": user_query,
                "{language}":   self.language or "fr",
                "{currency}":   target_currency,
                "{date}":       dates[0] if dates else "non spécifié",
            }
            for key, value in replacements.items():
                query = query.replace(key, value)

            try:
                raw = await self._call_openai(query, timeout=60.0)

                for fence in ["```json", "```"]:
                    raw = raw.replace(fence, "")
                raw_result = json.loads(raw.strip())

                return {
                    "status":     "llm_only",
                    "source":     "GPT-4o-mini Custom",
                    "type":       raw_result.get("type", "activity_search_results"),
                    "query":      raw_result.get("query", {"city": city} if city else {}),
                    "results":    raw_result.get("results", []),
                    "activities": raw_result.get("results", raw_result.get("activities", [])),
                    "message":    raw_result.get("message", ""),
                    "sections":   raw_result.get("sections", []),
                    "user_id":    self.user_id,
                    "session_id": self.session_id,
                }
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[ActivityAgent] ❌ Prompt custom échoué: {e} → fallback GPT")

        activities = await self._openai_fallback(
            query=user_query or f"activités à {city}",
            result_key="activities",
            entities=entities
        )

        if isinstance(activities, list):
            return {
                "status":     "llm_only",
                "source":     "GPT-4o-mini",
                "type":       "activity_search_results",
                "query":      {"city": city} if city else {},
                "results":    activities,
                "activities": activities,
                "user_id":    self.user_id,
                "session_id": self.session_id,
            }

        return {
            "status":     "llm_only",
            "source":     "GPT-4o-mini",
            "type":       "activity_search_results",
            "query":      {"city": city} if city else {},
            "results":    [],
            "activities": [],
            "user_id":    self.user_id,
            "session_id": self.session_id,
        }

    async def _resolve_human_only(self, contact: str = "") -> dict:
        lang = self.language or "query_fr"

        messages = {
            "query_fr":      "Cette demande nécessite l'intervention d'un conseiller.",
            "query_ar":      "هذا الطلب يتطلب تدخل مستشار.",
            "query_derja_a": "هذا الطلب يحتاج مستشار.",
            "query_derja_l": "Hadha talba yehtaj moustacher.",
            "query_en":      "This request requires a consultant.",
        }
        titles = {
            "query_fr":      "Contacter un conseiller",
            "query_ar":      "تواصل مع مستشار",
            "query_derja_a": "تواصل مع مستشار",
            "query_derja_l": "Contactez un conseiller",
            "query_en":      "Contact a consultant",
        }

        return {
            "status": "human_required",
            "type": "info_results",
            "message": messages.get(lang, messages["query_fr"]),
            "sections": [{
                "title":   titles.get(lang, titles["query_fr"]),
                "content": f"Pour {self.sub_category}, veuillez contacter notre équipe.",
                "items":   [f"📞 {contact}" if contact else "📧 Contactez votre agence"]
            }],
            "actions": [{"label": "Contacter", "action": "contact", "contact": contact}]
        }

    async def _resolve_api(self, api_payload: dict, rag_query: str,
                            steps: list = None, result_key: str = "activities",
                            expected_method: str = None) -> dict:
        steps = steps or []
        target_keys = {}

        possible_keys = [
            self.sub_category,
            f"Search:{self.sub_category}" if self.sub_category and not self.sub_category.startswith("Search:") else None,
            self.sub_category.replace("Search:", "") if self.sub_category and self.sub_category.startswith("Search:") else None,
        ]
        possible_keys = [k for k in possible_keys if k]

        found_key = None
        for key in possible_keys:
            if key in self.api_keys:
                found_key = key
                break

        if found_key:
            target_keys = {found_key: self.api_keys[found_key]}
        else:
            target_keys = {
                label: creds for label, creds in self.api_keys.items()
                if not expected_method or creds.get("http_method", "GET").upper() == expected_method.upper()
            }

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            for label, creds in target_keys.items():
                try:
                    method = creds.get("http_method", "GET").upper()

                    template         = creds.get("payload_template") or {}
                    headers_template = creds.get("headers_template") or {}
                    result_path      = creds.get("result_path") or result_key
                    param_mapping    = creds.get("param_mapping")
                    timeout_ms       = creds.get("timeout_ms", 15000)
                    retry_count      = creds.get("retry_count", 1)

                    if isinstance(template, str):
                        try:    template = json.loads(template)
                        except: template = {}
                    if isinstance(headers_template, str):
                        try:    headers_template = json.loads(headers_template)
                        except: headers_template = {}

                    context_values = {
                        "user_id":    self.user_id,
                        "session_id": self.session_id,
                        "tenant_id":  self.tenant_id,
                    }

                    dynamic_values = self._map_entities(
                        self.entities, param_mapping, api_payload, context=context_values
                    )

                    fill_values = {
                        "api_key": creds.get("key"),
                        **context_values,
                        **api_payload,
                        **dynamic_values
                    }

                    url = creds.get("url", "")
                    for k, v in fill_values.items():
                        if v is not None:
                            url = url.replace(f"{{{k}}}", str(v))

                    payload = self._fill_template(template, fill_values)
                    headers = self._fill_template(headers_template, fill_values) or {}

                    def _clean(p):
                        if not p:
                            return {}
                        return {k: v for k, v in p.items()
                                if v is not None and not isinstance(v, (list, dict))}

                    response   = None
                    last_error = None
                    timeout_sec = timeout_ms / 1000

                    for attempt in range(retry_count):
                        try:
                            if method == "GET":
                                response = await client.get(url, params=_clean(payload), headers=headers, timeout=timeout_sec)
                            elif method == "POST":
                                response = await client.post(url, json=payload, headers=headers, timeout=timeout_sec)
                            elif method == "PUT":
                                response = await client.put(url, json=payload, headers=headers, timeout=timeout_sec)
                            elif method == "DELETE":
                                response = await client.delete(url, params=_clean(payload), headers=headers, timeout=timeout_sec)
                            else:
                                break

                            if response.status_code < 400:
                                break
                            last_error = f"HTTP {response.status_code}"
                        except Exception as e:
                            last_error = str(e)

                        if attempt < retry_count - 1:
                            await asyncio.sleep(min(2 ** attempt, 10))

                    if not response or response.status_code >= 400:
                        continue

                    try:
                        data = response.json()
                    except Exception:
                        continue

                    result_data = self._extract_result(data, result_path)

                    if data:
                        parsed = await self._gemini_parse_response(data, api_payload)

                        if parsed:
                            return {
                                "status":     "success",
                                "source":     label,
                                "type":       "activity_search_results",
                                "results":    parsed,
                                "activities": parsed,
                                "user_id":    self.user_id,
                                "session_id": self.session_id,
                                "steps":      steps,
                            }

                        if result_data and isinstance(result_data, list) and result_data:
                            enriched = self._enrich_activities(result_data, self.sub_category)
                            if enriched:
                                return {
                                    "status":     "success",
                                    "source":     label,
                                    "type":       "activity_search_results",
                                    "results":    enriched[:20],
                                    "activities": enriched[:20],
                                    "user_id":    self.user_id,
                                    "session_id": self.session_id,
                                    "steps":      steps,
                                }

                except Exception as e:
                    print(f"[ActivityAgent] ❌ {label}: {e}")
                    continue

        return {
            "status":  "api_error",
            "type":    "info_results",
            "message": "Aucune activité trouvée pour votre recherche.",
            "sections": [{
                "title":   "Aucun résultat",
                "content": "Nous n'avons pas trouvé d'activités pour ces critères.",
                "items": [
                    "Essayez des dates différentes",
                    "Élargissez votre recherche",
                    "Vérifiez le nom de la ville"
                ]
            }],
            "actions":    [{"label": "Modifier la recherche", "action": "retry"}],
            "user_id":    self.user_id,
            "session_id": self.session_id,
            "steps":      steps,
        }

    async def _gemini_parse_response(self, raw_data: dict, api_payload: dict) -> list:
        city = api_payload.get("city", "")

        sc = self.sub_category.lower()
        if any(k in sc for k in ("tour", "tour/excursion", "excursion", "attraction")):
            item_type   = "tour"
            search_keys = "_embedded.attractions, results, tours, activities, items"
            date_note   = "Les tours n'ont pas toujours de date fixe — laisser '' si absent"
        else:
            item_type   = "event"
            search_keys = "_embedded.events, results, events, activities, items"
            date_note   = "Les événements ont toujours une date précise — extraire localDate"

        result_hash = hashlib.sha256(
            json.dumps(raw_data, ensure_ascii=False, sort_keys=True)[:3000].encode()
        ).hexdigest()[:20]
        cache_key = f"parse_result:{item_type}:{result_hash}"

        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        full_json = json.dumps(raw_data, ensure_ascii=False)
        if len(full_json) > 10000:
            full_json = full_json[:10000] + "..."

        prompt = f"""Tu es un expert en APIs d'activités et événements.

RÉPONSE API COMPLÈTE :
{full_json}

CONTEXTE :
- Ville : "{city}"
- Type d'item recherché : {item_type}
- Note dates : {date_note}

MISSION : extrais TOUS les {item_type}s depuis les clés : {search_keys}
Retourne ce JSON (sans backticks) :
{{
"items": [
    {{
    "id": "identifiant unique",
    "name": "nom",
    "type": "activity",
    "description": "description courte ou ''",
    "image": "url image principale ou ''",
    "date": "YYYY-MM-DD ou ''",
    "time": "HH:MM:SS ou ''",
    "venue": "nom du lieu ou ''",
    "city": "{city}",
    "address": "adresse ou ''",
    "category": "catégorie ou ''",
    "status": "onsale ou ''",
    "on_sale": true,
    "booking_url": "url ou ''",
    "price_min": 0.0,
    "price_max": 0.0,
    "currency": "devise ou ''",
    "duration": "durée ou ''",
    "actions": [
        {{"label": "Réserver", "action": "book", "url": ""}},
        {{"label": "Voir détails", "action": "details", "url": ""}}
    ]
    }}
]
}}

RÈGLES :
- N'invente rien — uniquement les valeurs présentes dans les données
- price_min et price_max en float (cherche sous priceRanges, price, min, max...)
- Pour les tours : date peut être vide si non spécifiée
- Pour les événements : date est obligatoire (cherche sous dates.start.localDate)
- Max 5 items"""

        try:
            raw = await self._call_openai(prompt, timeout=60.0)

            start = raw.find("{")
            end   = raw.rfind("}")
            if start != -1 and end != -1:
                raw = raw[start:end + 1]

            mapping       = json.loads(raw)
            all_items_raw = mapping.get("items", [])

        except asyncio.TimeoutError:
            return []
        except Exception as e:
            print(f"[ActivityParser] ❌ {e}")
            return []

        def _safe_str(v, default="") -> str:
            return str(v).strip() if v is not None else default

        def _safe_float(v, default=0.0) -> float:
            try:    return float(str(v).replace(",", "."))
            except: return default

        def _safe_bool(v, default=True) -> bool:
            if isinstance(v, bool): return v
            if isinstance(v, str):  return v.lower() in ("true", "1", "yes", "onsale")
            return default

        today   = datetime.now().strftime("%Y-%m-%d")
        results = []

        for i, item in enumerate(all_items_raw):
            if not isinstance(item, dict):
                continue

            item_id   = _safe_str(item.get("id"), f"act_{i+1:03d}")
            item_date = _safe_str(item.get("date"))
            if item_date and item_date < today:
                from datetime import timedelta
                item_date = (datetime.now() + timedelta(days=30 + i * 7)).strftime("%Y-%m-%d")

            price_min = _safe_float(item.get("price_min"))
            price_max = _safe_float(item.get("price_max"))
            if price_max == 0.0 and price_min > 0:
                price_max = price_min * 1.5

            booking_url = _safe_str(item.get("booking_url"))
            actions = item.get("actions") or [
                {"label": "Réserver",     "action": "book",    "url": booking_url},
                {"label": "Voir détails", "action": "details", "url": f"/activity/{item_id}"}
            ]

            results.append({
                "id":           item_id,
                "type":         "activity",
                "name":         _safe_str(item.get("name"), "Activité"),
                "description":  _safe_str(item.get("description")),
                "image":        _safe_str(item.get("image")),
                "date":         item_date,
                "time":         _safe_str(item.get("time")),
                "venue":        _safe_str(item.get("venue")),
                "city":         _safe_str(item.get("city") or city),
                "address":      _safe_str(item.get("address")),
                "category":     _safe_str(item.get("category"), "Activité"),
                "status":       _safe_str(item.get("status"), "onsale"),
                "on_sale":      _safe_bool(item.get("on_sale")),
                "booking_url":  booking_url,
                "price_min":    price_min,
                "price_max":    price_max,
                "currency":     _safe_str(item.get("currency"), ""),
                "duration":     _safe_str(item.get("duration")),
                "sub_category": self.sub_category,
                "actions":      actions,
            })

        _cache_set(cache_key, results)
        return results

    def _build_tour_search_form(self, missing_fields: list) -> dict:
        missing_names = [f.get("field") for f in missing_fields]

        field_templates = {
            "city": {
                "name":        "city",
                "label":       "Destination",
                "type":        "text",
                "required":    True,
                "placeholder": "Où souhaitez-vous faire des activités ?",
                "value":       None,
            },
            "departure_date": {
                "name":        "departure_date",
                "label":       "Date souhaitée",
                "type":        "date",
                "required":    False,
                "placeholder": "jj/mm/aaaa",
                "format":      "YYYY-MM-DD",
            },
        }

        fields = []
        for field in missing_fields:
            field_name = field.get("field")
            if field_name in field_templates:
                fields.append(field_templates[field_name])

        fields.append(field_templates["departure_date"])

        return {
            "type":        "form",
            "title":       "Recherche d'activités",
            "description": "Indiquez votre destination pour découvrir les activités disponibles.",
            "fields":      fields,
        }

    async def _load_api_keys(self, tenant_id: str) -> dict:
        try:
            from core.database import get_pool
            pool = await get_pool()
            rows = await pool.fetch(
                """
                SELECT label, api_key, api_url, http_method,
                       payload_template, headers_template, result_path,
                       param_mapping
                FROM tenant_api_keys
                WHERE tenant_id = $1
                  AND is_active = TRUE
                  AND agent_type = $2
                ORDER BY priority ASC
                """,
                uuid.UUID(tenant_id),
                self.agent_type,
            )
            return {
                r["label"]: {
                    "key": r["api_key"],
                    "url": r["api_url"],
                    "http_method": r["http_method"],
                    "payload_template": r["payload_template"],
                    "headers_template": r["headers_template"],
                    "result_path": r["result_path"],
                    "param_mapping": r["param_mapping"],
                }
                for r in rows
            }
        except Exception as e:
            print(f"[ActivityAgent] ❌ Erreur chargement API keys: {e}")
            return {}

    def _fill_template(self, template: Any, values: dict) -> Any:
        if isinstance(template, dict):
            return {k: v for k, v in
                    ((k, self._fill_template(v, values)) for k, v in template.items())
                    if v is not None}
        if isinstance(template, list):
            return [self._fill_template(i, values) for i in template]
        if isinstance(template, str):
            stripped = template.strip()
            if stripped.startswith("{") and stripped.endswith("}") and stripped.count("{") == 1:
                var = stripped[1:-1]
                return values.get(var)
            result = template
            for k, v in values.items():
                if v is not None:
                    result = result.replace(f"{{{k}}}", str(v))
            return None if re.search(r"\{[^}]+\}", result) else result
        return template

    def _extract_result(self, data: Any, result_path: str) -> Any:
        if not result_path or not data:
            return data
        path = result_path.strip().lstrip("$").lstrip(".")
        if not path:
            return data
        current = data
        for key in path.split("."):
            if not key:
                continue
            if isinstance(current, dict):
                current = current.get(key)
            elif isinstance(current, list) and key.isdigit():
                idx = int(key)
                current = current[idx] if idx < len(current) else None
            else:
                return None
            if current is None:
                return None
        return current

    def _apply_transform(self, value: str, transform: str) -> str:
        import unicodedata
        if transform == "upper":
            return value.upper()
        if transform == "lower":
            return value.lower()
        if transform == "upper3":
            return value[:3].upper()
        if transform == "lower2":
            return value[:2].lower()
        if transform == "slug":
            return value.lower().replace(" ", "-")
        if transform == "nospace":
            return value.replace(" ", "")
        if transform == "first_word":
            return value.split()[0] if value.split() else value
        if transform == "ascii_lower":
            n = unicodedata.normalize("NFD", value)
            return "".join(c for c in n if unicodedata.category(c) != "Mn").lower()
        if transform.startswith("max"):
            try:
                return value[:int(transform[3:])]
            except:
                return value
        return value

    def _map_entities(self, entities, param_mapping, api_payload=None, context=None) -> dict:
        result = {}
        if not param_mapping:
            return result
        if isinstance(param_mapping, str):
            try:
                param_mapping = json.loads(param_mapping)
            except:
                return result

        entity_lists = {k: entities.get(k, [])
                        for k in ("LOC", "DATE", "PRICE", "ORG", "FLIGHT")}

        for entity_key, mapping_val in param_mapping.items():
            if isinstance(mapping_val, dict):
                api_param = mapping_val.get("param")
                mapping_type = mapping_val.get("type", "NER")
                transform = mapping_val.get("transform")
            else:
                api_param = mapping_val
                mapping_type = "NER"
                transform = None

            if not api_param:
                continue

            if mapping_type == "API_SPECIFIC":
                val = (context or {}).get(api_param) or (api_payload or {}).get(api_param)
                if val is not None:
                    result[api_param] = val
                continue

            m = re.match(r'^(\w+)(?:\[(\d+)\])?(?:\.(\w+))?$', entity_key)
            if not m:
                continue

            etype = m.group(1).upper()
            index = int(m.group(2)) if m.group(2) else None
            field = m.group(3)
            values = entity_lists.get(etype, [])
            value = None

            if field == "count":
                value = len(values)
            elif field == "join":
                value = ", ".join(values)
            elif index is not None and index < len(values):
                val = values[index]
                if field == "first":
                    value = val.split()[0] if isinstance(val, str) else val
                elif field == "last":
                    value = val.split()[-1] if isinstance(val, str) else val
                else:
                    value = val

            if value is None:
                continue
            if transform:
                value = self._apply_transform(str(value), transform)
            result[api_param] = value

        return result

    def _validate_payload(self, label: str, api_payload: dict,
                           expected_method: str = None, creds: dict = None) -> bool:
        method = (expected_method or "GET").upper()

        template = creds.get("payload_template", {}) if creds else {}
        param_mapping = creds.get("param_mapping", {}) if creds else {}

        for attr in (template, param_mapping):
            if isinstance(attr, str):
                try:
                    attr = json.loads(attr)
                except:
                    attr = {}

        def _get_param_name(v):
            return v.get("param", "") if isinstance(v, dict) else str(v)

        api_keys_lower = {k.lower() for k in (template if isinstance(template, dict) else {}).keys()} | \
                         {_get_param_name(v).lower() for v in (param_mapping if isinstance(param_mapping, dict) else {}).values()}

        if not (api_keys_lower & GEO_KEYS):
            return True

        city = str(api_payload.get("city") or "").strip()
        geoid = str(api_payload.get("geoId") or "").strip()

        if method == "GET":
            if not city and not geoid:
                return False
        elif method == "POST":
            if not geoid:
                return False

        return True

    def _convert_to_ticketmaster_format(self, gemini_result: dict, context: dict) -> dict:
        activities = gemini_result.get("activities", [])
        events = []

        for i, activity in enumerate(activities):
            events.append({
                "id": f"gemini_{i+1:03d}",
                "name": activity.get("name", "Activité"),
                "type": "event",
                "url": activity.get("booking_url", ""),
                "locale": "fr-fr",
                "images": [
                    {
                        "ratio": "16_9",
                        "url": activity.get("image", ""),
                        "width": 1024,
                        "height": 576,
                        "fallback": True
                    }
                ] if activity.get("image") else [],
                "dates": {
                    "start": {
                        "localDate": context.get("date", "2026-06-01"),
                        "localTime": "14:00:00",
                        "dateTBD": False,
                        "dateTBA": False,
                        "timeTBA": False,
                        "noSpecificTime": False
                    },
                    "timezone": "Europe/Paris",
                    "status": {"code": "onsale"},
                    "spanMultipleDays": False
                },
                "classifications": [
                    {
                        "primary": True,
                        "segment": {"name": activity.get("type", "Activity")},
                        "genre": {"name": activity.get("type", "Activity")}
                    }
                ],
                "priceRanges": self._extract_price_range(activity.get("price", "")),
                "sales": {
                    "public": {
                        "startDateTime": "2026-01-01T00:00:00Z",
                        "endDateTime": "2026-12-31T23:59:59Z"
                    }
                },
                "_embedded": {
                    "venues": [
                        {
                            "name": activity.get("location", context.get("city", "Paris")),
                            "city": {"name": context.get("city", "Paris")},
                            "country": {"name": "France", "countryCode": "FR"}
                        }
                    ],
                    "attractions": []
                }
            })

        return {
            "_embedded": {"events": events},
            "page": {
                "size": 20,
                "totalElements": len(events),
                "totalPages": 1,
                "number": 0
            }
        }

    def _extract_price_range(self, price_str: str) -> list:
        import re
        if not price_str:
            return []
        numbers = re.findall(r'\d+(?:[.,]\d+)?', price_str)
        if numbers:
            min_price = float(numbers[0].replace(',', '.'))
            max_price = float(numbers[-1].replace(',', '.')) if len(numbers) > 1 else min_price * 1.5
            return [{
                "type": "standard",
                "currency": "EUR",
                "min": min_price,
                "max": max_price
            }]
        return []

    async def _openai_fallback(self, query: str, result_key: str = "activities",
                                steps: list = None, entities: dict = None) -> dict:
        cache_key = hashlib.sha256(query.encode()).hexdigest()[:16]
        if cache_key in _gemini_cache:
            return _gemini_cache[cache_key]

        context = self._build_gemini_context(entities or {})

        PROMPTS = {
            "activities": {
                "schema": [
                    {
                        "id": "act_001",
                        "name": "Nom de l'activité",
                        "type": "activity",
                        "description": "Description courte de l'activité",
                        "description_long": "Description détaillée et complète de l'activité",
                        "image": "",
                        "date": "2026-05-15",
                        "time": "14:00:00",
                        "venue": "Nom du lieu",
                        "city": "Nom de la ville",
                        "address": "Adresse complète du lieu",
                        "category": "Type d'activité (Concert, Musée, Visite, Sport, Spectacle, Gastronomie, Nature)",
                        "status": "onsale",
                        "on_sale": True,
                        "booking_url": "https://example.com/reserver",
                        "price_min": 25.0,
                        "price_max": 85.0,
                        "currency": "EUR",
                        "duration": "2 heures",
                        "languages": ["Français", "Anglais"],
                        "artists": ["Nom de l'artiste ou du guide"],
                        "important_notes": ["Note importante 1", "Note importante 2"],
                        "accessibility": "Accessible aux personnes à mobilité réduite",
                        "cancellation_policy": "Annulation gratuite jusqu'à 24h avant",
                        "meeting_point": "Point de rendez-vous exact",
                        "whats_included": ["Inclusion 1", "Inclusion 2"],
                        "whats_not_included": ["Non inclus 1", "Non inclus 2"],
                        "actions": [
                            {"label": "Réserver", "action": "book", "url": "https://example.com/reserver"},
                            {"label": "Voir détails", "action": "details", "url": "/activity/act_001"}
                        ]
                    }
                ],
                "instruction": "Génère une liste d'activités pertinentes avec tous les détails"
            },
            "result": {
                "schema": {
                    "status": "success",
                    "message": "Message de confirmation",
                    "booking_reference": "REF123456",
                    "next_steps": ["Étape 1", "Étape 2"],
                    "advice": "Conseil important"
                },
                "instruction": "Génère un résultat d'opération"
            }
        }

        template = PROMPTS.get(result_key, PROMPTS["activities"])

        prompt = f"""Tu es un assistant voyage expert spécialisé dans les activités, événements et visites.
        📅 AUJOURD'HUI: {datetime.now().strftime('%Y-%m-%d')}
        🚨 RÈGLE ABSOLUE: TOUTES LES DATES DOIVENT ÊTRE POSTÉRIEURES À AUJOURD'HUI ({datetime.now().strftime('%Y-%m-%d')})

    CONTEXTE:
    - Ville: {context.get('city', 'Non spécifiée')}
    - Date: {context.get('date', 'Non spécifiée')}
    - Date de fin: {context.get('end_date', 'Non spécifiée')}
    - Budget: {context.get('budget', 'Non spécifié')}
    - Participants: {context.get('participants', 'Non spécifié')}

    INSTRUCTION: {template['instruction']}

    SCHÉMA JSON À RESPECTER STRICTEMENT:
    {json.dumps(template['schema'], ensure_ascii=False, indent=2)}

    REQUÊTE UTILISATEUR: {query}

    RÈGLES IMPORTANTES:
    1. Réponds UNIQUEMENT en JSON valide - AUCUN texte avant ou après
    2. Retourne UNIQUEMENT un tableau d'activités (liste)
    3. Génère des activités RÉELLES et PLAUSIBLES pour la ville donnée
    4. Retourne au moins 3 activités si possible
    5. Pour les images, utilise des URLs d'images spécifiques disponibles sur internet (Unsplash, Pexels, etc.)
    6. 🔥 IMPORTANT: Les dates DOIVENT être dans le FUTUR par rapport à aujourd'hui ({datetime.now().strftime('%Y-%m-%d')})
    7. Si l'utilisateur a fourni une date, utilise-la. Sinon, génère des dates entre 1 et 6 mois dans le futur
    8. Les prix doivent être réalistes pour la destination
    9. Les durées doivent être adaptées à l'activité
    10. Les langues doivent être adaptées à la destination
    11. Remplis TOUS les champs avec des informations pertinentes et réalistes
    12. Ne retourne PAS de structure _embedded ou page

    RÉPONSE JSON:"""

        try:
            raw = await self._call_openai(prompt, timeout=60.0)
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = json.loads(raw.strip())
            today = datetime.now().strftime("%Y-%m-%d")

            if isinstance(result, list):
                for i, activity in enumerate(result):
                    if not activity.get("date") or activity.get("date", "") < today:
                        from datetime import timedelta
                        activity["date"] = (datetime.now() + timedelta(days=30 + i * 7)).strftime("%Y-%m-%d")
                result = await self._enrich_activities_images(result)
                for i, activity in enumerate(result):
                    activity = self._enrich_activity_with_defaults(activity, context, i)
                _cache_set(cache_key, result)
                return result
            elif isinstance(result, dict) and "activities" in result:
                activities = result["activities"]
                for i, activity in enumerate(activities):
                    activity = self._enrich_activity_with_defaults(activity, context, i)
                _cache_set(cache_key, activities)
                return activities
            else:
                _cache_set(cache_key, [])
                return []

        except Exception as e:
            print(f"[GPT Activity] ❌ {e}")
            return self._get_generic_activities_with_details(context)

    async def _enrich_activities_images(self, activities: list) -> list:
        async def fetch_one(activity: dict) -> dict:
            query = self._build_image_query(activity)
            if query:
                image_url = await self._fetch_unsplash_image(query)
                if image_url:
                    activity["image"] = image_url
                    activity["images"] = [{
                        "ratio": "16_9",
                        "url": image_url,
                        "width": 1024,
                        "height": 576,
                        "fallback": False
                    }]
            return activity

        tasks = [fetch_one(act) for act in activities]
        return list(await asyncio.gather(*tasks))

    def _enrich_activity_with_defaults(self, activity: dict, context: dict, index: int) -> dict:
        city = context.get('city', 'votre destination')

        if not activity.get("id"):
            activity["id"] = f"act_{index+1:03d}"
        if not activity.get("type"):
            activity["type"] = "activity"
        if not activity.get("status"):
            activity["status"] = "onsale"
        if not activity.get("on_sale"):
            activity["on_sale"] = True
        if not activity.get("price_min"):
            activity["price_min"] = 29.90
        if not activity.get("price_max"):
            activity["price_max"] = 49.90
        if not activity.get("currency"):
            activity["currency"] = "EUR"
        if not activity.get("duration"):
            activity["duration"] = "2 heures"
        if not activity.get("languages"):
            activity["languages"] = ["Français", "Anglais"]
        if not activity.get("description_long") and activity.get("description"):
            activity["description_long"] = activity["description"] + " " + f"Cette activité vous permettra de découvrir les merveilles de {city}."
        if not activity.get("important_notes"):
            activity["important_notes"] = [
                "Réservez à l'avance pour garantir votre place",
                "Présentez-vous 15 minutes avant le début"
            ]
        if not activity.get("whats_included"):
            activity["whats_included"] = ["Guide expert", "Équipement nécessaire"]
        if not activity.get("actions"):
            activity["actions"] = [
                {"label": "Réserver", "action": "book", "url": activity.get("booking_url", "#")},
                {"label": "Voir détails", "action": "details", "url": f"/activity/{activity['id']}"}
            ]
        return activity

    def _get_generic_activities_with_details(self, context: dict) -> list:
        city = context.get('city', 'votre destination')
        date = context.get('date', datetime.now().strftime("%Y-%m-%d"))

        activity_templates = [
            {
                "name": f"Visite guidée du centre historique de {city}",
                "category": "Visite guidée",
                "venue": f"Office de Tourisme de {city}",
                "address": f"Place centrale, {city}",
                "artists": ["Guide local professionnel"],
                "price_min": 29.90, "price_max": 39.90,
                "duration": "2 heures", "time": "10:00:00",
                "description": f"Découvrez les richesses architecturales et historiques de {city} lors d'une visite guidée passionnante.",
                "description_long": f"Partez à la découverte des monuments emblématiques de {city} accompagné d'un guide local expert. Cette visite de 2 heures vous fera explorer les ruelles pittoresques et les places animées qui font le charme de la ville.",
                "image": ""
            },
            {
                "name": f"Dégustation de spécialités locales à {city}",
                "category": "Gastronomie",
                "venue": "Caveau des Saveurs",
                "address": f"Rue des Gourmets, {city}",
                "artists": ["Chef local", "Sommelier"],
                "price_min": 49.90, "price_max": 79.90,
                "duration": "1h30", "time": "15:00:00",
                "description": f"Savourez les meilleures spécialités culinaires de {city}.",
                "description_long": f"Une expérience gustative unique pour découvrir les produits du terroir de {city}. Dégustation commentée par un expert qui vous fera voyager à travers les saveurs locales.",
                "image": ""
            },
            {
                "name": f"Excursion nature autour de {city}",
                "category": "Nature",
                "venue": "Centre d'Accueil Nature",
                "address": f"Boulevard Nature, {city}",
                "artists": ["Guide nature certifié"],
                "price_min": 39.90, "price_max": 59.90,
                "duration": "Une journée", "time": "09:00:00",
                "description": f"Explorez les paysages magnifiques aux alentours de {city}.",
                "description_long": f"Une journée complète pour découvrir les merveilles naturelles des environs de {city}. Randonnée guidée à travers forêts, collines et points de vue panoramiques.",
                "image": ""
            },
            {
                "name": f"Atelier d'artisanat local à {city}",
                "category": "Atelier",
                "venue": "Atelier des Artisans",
                "address": f"Rue de l'Artisanat, {city}",
                "artists": ["Artisan local"],
                "price_min": 35.00, "price_max": 55.00,
                "duration": "2h30", "time": "14:00:00",
                "description": f"Apprenez les techniques traditionnelles des artisans de {city}.",
                "description_long": f"Initiez-vous aux savoir-faire locaux lors d'un atelier participatif. Repartez avec votre propre création et des souvenirs uniques de {city}.",
                "image": ""
            }
        ]

        activities = []
        for i, template in enumerate(activity_templates[:4]):
            activities.append({
                "id": f"act_{i+1:03d}",
                "type": "activity",
                "name": template["name"],
                "description": template["description"],
                "description_long": template["description_long"],
                "image": template["image"],
                "date": date,
                "time": template["time"],
                "venue": template["venue"],
                "city": city,
                "address": template["address"],
                "category": template["category"],
                "status": "onsale",
                "on_sale": True,
                "booking_url": f"https://example.com/activity/act_{i+1:03d}",
                "price_min": template["price_min"],
                "price_max": template["price_max"],
                "currency": "EUR",
                "duration": template["duration"],
                "languages": ["Français", "Anglais"],
                "artists": template["artists"],
                "important_notes": [
                    "Réservez à l'avance pour garantir votre place",
                    "Présentez-vous 15 minutes avant le début",
                    "Annulation gratuite jusqu'à 24h avant"
                ],
                "accessibility": "Accessible aux personnes à mobilité réduite",
                "cancellation_policy": "Annulation gratuite jusqu'à 24h avant",
                "meeting_point": template["venue"],
                "whats_included": ["Guide expert", "Équipement nécessaire", "Assurance incluse"],
                "whats_not_included": ["Repas", "Transport"],
                "actions": [
                    {"label": "Réserver", "action": "book", "url": f"https://example.com/activity/act_{i+1:03d}"},
                    {"label": "Voir détails", "action": "details", "url": f"/activity/act_{i+1:03d}"}
                ]
            })

        return activities

    def _build_gemini_context(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        dates = entities.get("DATE", [])
        prices = entities.get("PRICE", [])

        return {
            "city": locs[0] if locs else "",
            "date": dates[0] if dates else "",
            "end_date": dates[1] if len(dates) > 1 else "",
            "budget": f"{prices[0]}€" if prices and prices[0] else "",
            "participants": str(prices[0]) if prices and str(prices[0]).isdigit() else ""
        }

    async def get_details(self, item_id: str, sub_category: str = None) -> dict:
        if item_id.startswith(("act_", "fallback_", "gemini_")):
            return await self._get_gemini_event_details(item_id)

        rag_cached = _cache_get(f"rag_detail:{item_id}")
        if rag_cached:
            return self._format_rag_activity_as_details(rag_cached)

        TOUR_SUBCATS = {
            "Tour/Excursion Booking",
            "Tour/Excursion Details",
            "Tour/Excursion Management",
        }
        EVENT_SUBCATS = {
            "Event Ticket Booking",
            "Event Ticket Details",
        }

        if sub_category in TOUR_SUBCATS:
            item_type    = "attraction"
            detail_label = "Tour/Excursion Details"
        elif sub_category in EVENT_SUBCATS:
            item_type    = "event"
            detail_label = "Event Ticket Details"
        else:
            sc_lower = (sub_category or "").lower()
            if any(k in sc_lower for k in ("tour", "excursion", "attraction")):
                item_type    = "attraction"
                detail_label = "Tour/Excursion Details"
            else:
                item_type    = "event"
                detail_label = "Event Ticket Details"

        api_config = self.api_keys.get(detail_label)

        if not api_config:
            return await self._get_gemini_event_details(item_id)

        url = api_config.get("url", "").replace("{id}", item_id)

        try:
            headers_template = api_config.get("headers_template") or {}
            if isinstance(headers_template, str):
                try:
                    headers_template = json.loads(headers_template)
                except:
                    headers_template = {}

            headers = {
                k: v.replace("{api_key}", api_config.get("key", ""))
                for k, v in headers_template.items()
                if isinstance(v, str)
            }

            params = {"apikey": api_config.get("key")}

            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, params=params, headers=headers)

            if response.status_code == 200:
                data = response.json()
                parsed = await self._gemini_parse_details(data, item_id, item_type)
                if parsed and parsed.get("name"):
                    return parsed
                if item_type == "attraction":
                    return await self.get_attraction_details(item_id)
                return await self.get_event_details(item_id)
            elif response.status_code == 404:
                return await self._get_gemini_event_details(item_id)
            else:
                return await self._get_gemini_event_details(item_id)

        except Exception as e:
            print(f"[ActivityAgent] ❌ Erreur: {e}")
            return await self._get_gemini_event_details(item_id)

    async def _gemini_parse_details(self, raw_data: dict, item_id: str,
                                    item_type: str = "event") -> dict:
        raw_json = json.dumps(raw_data, ensure_ascii=False)
        if len(raw_json) > 15000:
            raw_json = raw_json[:15000] + "..."

        if item_type == "attraction":
            target_format = """{
        "id": "identifiant",
        "name": "nom complet",
        "type": "attraction",
        "description": "description courte",
        "description_long": "description détaillée",
        "image_main": "url image principale",
        "images": [{"url": "...", "ratio": "16_9"}],
        "category": "type d'attraction",
        "subcategory": "sous-type",
        "city": "ville",
        "address": "adresse complète",
        "website": "url site web ou ''",
        "social_media": {
            "facebook": "", "instagram": "", "twitter": "", "youtube": ""
        },
        "upcoming_events_total": 0,
        "aliases": [],
        "actions": [
            {"label": "Voir le site", "action": "website", "url": ""},
            {"label": "Réserver", "action": "book", "url": ""}
        ]
    }"""
        else:
            target_format = """{
        "id": "identifiant",
        "name": "nom complet de l'événement",
        "type": "event",
        "description": "description courte",
        "description_long": "description détaillée ou informations supplémentaires",
        "image_main": "url image principale",
        "images": [{"url": "...", "ratio": "16_9", "width": 0, "height": 0}],
        "date": "YYYY-MM-DD",
        "time": "HH:MM:SS",
        "timezone": "Europe/Paris",
        "venue_name": "nom du lieu",
        "venue_address": "adresse",
        "venue_city": "ville",
        "venue_country": "pays",
        "venue_location": {"latitude": 0.0, "longitude": 0.0},
        "category": "type d'événement",
        "subcategory": "sous-type",
        "artists": ["artiste 1", "artiste 2"],
        "price_min": 0.0,
        "price_max": 0.0,
        "currency": "currency code ou ''",
        "status": "onsale",
        "on_sale_start": "",
        "on_sale_end": "",
        "booking_url": "url de réservation",
        "important_notes": ["note 1", "note 2"],
        "accessibility": "informations accessibilité",
        "cancellation_policy": "conditions annulation",
        "whats_included": ["inclus 1", "inclus 2"],
        "whats_not_included": ["non inclus 1"],
        "highlights": ["point fort 1", "point fort 2"],
        "duration": "durée estimée",
        "meeting_point": "point de rendez-vous",
        "actions": [
            {"label": "Réserver maintenant", "action": "book", "url": ""},
            {"label": "Voir le plan de salle", "action": "seatmap", "url": ""}
        ]
    }"""

        prompt = f"""Tu es un expert en extraction de données JSON d'APIs d'activités.

    Voici la réponse COMPLÈTE de l'API pour l'item "{item_id}" (type: {item_type}) :
    {raw_json}

    MISSION : extrais TOUTES les informations disponibles et mappe-les dans ce format.
    N'invente rien — utilise UNIQUEMENT les valeurs présentes dans les données.
    Si une valeur est absente, mets "" pour string, 0.0 pour nombre, [] pour liste.

    Réponds UNIQUEMENT avec ce JSON (sans texte, sans backticks) :
    {target_format}"""

        try:
            raw_result = await self._call_openai(prompt, timeout=60.0)
            details = json.loads(raw_result)
            return self._normalize_details(details, item_type)
        except Exception as e:
            print(f"[ActivityParser] ❌ Parse détails échoué: {e}")
            return {}

    def _normalize_details(self, details: dict, item_type: str) -> dict:
        def _safe_str(v, default="") -> str:
            return str(v).strip() if v is not None else default

        def _safe_float(v, default=0.0) -> float:
            try:
                return float(str(v).replace(",", "."))
            except:
                return default

        def _safe_list(v) -> list:
            return v if isinstance(v, list) else []

        images = []
        main_image = _safe_str(details.get("image_main"))
        if main_image:
            images.append({"ratio": "16_9", "url": main_image, "width": 1024, "height": 576, "fallback": False})
        for img in _safe_list(details.get("images", [])):
            if isinstance(img, dict) and img.get("url") and img.get("url") != main_image:
                images.append(img)

        price_min = _safe_float(details.get("price_min"))
        price_max = _safe_float(details.get("price_max"))
        if price_max == 0.0 and price_min > 0:
            price_max = price_min * 1.5

        return {
            "id":             _safe_str(details.get("id")),
            "name":           _safe_str(details.get("name"), "Activité"),
            "type":           item_type,
            "locale":         "fr-fr",
            "url":            _safe_str(details.get("booking_url")),
            "status":         "success",
            "source":         "api_parsed",
            "description":    _safe_str(details.get("description")),
            "additionalInfo": _safe_str(details.get("description_long")),
            "info":           "\n".join(_safe_list(details.get("whats_included", []))),
            "pleaseNote":     "\n".join(_safe_list(details.get("important_notes", []))),
            "description_long":    _safe_str(details.get("description_long")),
            "highlights":          _safe_list(details.get("highlights")),
            "whats_included":      _safe_list(details.get("whats_included")),
            "whats_not_included":  _safe_list(details.get("whats_not_included")),
            "meeting_point":       _safe_str(details.get("meeting_point")),
            "cancellation_policy": _safe_str(details.get("cancellation_policy")),
            "accessibility_info":  _safe_str(details.get("accessibility")),
            "duration":            _safe_str(details.get("duration")),
            "languages":           _safe_list(details.get("languages", ["Français"])),
            "artists":             _safe_list(details.get("artists")),
            "images": images,
            "dates": {
                "start": {
                    "localDate": _safe_str(details.get("date")),
                    "localTime": _safe_str(details.get("time")),
                    "dateTBD":   not bool(details.get("date")),
                    "dateTBA":   not bool(details.get("date")),
                    "timeTBA":   not bool(details.get("time")),
                },
                "timezone":        _safe_str(details.get("timezone"), "Europe/Paris"),
                "spanMultipleDays": False
            },
            "priceRanges": [{
                "type":     "standard",
                "currency": _safe_str(details.get("currency"), "EUR"),
                "min":      price_min,
                "max":      price_max
            }],
            "sales": {
                "public": {
                    "startDateTime": _safe_str(details.get("on_sale_start")),
                    "endDateTime":   _safe_str(details.get("on_sale_end")),
                    "startTBD": False, "startTBA": False
                }
            },
            "venue": {
                "name":       _safe_str(details.get("venue_name")),
                "address":    _safe_str(details.get("venue_address")),
                "city":       _safe_str(details.get("venue_city")),
                "country":    _safe_str(details.get("venue_country")),
                "postalCode": None,
                "location":   details.get("venue_location"),
            },
            "classifications": [{
                "primary": True,
                "segment": {"name": _safe_str(details.get("category", "Activity"))},
                "genre":   {"name": _safe_str(details.get("subcategory", ""))},
            }],
            "externalLinks": details.get("social_media", {}),
            "aliases":       _safe_list(details.get("aliases")),
            "upcomingEvents": {"total": details.get("upcoming_events_total", 0)},
            "actions": _safe_list(details.get("actions", [
                {"label": "Réserver", "action": "book", "url": _safe_str(details.get("booking_url"))}
            ])),
            "promoter": {}, "promoters": [], "seatmap": {},
            "accessibility": {}, "ticketLimit": {},
            "_links": {}, "_embedded": {"venues": [], "attractions": []}
        }

    def _enrich_activities(self, events: list, sub_category: str = None) -> list:
        if not events or not isinstance(events, list):
            return []

        enriched = []
        for event in events:
            embedded = event.get("_embedded", {})
            venues = embedded.get("venues", [])
            venue = venues[0] if venues else {}

            dates_data = event.get("dates", {})
            start_date = dates_data.get("start", {})

            classifications = event.get("classifications", [])
            classification = classifications[0] if classifications else {}
            genre = classification.get("genre", {})
            segment = classification.get("segment", {})

            images = event.get("images", [])
            sales = event.get("sales", {})
            public_sales = sales.get("public", {})

            enriched.append({
                "id": event.get("id"),
                "type": "activity",
                "name": event.get("name"),
                "description": self._truncate_text(event.get("description", ""), 100),
                "image": self._get_best_image(images),
                "date": start_date.get("localDate"),
                "time": start_date.get("localTime", ""),
                "venue": venue.get("name"),
                "city": venue.get("city", {}).get("name") if venue.get("city") else None,
                "category": genre.get("name") or segment.get("name") or "Événement",
                "status": dates_data.get("status", {}).get("code"),
                "sub_category": sub_category,
                "on_sale": self._check_on_sale(public_sales),
                "booking_url": event.get("url"),
                "actions": [
                    {"label": "Réserver", "action": "book", "url": event.get("url")},
                    {"label": "Voir détails", "action": "details", "url": f"/activity/{event.get('id')}"}
                ]
            })

        return enriched

    async def get_attraction_details(self, attraction_id: str) -> dict:
        if attraction_id.startswith("act_") or attraction_id.startswith("fallback_") or attraction_id.startswith("gemini_"):
            return await self._get_gemini_event_details(attraction_id)

        try:
            api_config = self.api_keys.get("Local Attractions & Activities")
            if not api_config:
                api_config = self.api_keys.get("Tour/Excursion Search")

            if not api_config:
                return {"error": "Configuration API non trouvée", "status": "error"}

            url = f"https://app.ticketmaster.com/discovery/v2/attractions/{attraction_id}.json"
            params = {"apikey": api_config.get("key"), "locale": "fr"}

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, params=params)

            if response.status_code == 200:
                data = response.json()
                classifications = data.get("classifications", [])
                classification = classifications[0] if classifications else {}
                segment = classification.get("segment", {})
                genre = classification.get("genre", {})
                images = data.get("images", [])
                external_links = data.get("externalLinks", {})
                upcoming_events = data.get("upcomingEvents", {})

                return {
                    "id": data.get("id"),
                    "name": data.get("name"),
                    "type": data.get("type", "attraction"),
                    "locale": data.get("locale", "fr"),
                    "url": data.get("url"),
                    "test": data.get("test", False),
                    "description": data.get("description", ""),
                    "additionalInfo": data.get("additionalInfo", ""),
                    "images": images,
                    "main_image": self._get_best_image(images),
                    "gallery_images": self._get_gallery_images(images),
                    "classifications": [
                        {
                            "primary": c.get("primary", False),
                            "segment": c.get("segment", {}),
                            "genre": c.get("genre", {}),
                            "subGenre": c.get("subGenre", {}),
                            "type": c.get("type", {}),
                            "subType": c.get("subType", {})
                        } for c in classifications
                    ],
                    "category": segment.get("name", ""),
                    "subcategory": genre.get("name", ""),
                    "externalLinks": {
                        "facebook": external_links.get("facebook", []),
                        "twitter": external_links.get("twitter", []),
                        "instagram": external_links.get("instagram", []),
                        "youtube": external_links.get("youtube", []),
                        "homepage": external_links.get("homepage", []),
                        "wiki": external_links.get("wiki", [])
                    },
                    "aliases": data.get("aliases", []),
                    "localizedAliases": data.get("localizedAliases", {}),
                    "upcomingEvents": {
                        "total": upcoming_events.get("_total", 0),
                        "ticketmaster": upcoming_events.get("ticketmaster", 0),
                        "mfx": upcoming_events.get("mfx", 0),
                        "universe": upcoming_events.get("universe", 0)
                    },
                    "_links": data.get("_links", {}),
                    "status": "success",
                    "source": "ticketmaster_attractions"
                }
            else:
                return {"error": f"HTTP {response.status_code}", "status": "error"}

        except Exception as e:
            print(f"[ActivityAgent] Erreur get_attraction_details: {e}")
            return {"error": str(e), "status": "error"}

    async def get_event_details(self, event_id: str) -> dict:
        if event_id.startswith("act_") or event_id.startswith("fallback_") or event_id.startswith("gemini_"):
            return await self._get_gemini_event_details(event_id)

        try:
            api_config = self.api_keys.get("Tour/Excursion Search")
            if not api_config:
                return {"error": "Configuration API non trouvée", "status": "error"}

            url = f"https://app.ticketmaster.com/discovery/v2/events/{event_id}.json"
            params = {"apikey": api_config.get("key")}

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, params=params)

            if response.status_code == 200:
                data = response.json()
                embedded = data.get("_embedded", {})
                venues = embedded.get("venues", [])
                venue = venues[0] if venues else {}
                dates_data = data.get("dates", {})
                start_date = dates_data.get("start", {})
                classifications = data.get("classifications", [])
                images = data.get("images", [])
                sales = data.get("sales", {})
                public_sales = sales.get("public", {})
                price_ranges = data.get("priceRanges", [])

                return {
                    "id": data.get("id"),
                    "name": data.get("name"),
                    "type": data.get("type"),
                    "locale": data.get("locale"),
                    "url": data.get("url"),
                    "test": data.get("test", False),
                    "description": data.get("description", ""),
                    "additionalInfo": data.get("additionalInfo", ""),
                    "info": data.get("info", ""),
                    "pleaseNote": data.get("pleaseNote", ""),
                    "images": images,
                    "dates": {
                        "start": {
                            "localDate": start_date.get("localDate"),
                            "localTime": start_date.get("localTime"),
                            "dateTime": start_date.get("dateTime"),
                            "dateTBD": start_date.get("dateTBD", False),
                            "dateTBA": start_date.get("dateTBA", False),
                            "timeTBA": start_date.get("timeTBA", False),
                            "noSpecificTime": start_date.get("noSpecificTime", False)
                        },
                        "timezone": dates_data.get("timezone"),
                        "spanMultipleDays": dates_data.get("spanMultipleDays", False)
                    },
                    "priceRanges": [
                        {
                            "type": pr.get("type"),
                            "currency": pr.get("currency"),
                            "min": pr.get("min"),
                            "max": pr.get("max")
                        } for pr in price_ranges
                    ],
                    "sales": {
                        "public": {
                            "startDateTime": public_sales.get("startDateTime"),
                            "startTBD": public_sales.get("startTBD", False),
                            "startTBA": public_sales.get("startTBA", False),
                            "endDateTime": public_sales.get("endDateTime")
                        }
                    },
                    "venue": {
                        "name": venue.get("name"),
                        "address": venue.get("address", {}).get("line1"),
                        "city": venue.get("city", {}).get("name"),
                        "state": venue.get("state", {}).get("name"),
                        "country": venue.get("country", {}).get("name"),
                        "postalCode": venue.get("postalCode"),
                        "location": venue.get("location")
                    },
                    "classifications": [
                        {
                            "primary": c.get("primary", False),
                            "segment": c.get("segment", {}),
                            "genre": c.get("genre", {}),
                            "subGenre": c.get("subGenre", {}),
                            "type": c.get("type", {}),
                            "subType": c.get("subType", {})
                        } for c in classifications
                    ],
                    "artists": [att.get("name") for att in embedded.get("attractions", [])],
                    "promoter": data.get("promoter", {}),
                    "promoters": data.get("promoters", []),
                    "seatmap": data.get("seatmap", {}),
                    "accessibility": data.get("accessibility", {}),
                    "ticketLimit": data.get("ticketLimit", {}),
                    "_links": data.get("_links", {}),
                    "_embedded": {
                        "venues": embedded.get("venues", []),
                        "attractions": embedded.get("attractions", [])
                    },
                    "status": "success"
                }
            else:
                return {"error": f"HTTP {response.status_code}", "status": "error"}

        except Exception as e:
            print(f"[ActivityAgent] Erreur get_event_details: {e}")
            return {"error": str(e), "status": "error"}

    async def _get_gemini_event_details(self, event_id: str) -> dict:
        for cache_key, cached_activities in _gemini_cache.items():
            if isinstance(cached_activities, list):
                for activity in cached_activities:
                    if isinstance(activity, dict) and activity.get("id") == event_id:
                        return self._format_gemini_activity_as_details(activity)

        index = 0
        try:
            parts = event_id.rsplit("_", 1)
            if len(parts) == 2 and parts[1].isdigit():
                index = int(parts[1]) - 1
        except Exception:
            pass

        prompt = f"""Tu es un assistant voyage expert. Génère les détails complets d'une activité touristique.

    ID demandé: {event_id}

    Réponds UNIQUEMENT en JSON valide avec cette structure exacte:
    {{
        "id": "{event_id}",
        "name": "Nom complet de l'activité",
        "type": "activity",
        "description": "Description courte (1-2 phrases)",
        "description_long": "Description détaillée et complète (3-5 paragraphes)",
        "image_main": "",
        "images": [],
        "date": "2026-06-15",
        "time": "10:00:00",
        "duration": "2 heures 30",
        "venue": "Nom du lieu exact",
        "city": "Nom de la ville",
        "address": "Adresse complète",
        "category": "Type (Concert / Musée / Visite / Sport / Spectacle / Gastronomie / Nature)",
        "status": "onsale",
        "on_sale": true,
        "booking_url": "https://example.com/reserver",
        "price_min": 25.0,
        "price_max": 75.0,
        "currency": "EUR",
        "languages": ["Français", "Anglais"],
        "artists": ["Nom artiste ou guide"],
        "important_notes": ["Note 1", "Note 2", "Note 3"],
        "accessibility": "Description accessibilité PMR",
        "cancellation_policy": "Conditions d'annulation détaillées",
        "meeting_point": "Point de rendez-vous précis",
        "whats_included": ["Inclus 1", "Inclus 2", "Inclus 3"],
        "whats_not_included": ["Non inclus 1", "Non inclus 2"],
        "highlights": ["Point fort 1", "Point fort 2", "Point fort 3"],
        "reviews_summary": "Résumé des avis (note moyenne et points clés)",
        "actions": [
            {{"label": "Réserver maintenant", "action": "book", "url": "https://example.com/reserver"}},
            {{"label": "Contacter l'organisateur", "action": "contact", "url": "mailto:contact@example.com"}}
        ]
    }}

    AUCUN texte avant ou après le JSON."""

        try:
            raw = await self._call_openai(prompt, timeout=60.0)
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            activity = json.loads(raw.strip())
            query = self._build_image_query(activity)
            if query:
                image_url = await self._fetch_unsplash_image(query)
                if image_url:
                    activity["image"] = image_url
                    activity["image_main"] = image_url
                    activity["images"] = [{
                        "ratio": "16_9", "url": image_url,
                        "width": 1024, "height": 576, "fallback": False
                    }]
            return self._format_gemini_activity_as_details(activity)

        except Exception as e:
            print(f"[ActivityAgent] ❌ Erreur GPT détails: {e}")
            return self._get_generic_activity_details(event_id)

    def _format_gemini_activity_as_details(self, activity: dict) -> dict:
        images = []

        flat_image = activity.get("image", "")
        if flat_image and flat_image.startswith("http"):
            images.append({
                "ratio": "16_9", "url": flat_image,
                "width": 1024, "height": 576, "fallback": False
            })

        for img in activity.get("images", []):
            if isinstance(img, dict):
                url = img.get("url", "")
            else:
                url = str(img)
            if url and url.startswith("http") and url != flat_image:
                images.append({
                    "ratio": img.get("ratio", "16_9") if isinstance(img, dict) else "16_9",
                    "url": url,
                    "width": img.get("width", 800) if isinstance(img, dict) else 800,
                    "height": img.get("height", 600) if isinstance(img, dict) else 600,
                    "fallback": False
                })

        main_image = activity.get("image_main", "")
        if main_image and main_image.startswith("http") and main_image != flat_image:
            images.insert(0, {
                "ratio": "16_9", "url": main_image,
                "width": 1024, "height": 576, "fallback": False
            })

        price_min = activity.get("price_min", 29.90)
        price_max = activity.get("price_max", 49.90)
        currency = activity.get("currency", "EUR")

        return {
            "id": activity.get("id"),
            "name": activity.get("name", "Activité"),
            "type": activity.get("type", "activity"),
            "locale": "fr-fr",
            "url": activity.get("booking_url", ""),
            "test": False,
            "description": activity.get("description", ""),
            "additionalInfo": activity.get("description_long", ""),
            "info": "\n".join(activity.get("whats_included", [])),
            "pleaseNote": "\n".join(activity.get("important_notes", [])),
            "description_long": activity.get("description_long", ""),
            "highlights": activity.get("highlights", []),
            "whats_included": activity.get("whats_included", []),
            "whats_not_included": activity.get("whats_not_included", []),
            "meeting_point": activity.get("meeting_point", ""),
            "cancellation_policy": activity.get("cancellation_policy", ""),
            "accessibility_info": activity.get("accessibility", ""),
            "duration": activity.get("duration", ""),
            "languages": activity.get("languages", ["Français"]),
            "reviews_summary": activity.get("reviews_summary", ""),
            "images": images,
            "dates": {
                "start": {
                    "localDate": activity.get("date"),
                    "localTime": activity.get("time", ""),
                    "dateTime": None,
                    "dateTBD": False,
                    "dateTBA": not bool(activity.get("date")),
                    "timeTBA": not bool(activity.get("time")),
                    "noSpecificTime": False
                },
                "timezone": "Europe/Paris",
                "spanMultipleDays": False
            },
            "priceRanges": [{
                "type": "standard",
                "currency": currency,
                "min": price_min,
                "max": price_max
            }],
            "sales": {
                "public": {
                    "startDateTime": "2026-01-01T00:00:00Z",
                    "startTBD": False, "startTBA": False,
                    "endDateTime": "2026-12-31T23:59:59Z"
                }
            },
            "venue": {
                "name": activity.get("venue"),
                "address": activity.get("address"),
                "city": activity.get("city"),
                "state": None,
                "country": "France",
                "postalCode": None,
                "location": None
            },
            "classifications": [{
                "primary": True,
                "segment": {"id": "KZFzniwnSyZfZ7v7n1", "name": activity.get("category", "Activity")},
                "genre": {"id": "KnvZfZ7vAvv", "name": activity.get("category", "Activity")},
                "subGenre": {"id": "KZazBEonSMnZfZ7vAdt", "name": activity.get("category", "Activity")},
                "type": {"id": "KZAyXgnZfZ7v7l1", "name": "Undefined"},
                "subType": {"id": "KZFzBErXgnZfZ7vAde", "name": "Undefined"}
            }],
            "artists": activity.get("artists", []),
            "actions": activity.get("actions", [
                {"label": "Réserver", "action": "book", "url": activity.get("booking_url", "#")}
            ]),
            "promoter": {}, "promoters": [], "seatmap": {},
            "accessibility": {}, "ticketLimit": {},
            "_links": {}, "_embedded": {"venues": [], "attractions": []},
            "source": "gemini",
            "status": "success"
        }

    def _get_generic_activity_details(self, event_id: str) -> dict:
        return {
            "id": event_id,
            "name": "Activité touristique",
            "type": "activity",
            "description": "Une activité découverte de votre destination.",
            "additionalInfo": "Contactez-nous pour plus d'informations sur cette activité.",
            "description_long": "Profitez de cette expérience unique pour découvrir les richesses de votre destination.",
            "images": [],
            "dates": {
                "start": {
                    "localDate": None, "localTime": None, "dateTime": None,
                    "dateTBD": True, "dateTBA": True, "timeTBA": True, "noSpecificTime": True
                },
                "timezone": "Europe/Paris",
                "spanMultipleDays": False
            },
            "priceRanges": [{"type": "standard", "currency": "EUR", "min": 0.0, "max": 0.0}],
            "sales": {"public": {"startDateTime": None, "startTBD": True, "startTBA": True, "endDateTime": None}},
            "venue": {"name": None, "address": None, "city": None, "state": None, "country": None, "postalCode": None, "location": None},
            "classifications": [],
            "artists": [],
            "highlights": [],
            "whats_included": [],
            "whats_not_included": [],
            "meeting_point": "",
            "cancellation_policy": "Contactez l'organisateur pour connaître les conditions d'annulation.",
            "languages": ["Français"],
            "duration": "",
            "promoter": {}, "promoters": [], "seatmap": {},
            "accessibility": {}, "ticketLimit": {},
            "_links": {}, "_embedded": {"venues": [], "attractions": []},
            "source": "generic",
            "status": "success"
        }

    def _truncate_text(self, text: str, max_len: int) -> str:
        if not text:
            return ""
        return text[:max_len] + "..." if len(text) > max_len else text

    def _get_best_image(self, images: list) -> str:
        if not images:
            return ""
        for img in images:
            if img.get("ratio") == "16_9" and img.get("width", 0) <= 400:
                return img.get("url")
        return images[0].get("url", "")

    def _get_gallery_images(self, images: list) -> list:
        return [img.get("url") for img in images if img.get("width", 0) >= 800][:5]

    def _check_on_sale(self, public_sales: dict) -> bool:
        from datetime import datetime
        start = public_sales.get("startDateTime")
        if not start:
            return True
        return datetime.now().isoformat() >= start

    def _extract_notes(self, description: str) -> list:
        if not description:
            return []
        notes = []
        keywords = ["important", "attention", "non-remboursable", "interdit", "obligatoire"]
        for line in description.split('\n'):
            if any(k in line.lower() for k in keywords):
                clean = line.strip().replace('•', '').strip()
                if clean and len(clean) < 200:
                    notes.append(clean)
        return notes[:5]

    async def _book_tour(self, entities: dict) -> dict:
        locs  = entities.get("LOC", [])
        dates = entities.get("DATE", [])

        tour_city_list = entities.get("tour_city", [])
        city = tour_city_list[0] if tour_city_list else (
            entities.get("destination") or (locs[0] if locs else "")
        )
        city = self._clean_city(city)

        if not city:
            missing_fields = [
                {
                    "field":       "city",
                    "type":        "LOC",
                    "label":       "Destination",
                    "description": "Ville ou destination pour les activités",
                    "placeholder": "Paris, Tunis, Rome...",
                }
            ]
            return {
                "status":          "need_more_info",
                "source":          "validation",
                "message":         "Veuillez indiquer votre destination",
                "required_fields": missing_fields,
                "action":          "complete_form",
                "form":            self._build_tour_search_form(missing_fields),
                "steps": [
                    "Veuillez indiquer votre destination",
                    "Envoyez le formulaire rempli"
                ],
                "user_id":    self.user_id,
                "session_id": self.session_id,
            }

        tour_date_list = entities.get("tour_departure_date", [])
        start_date = tour_date_list[0] if tour_date_list else (dates[0] if dates else None)
        end_date   = dates[1] if len(dates) > 1 else start_date

        api_payload = {
            "city":          city,
            "startDateTime": start_date,
            "endDateTime":   end_date,
        }

        rag_query = f"excursions visites guidées {city}"
        steps = [
            "Voici les excursions disponibles",
            "Choisissez votre visite",
            "Réservez en ligne",
        ]

        if self.response_type == "api":
            return await self._resolve_api(api_payload, rag_query, steps, "activities", "GET")
        elif self.response_type == "rag":
            return await self._resolve_rag_only(entities)
        elif self.response_type == "llm":
            return await self._resolve_llm_only(entities, "")
        elif self.response_type == "human":
            return await self._resolve_human_only(self.subcat_config.get("human_contact", ""))

        return await self._resolve_api(api_payload, rag_query, steps, "activities", "GET")

    async def _book_event(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        dates = entities.get("DATE", [])

        start_date = None
        end_date = None

        if len(dates) > 0 and dates[0]:
            start_date = dates[0]
        if len(dates) > 1 and dates[1]:
            end_date = dates[1]

        if start_date and not end_date:
            end_date = start_date

        city = locs[0] if locs else ""
        city = self._clean_city(city)

        api_payload = {
            "city": city,
            "startDateTime": start_date,
            "endDateTime": end_date,
        }

        rag_query = f"attractions lieux à visiter {city}"
        steps = ["Voici les attractions populaires", "Découvrez les incontournables", "Planifiez votre visite"]

        if self.response_type == "api":
            return await self._resolve_api(api_payload, rag_query, steps, "activities", "GET")
        elif self.response_type == "rag":
            return await self._resolve_rag_only(entities)
        elif self.response_type == "llm":
            return await self._resolve_llm_only(entities, "")
        elif self.response_type == "human":
            return await self._resolve_human_only(self.subcat_config.get("human_contact", ""))

        return await self._resolve_api(api_payload, rag_query, steps, "activities", "GET")

    async def _modify_tour(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        locs = [self._clean_city(v) for v in locs]
        dates = entities.get("DATE", [])
        booking_ref = entities.get("FLIGHT", [])

        api_payload = {
            "booking_reference": booking_ref[0] if booking_ref else None,
            "new_city": self._clean_city(locs[0]) if locs else None,
            "new_date": dates[0] if dates else None,
        }

        rag_query = "modification réservation excursion"
        steps = ["Vérifiez les conditions", "Des frais peuvent s'appliquer", "Confirmation modifiée"]

        if self.response_type == "api":
            return await self._resolve_api(api_payload, rag_query, steps, "result", "PUT")
        elif self.response_type == "rag":
            return await self._resolve_rag_only(entities)
        elif self.response_type == "llm":
            return await self._resolve_llm_only(entities, "")
        elif self.response_type == "human":
            return await self._resolve_human_only(self.subcat_config.get("human_contact", ""))

        return await self._resolve_api(api_payload, rag_query, steps, "result", "PUT")

    async def _fetch_unsplash_image(self, query: str) -> str:
        try:
            import unicodedata
            def remove_accents(text):
                return ''.join(c for c in unicodedata.normalize('NFD', text)
                            if unicodedata.category(c) != 'Mn')
            clean_query = remove_accents(query)

            unsplash_key = f"unsplash:{hashlib.sha256(clean_query.encode()).hexdigest()[:16]}"
            cached = _cache_get(unsplash_key)
            if cached is not None:
                return cached

            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    "https://api.unsplash.com/photos/random",
                    params={"query": clean_query, "orientation": "landscape", "content_filter": "high"},
                    headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"}
                )

            if response.status_code == 200:
                image_url = response.json()["urls"]["raw"] + "&w=1080&q=80&fit=crop"
                _cache_set(unsplash_key, image_url)
                return image_url
            return ""
        except Exception as e:
            print(f"[Unsplash] ❌ {e}")
            return ""

    def _build_image_query(self, activity: dict) -> str:
        import unicodedata

        def remove_accents(text):
            return ''.join(
                c for c in unicodedata.normalize('NFD', text)
                if unicodedata.category(c) != 'Mn'
            )

        name = activity.get("name", "")
        category = activity.get("category", "")
        city = activity.get("city", "")

        stop_words = {
            "de", "du", "des", "le", "la", "les", "un", "une", "et", "a",
            "au", "aux", "en", "sur", "par", "pour", "avec", "dans",
            "d'une", "d'un", "l'", "journee", "guidee", "visite", "excursion",
            "exploration", "decouverte", "sejour"
        }

        words = remove_accents(name).split()
        keywords = [w for w in words
                    if w.lower() not in stop_words
                    and len(w) > 3
                    and w.isalpha()][:4]

        city_clean = remove_accents(city)
        if city_clean and city_clean.lower() not in " ".join(keywords).lower():
            keywords.append(city_clean)

        return " ".join(keywords)


async def activity_agent(entities, language, tenant_config, sub_category=None,
                         tenant_id="", user_id="", session_id="",
                         response_type="api", subcat_config=None,
                         raw_text="", **kwargs) -> dict:
    agent = ActivityAgent(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
    return await agent.run(entities, language, tenant_config, sub_category=sub_category,
                           tenant_id=tenant_id, user_id=user_id, session_id=session_id,
                           response_type=response_type, subcat_config=subcat_config,
                           raw_text=raw_text, **kwargs)