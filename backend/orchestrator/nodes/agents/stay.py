# stay.py 
import asyncio
from html import entities
import json
import re
import uuid
import httpx
import hashlib
from datetime import datetime
from typing import Optional, Any
from core.config import settings
from orchestrator.rag.rag_engine import RAGEngine

_llm_cache: dict[str, dict] = {}

SUBCAT_DISPATCH = {
    "Hotel Booking":           "_book_stay",
    "Hotel Management":        "_modify_stay",
}

GEO_KEYS = {
    "city", "geoid", "geo_id", "location_id", "locationid",
    "lat", "lon", "latitude", "longitude", "place_id",
    "destination", "origin", "address", "region", "country",
    "hotel_id", "property_id",
}
_rag_cache: dict[str, RAGEngine] = {}


class StayAgent:

    def __init__(self, tenant_id: str = "", user_id: str = "", session_id: str = ""):
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.session_id = session_id
        self.tenant_config = {}
        self.entities = {}
        self.language = ""
        self.api_keys = {}
        self.sub_category = ""
        self.agent_type = "stay"
        self.response_type = "api"
        self.subcat_config = {}
        self.raw_text = ""
        
        cache_key = f"{tenant_id}:{self.agent_type}"
        if cache_key not in _rag_cache:
            _rag_cache[cache_key] = RAGEngine(tenant_id, self.agent_type, user_id, session_id)
        self.rag = _rag_cache[cache_key]

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
                  tenant_id="", user_id="", session_id="",response_type="api",subcat_config=None,raw_text = "",**kwargs ) -> dict:
        self.tenant_id = tenant_id or self.tenant_id
        self.tenant_config = tenant_config
        self.user_id = user_id or self.user_id
        self.session_id = session_id or self.session_id
        self.entities = entities
        self.language = language
        self.sub_category = sub_category or ""
        self.response_type = response_type
        self.subcat_config = subcat_config or {}
        self.raw_text = raw_text

        self.api_keys = await self._load_api_keys(self.tenant_id)

        method_name = SUBCAT_DISPATCH.get(sub_category)
        if not method_name and sub_category:
            subcat_only = sub_category.split(":")[-1].strip()
            method_name = next(
                (v for k, v in SUBCAT_DISPATCH.items() if k.endswith(subcat_only))
            )
        method = getattr(self, method_name)
        return await method(entities)

    async def _build_frontend_hotels(self, hotels_raw: list, api_payload: dict, nights: int) -> list:
        city     = api_payload.get("city", "")
        adults   = api_payload.get("adults", 1)
        currency = api_payload.get("currency", "EUR")
        
        def _safe_float(val) -> float:
            try:    return float(str(val).replace(",", "."))
            except: return 0.0

        def _build_amenities(stars: int) -> list:
            base = [
                {"icon": "wifi",      "name": "WiFi gratuit",   "included": True},
                {"icon": "breakfast", "name": "Petit-déjeuner", "included": True},
                {"icon": "parking",   "name": "Parking",        "included": True},
            ]
            if stars >= 4: base.append({"icon": "pool", "name": "Piscine", "included": True})
            if stars >= 5: base += [
                {"icon": "spa", "name": "Spa",            "included": True},
                {"icon": "gym", "name": "Salle de sport", "included": True},
            ]
            return base

        frontend_hotels = []
        for i, h in enumerate(hotels_raw):
            if not isinstance(h, dict):
                continue

            stars       = int(h.get("stars") or 3)
            total_price = _safe_float(h.get("total_price") or 0)
            ppn         = round(total_price / nights, 2) if nights > 0 and total_price > 0 else 0.0
            if nights > 1 and total_price > 0 and ppn > 0:
                if abs(ppn - total_price) < 0.01:  
                    ppn         = total_price
                    total_price = round(ppn * nights, 2)
            raw_rating = _safe_float(h.get("rating") or 0)
            if raw_rating > 5:  raw_rating = round(raw_rating / 2, 1)
            if raw_rating == 0: raw_rating = {3: 3.5, 4: 4.0, 5: 4.5}.get(stars, 3.5)

            raw_offers = h.get("offers") or []
            if not raw_offers and total_price > 0:
                raw_offers = [{"id": f"offer_{i}_0", "name": "Chambre Standard",
                            "board_code": "RO", "board_type": "Chambre seule",
                            "price": total_price, "quantity": 5}]

            built_offers = []
            for j, o in enumerate(raw_offers[:2]):
                if not isinstance(o, dict): continue
                op  = _safe_float(o.get("price") or total_price or 0)
                opn = _safe_float(o.get("price_per_night") or 0) or (
                    round(op / nights, 2) if nights > 0 and op > 0 else op)
                board_code = o.get("board_code") or "RO"
                board_type = o.get("board_type") or ""
                built_offers.append({
                    "id":                    str(o.get("id") or f"offer_{i}_{j}"),
                    "name":                  o.get("name") or "Chambre Standard",
                    "board_type":            board_type,
                    "board_code":            board_code,
                    "description":           o.get("description") or f"{o.get('name','Chambre')} - {board_type}",
                    "price":                 op,
                    "price_per_night":       opn,
                    "currency":              currency,
                    "quantity":              int(o.get("quantity") or 5),
                    "adults":                int(o.get("adults") or adults),
                    "children":              list(o.get("children") or []),
                    "cancellation_deadline": o.get("cancellation_deadline") or "",
                    "stop_reservation":      bool(o.get("stop_reservation", False)),
                    "selected":              False,
                    "recommended":           j == 0,
                })

            if not built_offers:
                continue

            if total_price == 0 and built_offers:
                total_price = built_offers[0]["price"]
                ppn         = built_offers[0]["price_per_night"]

            images   = [u for u in (h.get("images") or []) if isinstance(u, str) and u.startswith("http")][:5]
            image_url= images[0] if images else ""

            frontend_hotels.append({
                "id":              str(h.get("id") or f"hotel_{i}"),
                "type":            "hotel",
                "name":            h.get("name") or "Hôtel",
                "stars":           stars,
                "rating":          raw_rating,
                "rating_count":    int(h.get("rating_count") or 0),
                "currency":        currency,
                "price_per_night": ppn,
                "total_price":     total_price,
                "nights":          nights,
                "location":        {"city": h.get("city") or city,
                                    "country": h.get("country") or "",
                                    "address": h.get("address") or ""},
                "city":            h.get("city") or city,
                "address":         h.get("address") or "",
                "image":           image_url,
                "images":          images,
                "token":           h.get("token") or "",
                "amenities":       _build_amenities(stars),
                "offers":          built_offers,
                "best_offer":      built_offers[0] if built_offers else None,
                "price_range":     {"min": total_price, "max": round(total_price * 1.3, 2)},
                "source":          "RAG",
                "recommended":     90 - (i * 5),
                "actions": [
                    {"label": "Voir détails", "action": "details", "url": f"/hotel/{h.get('id', i)}"},
                    {"label": "Réserver",     "action": "book",    "url": f"/book/hotel/{h.get('id', i)}"},
                ],
            })

        return frontend_hotels

    async def _resolve_rag_only(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        dates = entities.get("DATE", [])
        prices = entities.get("PRICE", [])
        city = (
            self.entities.get("hotel_city")
            or (self.entities.get("city", [None])[0]
                if isinstance(self.entities.get("city"), list) else self.entities.get("city"))
            or (locs[0] if locs else "")
        )
        city = self._clean_city(city)
        check_in  = self.entities.get("check_in")  or (dates[0] if dates else "")
        check_out = self.entities.get("check_out") or (dates[1] if len(dates) > 1 else "")
        adults = int(prices[0]) if prices and str(prices[0]).isdigit() else 1
        
        api_payload = {
            "city": city,
            "arrival_date": check_in,
            "departure_date": check_out,
            "adults": adults,
            "currency": self.tenant_config.get("currency", "EUR")
        }
        rag_query = f"hôtels à {city} recommandations hébergement"
        rag_result = await self.rag.search_with_subcategory(
            query=rag_query,
            sub_category=self.sub_category,
            k=10,
            filter_by_loc=None
        )

        if rag_result and rag_result.get("context"):
            hotels_list = await self._gemini_parse_rag_context(rag_result["context"], api_payload)
            
            if not isinstance(hotels_list, list):
                hotels_list = []
            
            return {
                "status": "rag_only",
                "source": "RAG+GPT-4o-mini",
                "type": "hotel_search_results",
                "hotels": hotels_list,
                "results": hotels_list,
                "query": api_payload,
                "user_id": self.user_id,
                "session_id": self.session_id
            }
        
        return {
            "status": "rag_only",
            "type": "info_results",
            "message": f"Aucune information trouvée pour {city}",
            "results": [],
            "user_id": self.user_id,
            "session_id": self.session_id
        }

    async def _gemini_parse_rag_context(self, rag_text: str, api_payload: dict) -> list:
        city          = api_payload.get("city", "")
        city_normalized = city.strip().title()
        check_in      = api_payload.get("arrival_date", "")
        check_out     = api_payload.get("departure_date", "")
        adults        = int(api_payload.get("adults", 1))
        currency      = api_payload.get("currency", "EUR")
 
        nights = 3
        try:
            if check_in and check_out:
                nights = max(
                    (datetime.strptime(check_out, "%Y-%m-%d") -
                     datetime.strptime(check_in,  "%Y-%m-%d")).days, 1
                )
        except Exception:
            pass
 
        rag_hash  = hashlib.sha256(rag_text[:2000].encode()).hexdigest()[:16]
        cache_key = f"rag_parse_{city}_{rag_hash}"
 
        if cache_key in _llm_cache:
            print(f"[RAGParser] Cache hit → {len(_llm_cache[cache_key])} hôtels")
            return _llm_cache[cache_key]
 
        prompt = f"""Tu reçois un texte brut issu d'une base de données hôtelière.
Extrais les hôtels présents dans ce texte et retourne UNIQUEMENT ce JSON (sans backticks) :
 
{{MISSION : Extraire UNIQUEMENT les hôtels situés dans cette ville :
- Ville recherchée : {city_normalized}
 
⚠️ RÈGLE CRITIQUE :
Un hôtel correspond UNIQUEMENT si sa ville correspond à "{city_normalized}"
(ex: "Dubai", "Dubaï", "DUBAI" → tous valides pour "{city_normalized}")
Ne fais pas de correspondance stricte sur la casse ou les accents.
(ex: "Tunis", "Tunis City", "Tunis Centre" → tous valides pour "Tunis")
 
Si AUCUN hôtel du texte n'est dans cette ville → retourne {{"hotels": []}}
Ne retourne JAMAIS un hôtel situé dans une autre ville.
 
"hotels": [
    {{
    "id": "valeur trouvée dans le texte ou null",
    "name": "nom exact trouvé dans le texte",
    "stars": 0,
    "rating": 0,
    "rating_count": 0,
    "address": "",
    "city": "",
    "country": "",
    "images": [],
    "total_price": 0,
    "token": "",
    "offers": [
        {{
        "id": "",
        "name": "",
        "board_code": "",
        "board_type": "",
        "price": 0,
        "quantity": 0,
        "adults": 0
        }}
    ]
    }}
]
}}
 
RÈGLES ABSOLUES :
- Mets uniquement les valeurs que tu vois dans le texte
- Si un champ est absent du texte → laisse 0, "", [] ou null selon le type
- N'invente AUCUNE valeur (pas de prix estimés, pas de noms génériques)
- prix necessaire pour valider un hôtel (si pas de prix → ignore l'hôtel)
- Max 3 hôtels, max 2 offres par hôtel
 
TEXTE SOURCE :
{rag_text[:2000]}"""
 
        try:
            raw = await self._call_openai(prompt, timeout=60.0)
 
            for fence in ["```json", "```"]:
                raw = raw.replace(fence, "")
            raw = raw.strip()
 
            mapping    = json.loads(raw)
            hotels_raw = mapping.get("hotels", [])
            print(f"[RAGParser] {len(hotels_raw)} hôtels extraits du RAG")
 
            result = await self._build_frontend_hotels(hotels_raw, api_payload, nights)
 
            if result:
                _llm_cache[cache_key] = result
            else:
                print(f"[RAGParser] 0 hôtels extraits pour {city} → pas de cache")
 
            return result
 
        except asyncio.TimeoutError:
            print("[RAGParser] Timeout")
            return []
        except Exception as e:
            print(f"[RAGParser] Erreur: {e}")
            return []
 

    async def _resolve_llm_only(self, entities: dict, prompt_override: str = "") -> dict:
        locs   = entities.get("LOC",   [])
        dates  = entities.get("DATE",  [])
        prices = entities.get("PRICE", [])
 
        city      = locs[0]   if locs             else ""
        check_in  = dates[0]  if dates             else ""
        check_out = dates[1]  if len(dates) > 1   else ""
        adults    = int(prices[0]) if prices and str(prices[0]).isdigit() else 1
 
        api_payload = {
            "city":           city,
            "arrival_date":   check_in,
            "departure_date": check_out,
            "adults":         adults,
            "currency":       self.tenant_config.get("currency", "EUR"),
        }
 
        if prompt_override:
            try:
                replacements = {
                    "{city}":     city      or "non spécifié",
                    "{check_in}": check_in  or "non spécifiée",
                    "{check_out}":check_out or "non spécifiée",
                    "{adults}":   str(adults),
                    "{currency}": self.tenant_config.get("currency", "EUR"),
                    "{language}": self.language or "fr",
                }
                final_prompt = prompt_override
                for k, v in replacements.items():
                    final_prompt = final_prompt.replace(k, v)
 
                raw = await self._call_openai(final_prompt, timeout=60.0)
 
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                result = json.loads(raw.strip())
 
                hotels_list = []
                if isinstance(result, dict):
                    hotels_list = result.get("results") or result.get("hotels") or []
 
                return {
                    "status":     "llm_only",
                    "source":     "GPT-4o-mini Custom",
                    "type":       "hotel_search_results",
                    "hotels":     hotels_list,
                    "results":    hotels_list,
                    "query":      result.get("query", {}),
                    "user_id":    self.user_id,
                    "session_id": self.session_id,
                }
            except Exception as e:
                print(f"[StayAgent] Prompt personnalisé échoué: {e} → fallback système")
 
        query = f"hôtels à {city} du {check_in} au {check_out} pour {adults} personnes"
        gemini_result = await self._gpt_fallback(api_payload, query)
 
        hotels_list = []
        if isinstance(gemini_result, dict):
            hotels_list = gemini_result.get("results") or gemini_result.get("hotels") or []
 
        return {
            "status":     "llm_only",
            "source":     "Gemini",
            "type":       "hotel_search_results",
            "hotels":     hotels_list,
            "results":    hotels_list,
            "query":      gemini_result.get("query", {}),
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

    async def _load_api_keys(self, tenant_id: str) -> dict:
        try:
            from core.database import get_pool
            pool = await get_pool()
            rows = await pool.fetch(
                """
                SELECT label, api_key, api_url, http_method,
                       payload_template, headers_template, result_path,
                       param_mapping, timeout_ms, retry_count
                FROM tenant_api_keys
                WHERE tenant_id = $1
                  AND is_active = TRUE
                  AND agent_type = $2
                ORDER BY priority ASC
                """,
                uuid.UUID(tenant_id),
                self.agent_type,
            )
            
            print(f"[StayAgent] {len(rows)} API keys trouvées")
            for row in rows:
                print(f"  - {row['label']} ({row['http_method']})")
            
            return {
                r["label"]: {
                    "key": r["api_key"],
                    "url": r["api_url"],
                    "http_method": r["http_method"],
                    "payload_template": r["payload_template"],
                    "headers_template": r["headers_template"],
                    "result_path": r["result_path"],
                    "param_mapping": r["param_mapping"],
                    "timeout_ms": r.get("timeout_ms", 15000),
                    "retry_count": r.get("retry_count", 1),
                }
                for r in rows
            }
        except Exception as e:
            print(f"[StayAgent] Erreur chargement API keys: {e}")
            return {}

    def _fill_template(self, template: Any, values: dict) -> Any:
        if isinstance(template, dict):
            return {k: self._fill_template(v, values) for k, v in template.items()}
        if isinstance(template, list):
            return [self._fill_template(i, values) for i in template]
        if isinstance(template, str):
            stripped = template.strip()
            if stripped.startswith('{') and stripped.endswith('}') and stripped.count('{') == 1:
                key = stripped[1:-1]
                if key in values and values[key] is not None:
                    return values[key]
            
            result = template
            for k, v in values.items():
                if v is not None:
                    placeholder = f"{{{k}}}"
                    if placeholder in result:
                        str_v = json.dumps(v) if isinstance(v, (list, dict)) else str(v)
                        result = result.replace(placeholder, str_v)
            
            stripped = result.strip()
            if stripped.startswith(('[', '{')):
                try:
                    return json.loads(stripped)
                except:
                    pass
            return result
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
        
        if transform == "city_to_mygo_id":
            city_mapping_mygo = {
                "hammamet": "10",
                "nabeul": "11",
                "kelibia": "12",
                "korba": "13",
                "korbous": "14",
                "zaghouan": "59",
                "sousse": "34",
                "mahdia": "35",
                "monastir": "37",
                "el jem": "6482",
                "djerba": "18",
                "zarzis": "19",
                "mednenine": "76",
                "douz": "20",
                "kebili": "22",
                "ksar ghilane": "23",
                "tozeur": "47",
                "nefta": "75",
                "ain drahem": "31",
                "bizerte": "48",
                "tabarka": "33",
                "kairouan": "17",
                "sbeitla": "72",
                "sidi bouzid": "74",
                "le kef": "49",
                "téboursouk": "71",
                "nefza": "6484",
                "béja": "6487",
                "gafsa": "54",
                "gabes": "55",
                "tataouine": "70",
                "matmata": "73",
                "tunis": "32",
                "carthage": "32",
                "gammarth": "6485",
                "sfax": "39",
                "kerkennah": "6483",
                "istanbul": "6488",
                "esen yurt": "6489",
            }
            
            result = city_mapping_mygo.get(value.lower().strip())
            if result is None:
                print(f"[StayAgent] Ville '{value}' non supportée par MyGO → fallback nécessaire")
            return result
        
        if transform == "stars_format":
            numbers = re.findall(r'\d+', value)
            if numbers:
                stars_list = [int(n) for n in numbers]
                return json.dumps(stars_list)
            return "[]"
        
        if transform == "build_rooms_config":
            try:
                if isinstance(value, (list, tuple)):
                    rooms = []
                    for room in value:
                        if isinstance(room, dict):
                            if "Adult" in room:
                                rooms.append(room)
                            else:
                                rooms.append({"Adult": int(room.get("adults", 2))})
                        elif isinstance(room, (int, str)):
                            rooms.append({"Adult": int(room)})
                    return json.dumps(rooms) if rooms else '[{"Adult": 2}]'
                elif isinstance(value, (int, float)):
                    return json.dumps([{"Adult": int(value)}])
                elif isinstance(value, str):
                    if value.startswith('['):
                        return value
                    try:
                        n = int(value)
                        return json.dumps([{"Adult": n}])
                    except ValueError:
                        pass
                return '[{"Adult": 2}]'
            except Exception as e:
                print(f"[StayAgent] Error building rooms_config: {e}")
                return '[{"Adult": 2}]'

        if transform.startswith("map:"):
            mapping = {k.strip().lower(): v.strip()
                    for pair in transform[4:].split(",")
                    if "=" in pair for k, v in [pair.split("=", 1)]}
            return mapping.get(value.lower().strip(), value[:3].upper())
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

        entity_lists = {
            "LOC": [self._clean_city(v) for v in entities.get("LOC", [])],
            "DATE": entities.get("DATE", []),
            "PRICE": entities.get("PRICE", []),
            "ORG": entities.get("ORG", []),
            "FLIGHT": entities.get("FLIGHT", []),
            "PERSONS": entities.get("PERSONS", []),
            "MISC": entities.get("MISC", []),
            "SORT_BY": entities.get("SORT_BY", []),
            "ORDER": entities.get("ORDER", [])
        }

        for entity_key, mapping_config in param_mapping.items():
            if isinstance(mapping_config, dict):
                api_param = mapping_config.get("param")
                transform = mapping_config.get("transform")
                is_optional = mapping_config.get("optional", False)
                default_value = mapping_config.get("default")
            else:
                api_param = mapping_config
                transform = None
                is_optional = False
                default_value = None

            if not api_param:
                continue

            if is_optional and default_value is not None:
                result[api_param] = default_value

            m = re.match(r'^(\w+)(?:\[(\d+)\])?(?:\.(\w+))?$', entity_key)
            if not m:
                if entity_key in entity_lists and entity_lists[entity_key]:
                    value = entity_lists[entity_key][0]
                    if transform:
                        value = self._apply_transform(str(value), transform)
                    result[api_param] = value
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
            elif index is None and values:
                value = values[0]

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

        dest_id = str(api_payload.get("dest_id") or "").strip()

        if method == "GET":
            if not dest_id:
                print(f"[StayAgent] {label} GET → dest_id vide")
                return False

        elif method == "POST":
            if not dest_id:
                print(f"[StayAgent] {label} POST → dest_id vide")
                return False

        elif method in ("PUT", "DELETE"):
            booking_ref = str(api_payload.get("booking_reference") or "").strip()
            if not booking_ref:
                print(f"[StayAgent] {label} {method} → booking_reference manquant")
                return False

        return True

    async def _gpt_fallback(self, api_payload: dict, query: str, steps: list = None) -> dict:
        city = api_payload.get("city", "")
        arrival_date = api_payload.get("arrival_date", "")
        departure_date = api_payload.get("departure_date", "")
        adults = api_payload.get("adults", 1)
        stars = api_payload.get("stars")
        price_min = api_payload.get("price_min")
        price_max = api_payload.get("price_max")
        currency = self.tenant_config.get("currency", "EUR") if self.tenant_config else "EUR"
        nights = 3
        try:
            from datetime import datetime
            if arrival_date and departure_date:
                check_in = datetime.strptime(arrival_date, "%Y-%m-%d")
                check_out = datetime.strptime(departure_date, "%Y-%m-%d")
                nights = max((check_out - check_in).days, 1)
        except:
            nights = 3
        cache_key = hashlib.sha256(f"{city}{arrival_date}{departure_date}".encode()).hexdigest()[:16]
        if cache_key in _llm_cache:
            return _llm_cache[cache_key]

        prompt = f"""
        Tu es un expert en hébergements hôteliers.
        Génère des hôtels RÉALISTES, COHÉRENTS et STRUCTURÉS pour une API de réservation.

        RECHERCHE UTILISATEUR :
        - Destination : {city if city else 'destination non spécifiée'}
        - Date arrivée : {arrival_date if arrival_date else 'non spécifiée'}
        - Date départ : {departure_date if departure_date else 'non spécifiée'}
        - Voyageurs : {adults}
        - Étoiles : {stars if stars else 'tous'}
        - Budget par nuit : {f'entre {price_min}{currency} et {price_max}{currency}' if price_min or price_max else 'non spécifié'}
        - Devise : {currency}

        INSTRUCTIONS IMPORTANTES :
        1. Génère des hôtels RÉELS ou ULTRA-RÉALISTES
        2. Respecte la cohérence prix / étoiles / standing
        3. Génère 4 ou 5 hôtels
        4. Chaque hôtel DOIT contenir une liste "offers" 2 offres au maximum
        5. NE JAMAIS utiliser de formules (ex: price * nights)
        6. Tous les champs numériques doivent être des VALEURS FIXES

        ICÔNES AMENITIES AUTORISÉES :
        wifi, parking, breakfast, pool, spa, gym, restaurant, bar, luggage

        ========================================================
        FORMAT JSON STRICT (OBLIGATOIRE)
        ========================================================

        {{
            "type": "hotel_search_results",

            "query": {{
                "city": "{city}",
                "check_in": "{arrival_date}",
                "check_out": "{departure_date}",
                "guests": {adults},
                "rooms": 1,
                "nights": {nights}
            }},

            "results": [
                {{
                    "id": "hotel_1",
                    "type": "hotel",
                    "name": "Hotel Example Name",
                    "category": "Luxe",
                    "stars": 4,

                    "location": {{
                        "city": "{city}",
                        "country": "Country Name",
                        "address": "Realistic address"
                    }},

                    "rating": 4.3,
                    "rating_count": 1200,

                    "price_per_night": 120,
                    "total_price": 360,
                    "currency": "{currency}",
                    "nights": {nights},

                    "images": [
                        "https://images.unsplash.com/photo-1",
                        "https://images.unsplash.com/photo-2"
                    ],

                    "amenities": [
                        {{"icon": "wifi", "name": "Wifi gratuit", "included": true}},
                        {{"icon": "breakfast", "name": "Petit-déjeuner", "included": true}},
                        {{"icon": "parking", "name": "Parking", "included": true}},
                        {{"icon": "pool", "name": "Piscine", "included": false, "price": 15}}
                    ],

                    "offers": [
                        {{
                            "id": "offer_1",
                            "name": "Chambre Standard",
                            "board_type": "BB",
                            "board_code": "BB",
                            "description": "Chambre confortable avec petit-déjeuner inclus",
                            "price": 360,
                            "price_per_night": 120,
                            "currency": "{currency}",
                            "quantity": 5,
                            "adults": {adults},
                            "children": [],
                            "cancellation_deadline": "2026-05-01",
                            "stop_reservation": false
                        }},
                        {{
                            "id": "offer_2",
                            "name": "Chambre Supérieure",
                            "board_type": "HB",
                            "board_code": "HB",
                            "description": "Chambre plus spacieuse avec demi-pension",
                            "price": 480,
                            "price_per_night": 160,
                            "currency": "{currency}",
                            "quantity": 3,
                            "adults": {adults},
                            "children": [],
                            "cancellation_deadline": "2026-05-01",
                            "stop_reservation": false
                        }}
                    ],

                    "best_offer": {{
                        "id": "offer_1",
                        "name": "Chambre Standard",
                        "price": 360,
                        "price_per_night": 120
                    }},

                    "price_range": {{
                        "min": 100,
                        "max": 650
                    }},

                    "recommended": 85,

                    "source": "Gemini AI",

                    "actions": [
                        {{
                            "label": "Voir détails",
                            "action": "details",
                            "url": "/hotel/hotel_1"
                        }},
                        {{
                            "label": "Réserver",
                            "action": "book",
                            "url": "/book/hotel_1"
                        }}
                    ]
                }}
            ]
        }}

        RÈGLES IMPORTANTES : - Utilise des noms d'hôtels COHÉRENTS avec la ville
        - Les prix doivent être en {currency}
        - Les notes doivent être entre 3.0 et 5.0
        - Adapte les équipements au standing (hôtel de luxe → plus d'équipements)
        - Réponds UNIQUEMENT en JSON GÉNÈRE DES HÔTELS PERTINENTS POUR {city if city else 'cette destination'}
        """

        try:
            raw = await self._call_openai(prompt, timeout=60.0)
            
            for fence in ["```json", "```"]:
                raw = raw.replace(fence, "")
            raw = raw.strip()
            if raw.startswith("json"):
                raw = raw[4:]

            result = json.loads(raw.strip())
            
            if result.get("type") != "hotel_search_results":
                result["type"] = "hotel_search_results"
            
            if "results" not in result or not isinstance(result["results"], list):
                result["results"] = []
            
            cleaned_results = []
            for hotel in result.get("results", []):
                if isinstance(hotel, dict):
                    cleaned_hotel = {
                        "id": hotel.get("id", f"hotel_{len(cleaned_results)}"),
                        "type": "hotel",
                        "name": hotel.get("name", "Hôtel"),
                        "category": hotel.get("category", ""),
                        "stars": hotel.get("stars", 0),
                        "rating": hotel.get("rating", 0),
                        "rating_count": hotel.get("rating_count", 0),
                        "location": {
                            "city": hotel.get("location", {}).get("city", api_payload.get("city", "")),
                            "country": hotel.get("location", {}).get("country", ""),
                            "address": hotel.get("location", {}).get("address", "")
                        },
                        "price_per_night": hotel.get("price_per_night", 0),
                        "total_price": hotel.get("total_price", 0),
                        "currency": api_payload.get("currency", "EUR"),
                        "images": hotel.get("images", []),
                        "amenities": hotel.get("amenities", []),
                        "offers": hotel.get("offers", []),
                        "actions": hotel.get("actions", [])
                    }
                    cleaned_results.append(cleaned_hotel)
            
            result["results"] = cleaned_results
            result["hotels"] = cleaned_results
            
            _llm_cache[cache_key] = result
            return result

        except Exception as e:
            print(f"[Gemini Hotel] Erreur: {e}")
            return self._generate_dynamic_fallback_hotels(city, arrival_date, departure_date, adults, stars, price_min, price_max)

    async def _gemini_parse_response(self, raw_data, api_payload: dict) -> list:
        try:
            cache_key = hashlib.sha256(
                json.dumps(raw_data, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()[:16]
        except Exception:
            cache_key = None
 
        if cache_key and cache_key in _llm_cache:
            cached = _llm_cache[cache_key]
            print(f"[GeminiParser] Cache hit → {len(cached)} hôtels")
            return cached
 
        if isinstance(raw_data, list):
            print(f"[GeminiParser] raw_data est une liste de {len(raw_data)} éléments → wrap")
            raw_data = {"hotels": raw_data}
 
        city      = api_payload.get("city", "")
        check_in  = api_payload.get("arrival_date", "")
        check_out = api_payload.get("departure_date", "")
        adults    = api_payload.get("adults", 1)
 
        nights = 3
        try:
            if check_in and check_out:
                nights = max(
                    (datetime.strptime(check_out, "%Y-%m-%d") -
                     datetime.strptime(check_in,  "%Y-%m-%d")).days, 1
                )
        except Exception:
            nights = 3
 
        def _truncate(data: dict, max_hotels: int = 5) -> dict:
            for key in ["hotels", "data", "results", "items", "HotelSearch",
                        "response", "HotelList", "hotelOffers", "properties"]:
                if key in data and isinstance(data[key], list) and len(data[key]) > max_hotels:
                    data = dict(data)
                    data[key] = data[key][:max_hotels]
                    print(f"[GeminiParser] Tronqué: → {max_hotels} hôtels")
                    return data
            for key, val in data.items():
                if isinstance(val, list) and len(val) > max_hotels and val and isinstance(val[0], dict):
                    data = dict(data)
                    data[key] = val[:max_hotels]
                    print(f"[GeminiParser] Tronqué sous '{key}'")
                    return data
            return data
 
        raw_data  = _truncate(raw_data, max_hotels=5)
        full_json = json.dumps(raw_data, ensure_ascii=False)
        if len(full_json) > 8000:
            full_json = full_json[:8000] + "..."
 
        prompt = f"""Tu es un expert en APIs hôtelières. Analyse cette réponse et extrais les hôtels.
 
RÉPONSE API :
{full_json}
 
CONTEXTE :
- Ville : {city} | Nuits : {nights} | Adultes : {adults}
- Check-in : {check_in} / Check-out : {check_out}
 
MISSION : Extrais TOUS les hôtels et retourne ce JSON strict (sans texte, sans backticks) :
{{
"currency": "devise trouvée ou ''",
"hotels": [
    {{
    "id": "identifiant unique",
    "name": "nom de l'hôtel",
    "stars": 4,
    "rating": 4.2,
    "rating_count": 850,
    "address": "adresse ou ''",
    "city": "ville ou '{city}'",
    "country": "pays ou ''",
    "images": ["url1", "url2"],
    "total_price": 450.0,
    "token": "token de réservation ou ''",
    "offers": [
        {{
        "id": "id offre",
        "name": "nom chambre",
        "board_type": "type pension",
        "board_code": "BB/HB/FB/AI/RO",
        "price": 450.0,
        "price_per_night": 150.0,
        "cancellation_deadline": "2026-05-01 ou ''",
        "quantity": 5,
        "adults": 2,
        "stop_reservation": false
        }}
    ]
    }}
]
}}
 
RÈGLES GÉNÉRALES :
- Cherche les hôtels dans TOUTE la structure, quelle que soit la profondeur
- Cherche les offres sous : offers, rooms, rates, roomTypes, packages, plans, Boarding, Pax, RoomOffers, fares...
- N'invente rien — extrais uniquement ce qui existe
 
PRIX :
- Cherche : price, amount, fare, rate, total, totalAmount, Price, Amount, Rate, BasePrice, fare_amount...
- Prix en centimes/nanos → divise par 100 ou 1e9 selon le contexte
- prix_total = prix_par_nuit × {nights} si seulement le prix nuit est disponible
- Si plusieurs prix dans une offre, prendre le prix total (le plus grand)
- Tous les prix en float arrondi à 2 décimales
 
OFFRES :
- Cherche board_code dans : board_code, boardCode, MealPlan, meal_plan, BoardBasis, ratePlan, board_type...
Codes standards : RO=Room Only, BB=Bed&Breakfast, HB=Half Board, FB=Full Board, AI=All Inclusive
- Si board_type est long (ex: "Bed and Breakfast") → déduire le code (BB)
- name chambre : cherche name, roomName, roomType, description, RoomName, TypeName...
- quantity : cherche quantity, availability, allotment, available_rooms, Quantity...
- stop_reservation : true si StopReservation=true ou stop=true ou closed=true
 
IMAGES :
- Toutes les URLs trouvées (max 5), complète les URLs relatives avec le domaine de base
- Cherche : image, images, photo, photos, Image, thumbnail, photoUrls, MainPhoto...
 
RATING :
- Note réelle (ex: 8.5/10 → 4.25, 4.2/5 → 4.2) ou 0 si absente
- rating_count : nombre d'avis réel ou 0
 
AUTRES :
- cancellation_deadline : date ISO si trouvée sinon ''
- token : cherche Token, token, bookingToken, property_id, hotelCode...
- Max 2 offres par hôtel (les 2 moins chères si plus)"""
 
        try:
            raw = await self._call_openai(prompt, timeout=60.0)
 
            for fence in ["```json", "```"]:
                raw = raw.replace(fence, "")
            raw = raw.strip()
            raw = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw)
 
            mapping = json.loads(raw)
            print(f"[GeminiParser] Mapping: {len(mapping.get('hotels', []))} hôtels, devise: {mapping.get('currency')}")
 
        except asyncio.TimeoutError:
            print("[GeminiParser] Timeout 60s → liste vide")
            return []
        except Exception as e:
            print(f"[GeminiParser] GPT-4o-mini échoué: {e}")
            return []
 
        detected_currency = mapping.get("currency") or api_payload.get("currency") or "EUR"
        all_hotels_raw    = mapping.get("hotels", [])
        print(f"[GeminiParser] {len(all_hotels_raw)} hôtels extraits")
 
        def _build_amenities(stars: int) -> list:
            base = [
                {"icon": "wifi",      "name": "WiFi gratuit",   "included": True},
                {"icon": "breakfast", "name": "Petit-déjeuner", "included": True},
                {"icon": "parking",   "name": "Parking",        "included": True},
            ]
            if stars >= 4:
                base.append({"icon": "pool", "name": "Piscine", "included": True})
            if stars >= 5:
                base += [
                    {"icon": "spa", "name": "Spa",            "included": True},
                    {"icon": "gym", "name": "Salle de sport", "included": True},
                ]
            return base
 
        def _safe_float(val) -> float:
            try:    return float(str(val).replace(",", "."))
            except: return 0.0
 
        frontend_hotels = []
        for i, h in enumerate(all_hotels_raw):
            if not isinstance(h, dict):
                continue
 
            stars       = int(h.get("stars") or 3)
            total_price = _safe_float(h.get("total_price") or 0)
            ppn         = round(total_price / nights, 2) if nights > 0 and total_price > 0 else 0.0
            if nights > 1 and total_price > 0:
                if abs(ppn - total_price) < 0.01:
                    ppn         = total_price
                    total_price = round(ppn * nights, 2)
            raw_rating  = _safe_float(h.get("rating") or 0)
            if raw_rating > 5:   raw_rating = round(raw_rating / 2, 1)
            if raw_rating == 0:  raw_rating = {3: 3.5, 4: 4.0, 5: 4.5}.get(stars, 3.5)
            rating_count = int(h.get("rating_count") or 0)
 
            raw_offers = h.get("offers") or []
            if not raw_offers and total_price > 0:
                raw_offers = [{
                    "id": f"offer_{i}_0",
                    "name": "Chambre Standard",
                    "board_type": "Chambre seule",
                    "board_code": "RO",
                    "price": total_price,
                    "quantity": 5,
                    "stop_reservation": False,
                }]
 
            built_offers = []
            for j, o in enumerate(raw_offers[:2]):
                if not isinstance(o, dict):
                    continue
 
                op = _safe_float(
                    o.get("price") or o.get("amount") or
                    o.get("fare")  or o.get("rate")   or
                    o.get("total") or o.get("totalAmount") or
                    total_price or 0
                )
                if op > 0 and total_price > 0 and op > total_price * 5:
                    op = round(op / 100, 2)
 
                opn = _safe_float(o.get("price_per_night") or 0) or (
                    round(op / nights, 2) if nights > 0 and op > 0 else op
                )
 
                board_code = o.get("board_code") or ""
                board_type = o.get("board_type") or ""
                if not board_code and board_type:
                    _board_map = {
                        "bed and breakfast": "BB", "breakfast": "BB",
                        "half board": "HB", "demi-pension": "HB",
                        "full board": "FB", "pension complète": "FB",
                        "all inclusive": "AI", "tout inclus": "AI",
                        "room only": "RO", "chambre seule": "RO",
                        "self catering": "SC",
                    }
                    board_code = _board_map.get(board_type.lower().strip(), "RO")
 
                built_offers.append({
                    "id":                    str(o.get("id") or f"offer_{i}_{j}"),
                    "name":                  o.get("name") or "Chambre Standard",
                    "board_type":            board_type,
                    "board_code":            board_code or "RO",
                    "description":           o.get("description") or f"{o.get('name', 'Chambre')} - {board_type}",
                    "price":                 op,
                    "price_per_night":       opn,
                    "price_with_markup":     op,
                    "currency":              detected_currency,
                    "quantity":              int(o.get("quantity") or 5),
                    "adults":                int(o.get("adults") or adults),
                    "children":              list(o.get("children") or []),
                    "cancellation_deadline": o.get("cancellation_deadline") or "",
                    "stop_reservation":      bool(o.get("stop_reservation", False)),
                    "selected":              False,
                    "recommended":           j == 0,
                })
 
            if not built_offers:
                print(f"[GeminiParser] Hôtel '{h.get('name')}' ignoré → aucune offre")
                continue
 
            if total_price == 0 and built_offers:
                total_price = built_offers[0]["price"]
                ppn         = built_offers[0]["price_per_night"]
 
            raw_images = h.get("images") or []
            if not raw_images and h.get("image"):
                raw_images = [h["image"]]
            images    = [u for u in raw_images if isinstance(u, str) and u.startswith("http")][:5]
            image_url = images[0] if images else ""
 
            hotel = {
                "id":              str(h.get("id") or f"hotel_{i}"),
                "type":            "hotel",
                "name":            h.get("name") or "Hôtel",
                "stars":           stars,
                "rating":          raw_rating,
                "rating_count":    rating_count,
                "currency":        detected_currency,
                "price_per_night": ppn,
                "total_price":     total_price,
                "nights":          nights,
                "location": {
                    "city":    h.get("city")    or city,
                    "country": h.get("country") or "",
                    "address": h.get("address") or "",
                },
                "city":       h.get("city")    or city,
                "address":    h.get("address") or "",
                "image":      image_url,
                "images":     images,
                "token":      h.get("token") or "",
                "amenities":  _build_amenities(stars),
                "offers":     built_offers,
                "best_offer": built_offers[0] if built_offers else None,
                "price_range": {
                    "min": total_price,
                    "max": round(total_price * 1.3, 2),
                },
                "source":      "API",
                "recommended": 90 - (i * 5),
                "actions": [
                    {"label": "Voir détails", "action": "details",
                     "url": f"/hotel/{h.get('id', i)}"},
                    {"label": "Réserver",     "action": "book",
                     "url": f"/book/hotel/{h.get('id', i)}"},
                ],
            }
            frontend_hotels.append(hotel)
 
        print(f"[GeminiParser] {len(frontend_hotels)} hôtels construits pour le frontend")
        for h in frontend_hotels:
            print(f"  - {h['name']}: {len(h['offers'])} offres | {h['total_price']} {h['currency']}")
 
        if cache_key:
            _llm_cache[cache_key] = frontend_hotels
 
        return frontend_hotels

    def _generate_dynamic_fallback_hotels(self, city: str, arrival_date: str, departure_date: str,
                                        adults: int, stars: str, price_min: str, price_max: str,
                                        currency: str = "EUR", nights: int = 3) -> dict:
        city_lower = city.lower() if city else ""
        
        real_hotels_db = {
            "paris": [
                {"name": "Hôtel Plaza Athénée", "category": "Luxe", "stars": 5, "address": "25 Avenue Montaigne, 75008 Paris", "rating": 4.8},
                {"name": "Le Meurice", "category": "Luxe", "stars": 5, "address": "228 Rue de Rivoli, 75001 Paris", "rating": 4.7},
                {"name": "Hôtel Regina Louvre", "category": "Luxe", "stars": 4, "address": "2 Place des Pyramides, 75001 Paris", "rating": 4.6},
                {"name": "Novotel Paris Centre", "category": "Affaires", "stars": 4, "address": "8 Place Marguerite de Navarre, 75001 Paris", "rating": 4.2},
                {"name": "Ibis Paris Gare du Nord", "category": "Économique", "stars": 3, "address": "6 Rue de Dunkerque, 75010 Paris", "rating": 3.8}
            ],
            "london": [
                {"name": "The Ritz London", "category": "Luxe", "stars": 5, "address": "150 Piccadilly, London W1J 9BR", "rating": 4.9},
                {"name": "The Savoy", "category": "Luxe", "stars": 5, "address": "Strand, London WC2R 0EZ", "rating": 4.8},
                {"name": "Hilton London Bankside", "category": "Affaires", "stars": 4, "address": "2-8 Great Suffolk St, London SE1 0UG", "rating": 4.4}
            ],
            "rome": [
                {"name": "Hotel Eden", "category": "Luxe", "stars": 5, "address": "Via Ludovisi, 49, 00187 Roma", "rating": 4.7},
                {"name": "Rome Cavalieri", "category": "Luxe", "stars": 5, "address": "Via Alberto Cadlolo, 101, 00136 Roma", "rating": 4.6},
                {"name": "Hotel Artemide", "category": "Luxe", "stars": 4, "address": "Via Nazionale, 22, 00184 Roma", "rating": 4.5}
            ],
            "barcelona": [
                {"name": "Hotel Arts Barcelona", "category": "Luxe", "stars": 5, "address": "Carrer de la Marina, 19, 08005 Barcelona", "rating": 4.7},
                {"name": "Majestic Hotel & Spa", "category": "Luxe", "stars": 5, "address": "Passeig de Gràcia, 68, 08007 Barcelona", "rating": 4.6}
            ],
            "default": [
                {"name": "Grand Plaza Hotel", "category": "Luxe", "stars": 5, "address": "City Center", "rating": 4.5},
                {"name": "Central Park Hotel", "category": "Affaires", "stars": 4, "address": "Downtown", "rating": 4.2},
                {"name": "City Comfort Inn", "category": "Économique", "stars": 3, "address": "City Center", "rating": 3.8}
            ]
        }
        
        city_prices = {
            "paris": {"min": 120, "max": 450},
            "london": {"min": 130, "max": 500},
            "rome": {"min": 100, "max": 350},
            "barcelona": {"min": 90, "max": 300},
            "berlin": {"min": 80, "max": 250},
            "amsterdam": {"min": 100, "max": 320},
            "default": {"min": 70, "max": 200}
        }
        
        exchange_rates = {"EUR": 1.0, "USD": 1.09, "GBP": 0.86, "JPY": 165.0, "CAD": 1.48, "CHF": 0.96, "AED": 4.0, "MAD": 10.8, "TND": 3.4}
        rate = exchange_rates.get(currency, 1.0)
        
        hotels_data = real_hotels_db.get(city_lower, real_hotels_db["default"])
        price_range = city_prices.get(city_lower, city_prices["default"])
        
        min_price = int(price_range["min"] * rate)
        max_price = int(price_range["max"] * rate)
        
        if price_min:
            min_price = max(min_price, int(float(price_min) * rate))
        if price_max:
            max_price = min(max_price, int(float(price_max) * rate))
        
        hotels = []
        for i, hotel_data in enumerate(hotels_data[:4]):
            price_factor = 1.0 - (i * 0.1)
            price_per_night = int((min_price + (max_price - min_price) * (i / 4)) * price_factor)
            price_per_night = max(price_per_night, int(min_price * 0.7))
            
            total_price = price_per_night * nights
            
            offers = [
                {
                    "id": f"offer_{i}_1",
                    "name": "Chambre Standard",
                    "board_type": "Petit-déjeuner inclus",
                    "board_code": "BB",
                    "description": "Chambre confortable avec petit-déjeuner buffet",
                    "price": total_price,
                    "price_per_night": price_per_night,
                    "currency": currency,
                    "quantity": 5,
                    "adults": adults,
                    "children": [],
                    "cancellation_deadline": "",
                    "stop_reservation": False
                },
                {
                    "id": f"offer_{i}_2",
                    "name": "Chambre Supérieure",
                    "board_type": "Demi-pension",
                    "board_code": "HB",
                    "description": "Chambre spacieuse avec dîner inclus",
                    "price": int(total_price * 1.3),
                    "price_per_night": int(price_per_night * 1.3),
                    "currency": currency,
                    "quantity": 3,
                    "adults": adults,
                    "children": [],
                    "cancellation_deadline": "",
                    "stop_reservation": False
                }
            ]
            
            if hotel_data["stars"] >= 5:
                amenities = [
                    {"icon": "wifi", "name": "Wifi gratuit", "included": True},
                    {"icon": "breakfast", "name": "Petit-déjeuner", "included": True},
                    {"icon": "pool", "name": "Piscine", "included": True},
                    {"icon": "spa", "name": "Spa", "included": True},
                    {"icon": "gym", "name": "Salle de sport", "included": True},
                    {"icon": "restaurant", "name": "Restaurant gastronomique", "included": True}
                ]
            elif hotel_data["stars"] >= 4:
                amenities = [
                    {"icon": "wifi", "name": "Wifi gratuit", "included": True},
                    {"icon": "breakfast", "name": "Petit-déjeuner", "included": True},
                    {"icon": "pool", "name": "Piscine", "included": True},
                    {"icon": "gym", "name": "Salle de sport", "included": True}
                ]
            else:
                amenities = [
                    {"icon": "wifi", "name": "Wifi gratuit", "included": True},
                    {"icon": "breakfast", "name": "Petit-déjeuner", "included": False, "price": int(12 * rate)}
                ]
            
            hotels.append({
                "id": f"hotel_{i+1}",
                "type": "hotel",
                "name": hotel_data["name"],
                "category": hotel_data["category"],
                "stars": hotel_data["stars"],
                "location": {
                    "city": city if city else "Destination",
                    "country":"",
                    "address": hotel_data["address"]
                },
                "rating": hotel_data["rating"],
                "rating_count": 800 + (i * 100),
                "price_per_night": price_per_night,
                "total_price": total_price,
                "currency": currency,
                "nights": nights,
                "images": [
                    "https://images.unsplash.com/photo-1566073771259-6a8506099945?w=800&h=600&fit=crop",
                    "https://images.unsplash.com/photo-1582719478250-c89cae4dc85b?w=800&h=600&fit=crop"
                ],
                "amenities": amenities,
                "offers": offers,
                "best_offer": offers[0],
                "price_range": {
                    "min": total_price,
                    "max": int(total_price * 1.5)
                },
                "recommended": 85 - (i * 10),
                "source": "Gemini AI",
                "actions": [
                    {"label": "Voir détails", "action": "details", "url": f"/hotel/hotel_{i+1}"},
                    {"label": "Réserver", "action": "book", "url": f"/book/hotel_{i+1}"}
                ]
            })
        
        if not isinstance(hotels, list):
            hotels = []
            
        return {
            "type": "hotel_search_results",
            "query": {
                "city": city,
                "check_in": arrival_date,
                "check_out": departure_date,
                "guests": adults,
                "rooms": 1,
                "nights": nights
            },
            "hotels": hotels,
            "results": hotels,
        }

    async def _resolve_api(self, api_payload: dict, rag_query: str,
                            steps: list = None, result_key: str = "offers",
                            expected_method: str = None) -> dict:
        print(f"[DEBUG] _resolve_api appelé avec:")
        print(f"  - sub_category: {self.sub_category}")
        print(f"  - expected_method: {expected_method}")
        print(f"  - result_key: {result_key}")
        print(f"  - api_payload: {api_payload}")
        print(f"  - API keys disponibles: {list(self.api_keys.keys())}")

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
            print(f"[DEBUG] API trouvée avec key: {found_key}")
        else:
            print(f"[DEBUG] sub_category '{self.sub_category}' non trouvée dans api_keys")
            target_keys = {
                label: creds for label, creds in self.api_keys.items()
                if not expected_method or creds.get("http_method", "GET").upper() == expected_method.upper()
            }

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            
            for label, creds in target_keys.items():
                try:
                    method = creds.get("http_method", "GET").upper()
                    print(f"[DEBUG] Tentative avec API: {label} (méthode: {method})")

                    template = creds.get("payload_template") or {}
                    headers_template = creds.get("headers_template") or {}
                    result_path = creds.get("result_path") or result_key
                    param_mapping = creds.get("param_mapping")
                    timeout_ms = creds.get("timeout_ms", 15000)
                    retry_count = creds.get("retry_count", 1)

                    if isinstance(template, str):
                        try:
                            template = json.loads(template)
                        except:
                            template = {}
                            
                    if isinstance(headers_template, str):
                        try:
                            headers_template = json.loads(headers_template)
                        except:
                            headers_template = {}

                    context_values = {
                        "user_id": self.user_id,
                        "session_id": self.session_id,
                        "tenant_id": self.tenant_id,
                    }

                    dynamic_values = self._map_entities(
                        self.entities, param_mapping, api_payload, context=context_values
                    )
                    if not dynamic_values.get("city_id") and api_payload.get("city"):
                        city_id = self._apply_transform(
                            api_payload["city"].lower().strip(),
                            "city_to_mygo_id"
                        )
                        if city_id:
                            dynamic_values["city_id"] = city_id
                            print(f"[DEBUG] city_id ← api_payload.city: {city_id}")
                        else:
                            print(f"[DEBUG] Ville '{api_payload['city']}' non supportée par MyGO")
                    parsed_mapping_temp = param_mapping
                    if isinstance(parsed_mapping_temp, str):
                        try:
                            parsed_mapping_temp = json.loads(parsed_mapping_temp)
                        except:
                            parsed_mapping_temp = {}

                    for entity_key, mapping_config in (parsed_mapping_temp or {}).items():
                        if not isinstance(mapping_config, dict):
                            continue
                        api_param   = mapping_config.get("param")
                        default_val = mapping_config.get("default")
                        if not api_param:
                            continue
                        if not dynamic_values.get(api_param):
                            if api_payload.get(api_param) is not None:
                                dynamic_values[api_param] = api_payload[api_param]
                                print(f"[DEBUG] {api_param} ← api_payload: {api_payload[api_param]}")
                            elif default_val is not None:
                                dynamic_values[api_param] = default_val
                                print(f"[DEBUG] {api_param} ← default: {default_val}")

                    parsed_mapping = param_mapping
                    if isinstance(parsed_mapping, str):
                        try:
                            parsed_mapping = json.loads(parsed_mapping)
                        except:
                            parsed_mapping = {}

                    params_manquants = []
                    for entity_key, mapping_config in (parsed_mapping or {}).items():
                        if not isinstance(mapping_config, dict):
                            continue
                        if mapping_config.get("optional", False):
                            continue
                        api_param = mapping_config.get("param")
                        if api_param and not dynamic_values.get(api_param):
                            params_manquants.append(api_param)

                    if params_manquants:
                        print(f"[DEBUG] {label} → params obligatoires non résolus: {params_manquants} → skip")
                        continue

                    print(f"[DEBUG] Payload valide pour {label}")

                    adults = api_payload.get("adults", 2)
                    dynamic_values['rooms_config'] = [{"Adult": int(adults)}]

                    if 'stars' not in dynamic_values or not dynamic_values['stars']:
                        dynamic_values['stars'] = []

                    if 'keywords' not in dynamic_values:
                        dynamic_values['keywords'] = ''
                    
                    print(f"[DEBUG] dynamic_values après mapping: {dynamic_values}")

                    fill_values = {"api_key": creds.get("key"), **context_values,
                                **api_payload, **dynamic_values}

                    url = creds.get("url", "")
                    for k, v in fill_values.items():
                        if v is not None:
                            url = url.replace(f"{{{k}}}", str(v))

                    payload = self._fill_template(template, fill_values)
                    headers = self._fill_template(headers_template, fill_values) or {}

                    print(f"[DEBUG] URL finale: {url}")
                    print(f"[DEBUG] Payload final: {payload}")

                    def _clean(p):
                        if not p:
                            return {}
                        return {k: v for k, v in (p or {}).items()
                                if v is not None and not isinstance(v, (list, dict))}

                    response = None
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
                            else:
                                last_error = f"HTTP {response.status_code}"
                                
                        except httpx.TimeoutException as e:
                            last_error = f"Timeout: {e}"
                        except Exception as e:
                            last_error = str(e)

                        if attempt < retry_count - 1:
                            await asyncio.sleep(min(2 ** attempt, 10))

                    if not response or response.status_code >= 400:
                        print(f"[StayAgent] API {label} échouée: {last_error}")
                        return {
                            "status": "api_error",
                            "source": label,
                            "type": "info_results",
                            "message": "Le service de recherche d'hôtels est temporairement indisponible.",
                            "sections": [{
                                "title": "Erreur technique",
                                "content": "L'API de réservation hôtelière ne répond pas.",
                                "items": ["Veuillez réessayer plus tard ou contacter votre agence"]
                            }],
                            "actions": [{"label": "Contacter l'agence", "action": "contact"}],
                            "user_id": self.user_id,
                            "session_id": self.session_id,
                            "steps": steps,
                        }

                    try:
                        data = response.json()
                    except json.JSONDecodeError as e:
                        print(f"[StayAgent] {label}: JSON decode error: {e}")
                        return {
                            "status": "api_error",
                            "source": label,
                            "type": "info_results",
                            "message": "Erreur de formatage de la réponse API.",
                            "user_id": self.user_id,
                            "session_id": self.session_id,
                            "steps": steps,
                        }

                    result_data = self._extract_result(data, result_path)
                    print(f"[DEBUG] result_data extrait: {type(result_data)}")
                    if data:
                        print(f"[StayAgent] GPT-4o-mini Parser pour {label}")
                        parsed_results = await self._gemini_parse_response(data, api_payload)
                        if parsed_results:
                            print(f"[StayAgent] API {label}: {len(parsed_results)} hôtels parsés")
                            return {
                                "status": "success",
                                "source": label,
                                "results": parsed_results,
                                result_key: parsed_results,
                                "user_id": self.user_id,
                                "session_id": self.session_id,
                                "steps": steps,
                            }
                    return {
                        "status": "api_error",
                        "source": label,
                        "type": "info_results",
                        "message": "Aucun hôtel trouvé pour votre recherche.",
                        "user_id": self.user_id,
                        "session_id": self.session_id,
                        "steps": steps,
                    }

                except Exception as e:
                    print(f"[StayAgent] {label}: {e}")
                    continue

        return {
            "status": "api_error",
            "type": "info_results",
            "message": "Service de recherche d'hôtels indisponible.",
            "sections": [{
                "title": "Erreur",
                "content": "Aucune API configurée pour ce service.",
                "items": ["Contactez votre administrateur"]
            }],
            "user_id": self.user_id,
            "session_id": self.session_id,
            "steps": steps,
        }

    async def _book_stay(self, entities: dict) -> dict:
        print(f"[StayAgent] entities reçues: {entities}")
        locs = entities.get("LOC", [])
        dates = entities.get("DATE", [])
        prices = entities.get("PRICE", [])
        misc = entities.get("MISC", [])
        persons = entities.get("PERSONS", [])
        
        price_min = None
        price_max = None
        stars = None
        sort_by = None
        order = None
        adults = 1
        if persons:
            try:
                adults = int(persons[0])
            except (ValueError, TypeError):
                adults = 1
        if len(prices) > 0:
            try: price_min = int(prices[0])
            except: pass
        if len(prices) > 1:
            try: price_max = int(prices[1])
            except: pass
        
        CABIN_CLASSES = {"economy", "business", "first", "premium_economy", "premium economy"}
        if misc:
            stars_candidates = [m for m in misc if str(m).lower() not in CABIN_CLASSES]
            stars = stars_candidates[0] if stars_candidates else None
        
        if "SORT_BY" in entities:
            sort_by = entities["SORT_BY"]
        if "ORDER" in entities:
            order = entities["ORDER"]

        check_in  = (
            entities.get("check_in") or
            entities.get("departure_date") or
            (dates[0] if len(dates) > 0 else None)
        )
        check_out = (
            entities.get("check_out") or
            entities.get("return_date")
            # ← pas de fallback dates[1] pour éviter les dates parasites
        )

        missing_fields = []
        
        city_available = (
            entities.get("hotel_city") or
            entities.get("city") or
            (locs[0] if locs else None)
        )
        if not city_available:
            missing_fields.append({
                "field": "city",
                "type": "LOC",
                "label": "Destination",
                "description": "Ville de destination",
                "placeholder": "Paris, Lyon, New York"
            })
        
        if not check_in or not check_out:
            missing_fields.append({
                "field": "dates",
                "type": "DATE",
                "label": "Dates de séjour",
                "description": "Dates d'arrivée et de départ",
                "format": "YYYY-MM-DD",
                "placeholder": "Check in / check out"
            })
        
        if missing_fields:
            return {
                "status": "need_more_info",
                "source": "validation",
                "message": "Veuillez compléter les informations ci-dessous",
                "required_fields": missing_fields,
                "action": "complete_form",
                "form": self._build_search_form(missing_fields),
                "steps": ["Veuillez compléter les informations ci-dessous", "Envoyez le formulaire rempli"]
            }
        
        city = (
            entities.get("hotel_city")
            or (entities.get("city", [None])[0]
                if isinstance(entities.get("city"), list) else entities.get("city"))
            or (locs[0] if locs else "")
        )
        city = self._clean_city(city)
        if entities.get("hotel_city") and any('\u0600' <= c <= '\u06ff' for c in str(entities.get("hotel_city", ""))):
            entities["hotel_city"] = self._clean_city(entities["hotel_city"])
        if isinstance(entities.get("city"), list):
            entities["city"] = [self._clean_city(v) for v in entities["city"]]
        elif entities.get("city"):
            entities["city"] = self._clean_city(entities["city"])
        
        api_payload = {
            "adults": adults,
            "currency": self.tenant_config.get("currency", "EUR") if self.tenant_config else "EUR",
            "city": city
        }
        
        if check_in:
            api_payload["arrival_date"] = check_in
        if check_out:
            api_payload["departure_date"] = check_out
        if price_min:
            api_payload["price_min"] = price_min
        if price_max:
            api_payload["price_max"] = price_max
        if stars:
            api_payload["stars"] = stars
        if sort_by:
            api_payload["sort_by"] = sort_by
        if order:
            api_payload["order"] = order
        
        rag_query = f"hôtels disponibles {city} {check_in} {check_out} prix étoiles avis"
        steps = [
            "Voici les hôtels disponibles pour vos dates",
            "Précisez vos critères (prix, étoiles, quartier) pour affiner",
            "Sélectionnez un hôtel pour voir les détails"
        ]
        
        if self.response_type == "api":
            return await self._resolve_api(api_payload, rag_query, steps, "offers", "GET")
        
        elif self.response_type == "rag":
            return await self._resolve_rag_only(entities)
        
        elif self.response_type == "llm":
            prompt_override = self.subcat_config.get("llm_prompt_override", "")
            return await self._resolve_llm_only(entities, prompt_override)
        
        elif self.response_type == "human":
            contact = self.subcat_config.get("human_contact", "")
            return await self._resolve_human_only(contact)
        
        return await self._resolve_api(api_payload, rag_query, steps, "offers", "GET")

    def _build_search_form(self, missing_fields: list) -> dict:
        form = {
            "type": "form",
            "title": "Recherche d'hôtels",
            "description": "Veuillez fournir les informations suivantes :",
            "fields": []
        }
        
        missing_field_names = [f.get("field") for f in missing_fields]
        
        field_templates = {
            "city": {
                "name": "city",
                "label": "Destination",
                "type": "text",
                "required": True,
                "placeholder": "Où allez-vous ?",
                "value": None
            },
            "dates": {
                "name": "dates",
                "label": "Dates de séjour",
                "type": "date_range",
                "required": True,
                "placeholder": "check_out / check_in",
                "format": "jj/mm/aaaa",
                "fields": [
                    {"name": "check_in", "label": "Check_in", "type": "date", "placeholder": "jj/mm/aaaa"},
                    {"name": "check_out", "label": "Check_out", "type": "date", "placeholder": "jj/mm/aaaa"}
                ]
            },
            "guests": {
                "name": "guests",
                "label": "Voyageurs",
                "type": "number",
                "required": True,
                "placeholder": "Nombre de personnes",
                "min": 1,
                "max": 10,
                "value": 1
            },
            "rooms": {
                "name": "rooms",
                "label": "Chambres",
                "type": "number",
                "required": True,
                "placeholder": "Nombre de chambres",
                "min": 1,
                "max": 5,
                "value": 1
            }
        }
        
        for field in missing_fields:
            field_name = field.get("field")
            if field_name in field_templates:
                form["fields"].append(field_templates[field_name])
        
        if "city" not in missing_field_names and "dates" not in missing_field_names:
            form["fields"].append(field_templates["guests"])
            form["fields"].append(field_templates["rooms"])
        
        return form

    async def _modify_stay(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        dates = entities.get("DATE", [])
        booking_ref = entities.get("FLIGHT", [])

        check_in = dates[0] if len(dates) > 0 else None
        check_out = dates[1] if len(dates) > 1 else None
        
        api_payload = {
            "booking_reference": booking_ref[0] if booking_ref else None,
            "new_check_in": check_in,
            "new_check_out": check_out,
            "new_city": locs[0] if locs else None,
        }
        
        rag_query = "modification réservation hôtel conditions frais délai procédure"
        steps = [
            "Vérifiez les conditions tarifaires de votre réservation",
            "Des frais de modification peuvent s'appliquer selon le tarif",
            "Contactez l'hôtel ou l'agence avec votre référence",
            "Une confirmation modifiée vous sera envoyée"
        ]
        
        if self.response_type == "api":
            return await self._resolve_api(api_payload, rag_query, steps, "result", "PUT")
        
        elif self.response_type == "rag":
            return await self._resolve_rag_only(entities)
        
        elif self.response_type == "llm":
            prompt_override = self.subcat_config.get("llm_prompt_override", "")
            return await self._resolve_llm_only(entities, prompt_override)
        
        elif self.response_type == "human":
            contact = self.subcat_config.get("human_contact", "")
            return await self._resolve_human_only(contact)
        
        return await self._resolve_api(api_payload, rag_query, steps, "result", "PUT")

    def _parse_mygo_response(self, mygo_data: list, api_payload: dict) -> list:
        parsed_hotels = []
        target_currency = self.tenant_config.get("currency", "EUR") if self.tenant_config else "EUR"
        
        nights = 3
        try:
            from datetime import datetime
            check_in = api_payload.get("arrival_date", "")
            check_out = api_payload.get("departure_date", "")
            if check_in and check_out:
                check_in_dt = datetime.strptime(check_in, "%Y-%m-%d")
                check_out_dt = datetime.strptime(check_out, "%Y-%m-%d")
                nights = (check_out_dt - check_in_dt).days
                if nights <= 0:
                    nights = 3
        except Exception as e:
            print(f"[StayAgent] Erreur calcul nuits: {e}")
            nights = 3
        
        for item in mygo_data[:10]:
            try:
                hotel_info = item.get("Hotel", {})
                if not hotel_info:
                    continue
                    
                price_info = item.get("Price", {})
                currency = item.get("Currency", "TND")
                
                offers = []
                boarding_list = price_info.get("Boarding", [])
                
                for boarding in boarding_list[:2]:
                    boarding_name = boarding.get("Name", "") or ""
                    boarding_code = boarding.get("Code", "") or ""
                    
                    pax_list = boarding.get("Pax", [])
                    if not pax_list:
                        continue
                    
                    pax = pax_list[0]
                    adult_count = pax.get("Adult", 2) or 2
                    children = pax.get("Child", []) or []
                    
                    rooms = pax.get("Rooms", [])[:2]
                    
                    for room in rooms:
                        price_str = room.get("Price", "0")
                        try:
                            price = float(price_str) if price_str else 0
                        except (ValueError, TypeError):
                            price = 0
                        
                        price_with_markup_str = room.get("PriceWithAffiliateMarkup", price_str)
                        try:
                            price_with_markup = float(price_with_markup_str) if price_with_markup_str else price
                        except (ValueError, TypeError):
                            price_with_markup = price
                        
                        offer = {
                            "id": str(room.get("Id", f"offer_{len(offers)}")),
                            "name": (room.get("Name", "Chambre Standard") or "Chambre Standard")[:50],
                            "board_type": boarding_name[:30],
                            "board_code": boarding_code[:10],
                            "description": (room.get("Description", "") or f"{room.get('Name', 'Chambre')} avec {boarding_name}")[:100],
                            "price": price,
                            "price_with_markup": price_with_markup,
                            "price_per_night": round(price / nights, 2) if nights > 0 and price > 0 else price,
                            "currency": currency or "TND",
                            "target_currency": target_currency,
                            "quantity": min(room.get("Quantity", 0) or 0, 99),
                            "adults": min(adult_count, 10),
                            "children": children[:3],
                            "cancellation_deadline": "",
                            "stop_reservation": room.get("StopReservation", False) or False,
                            "selected": False,
                            "recommended": len(offers) == 0
                        }
                        offers.append(offer)
                        
                        if len(offers) >= 2:
                            break
                    
                    if len(offers) >= 2:
                        break
                
                if not offers:
                    base_price = price_info.get("BasePrice", 0)
                    if base_price:
                        try:
                            base_price_float = float(base_price) if base_price else 0
                            if base_price_float > 0:
                                offers.append({
                                    "id": "offer_default",
                                    "name": "Chambre Standard",
                                    "board_type": "Chambre seule",
                                    "board_code": "RO",
                                    "description": "Chambre standard",
                                    "price": base_price_float,
                                    "price_per_night": round(base_price_float / nights, 2) if nights > 0 else base_price_float,
                                    "currency": currency or "TND",
                                    "target_currency": target_currency,
                                    "quantity": 5,
                                    "adults": min(api_payload.get("adults", 2), 10),
                                    "children": [],
                                    "selected": False,
                                    "recommended": True
                                })
                        except (ValueError, TypeError):
                            pass
                
                image_url = hotel_info.get("Image", "") or ""
                if image_url and not image_url.startswith("http"):
                    base_url = "https://admin.mygo.co"
                    if image_url.startswith("/"):
                        image_url = f"{base_url}{image_url}"
                    else:
                        image_url = f"{base_url}/{image_url}"
                
                if not image_url:
                    hotel_name = hotel_info.get("Name", "Hotel") or "Hotel"
                    import urllib.parse
                    encoded_name = urllib.parse.quote(hotel_name[:30])
                    image_url = f"https://placehold.co/800x600/1565c0/white?text={encoded_name}"
                
                images = [image_url]
                
                category = hotel_info.get("Category", {}) or {}
                stars = category.get("Star", 0) or 0
                if stars == 0:
                    category_title = category.get("Title", "") or ""
                    if category_title:
                        import re
                        stars_match = re.search(r'(\d+)', category_title)
                        if stars_match:
                            try:
                                stars = int(stars_match.group(1))
                            except (ValueError, TypeError):
                                stars = 0
                
                amenities = []
                if stars >= 3:
                    amenities.append({"icon": "wifi", "name": "WiFi gratuit", "included": True})
                    amenities.append({"icon": "breakfast", "name": "Petit-déjeuner", "included": True})
                if stars >= 4:
                    amenities.append({"icon": "pool", "name": "Piscine", "included": True})
                if stars >= 5:
                    amenities.append({"icon": "spa", "name": "Spa", "included": True})
                    amenities.append({"icon": "gym", "name": "Salle de sport", "included": True})
                amenities.append({"icon": "parking", "name": "Parking", "included": True})
                
                best_offer = None
                min_price = float('inf')
                for offer in offers:
                    price = offer.get("price", 0) or 0
                    if price > 0 and price < min_price:
                        min_price = price
                        best_offer = offer
                
                total_price = min_price if min_price != float('inf') else 0
                price_per_night = round(total_price / nights, 2) if nights > 0 and total_price > 0 else 0
                
                city_name = (hotel_info.get("City", {}).get("Name", api_payload.get("city", "")) or api_payload.get("city", ""))[:50]
                country_name = "Tunisie"
                
                parsed_hotel = {
                    "id": str(hotel_info.get("Id", f"hotel_{len(parsed_hotels)}")),
                    "type": "hotel",
                    "name": (hotel_info.get("Name", "Hôtel") or "Hôtel")[:100],
                    "category": (category.get("Title", "") or "")[:50],
                    "stars": min(stars, 5),
                    "rating": min(stars, 5),
                    "rating_count": 0,
                    "location": {
                        "city": city_name,
                        "country": country_name,
                        "address": (hotel_info.get("Adress", "") or "")[:200],
                    },
                    "city": city_name,
                    "address": (hotel_info.get("Adress", "") or "")[:200],
                    "image": image_url,
                    "images": images[:3],
                    "token": (item.get("Token", "") or "")[:100],
                    "currency": currency or "TND",
                    "target_currency": target_currency,
                    "recommended": min(item.get("Recommended", 85) or 85, 100),
                    "source": (item.get("Source", "MyGO API") or "MyGO API")[:50],
                    "price_per_night": price_per_night,
                    "total_price": total_price,
                    "nights": nights,
                    "offers": offers[:2],
                    "amenities": amenities[:8],
                    "price_range": {
                        "min": total_price,
                        "max": total_price * 1.5 if total_price > 0 else 0
                    },
                    "best_offer": best_offer,
                    "actions": [
                        {"label": "Voir détails", "action": "details", "url": f"/hotel/{hotel_info.get('Id', '')}"},
                        {"label": "Réserver", "action": "book", "url": f"/hotel/{hotel_info.get('Id', '')}"}
                    ]
                }
                parsed_hotels.append(parsed_hotel)
                
                if len(parsed_hotels) >= 5:
                    break
                    
            except Exception as e:
                print(f"[StayAgent] Erreur parsing hôtel: {e}")
                continue
        
        print(f"[StayAgent] Parsé: {len(parsed_hotels)} hôtels")
        for hotel in parsed_hotels:
            print(f"  - {hotel['name']}: {len(hotel.get('offers', []))} offres, prix: {hotel['total_price']} {hotel['currency']}")
        
        return parsed_hotels


async def stay_agent(entities, language, tenant_config, sub_category=None,
                     tenant_id="", user_id="", session_id="",
                     response_type="api", subcat_config=None,
                     raw_text="", **kwargs) -> dict:
    agent = StayAgent(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
    return await agent.run(entities, language, tenant_config, sub_category=sub_category,
                           tenant_id=tenant_id, user_id=user_id, session_id=session_id,
                           response_type=response_type, subcat_config=subcat_config,
                           raw_text=raw_text, **kwargs)