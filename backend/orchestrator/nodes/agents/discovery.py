# discovery.py
import uuid, asyncio, hashlib, re, json, httpx
from typing import Any
from orchestrator.rag.rag_engine import RAGEngine
from core.config import settings
from core.database import get_pool
import time as _time

_llm_cache: dict[str, tuple] = {}  
_PARSE_CACHE_TTL = 3600

SUBCAT_DISPATCH = {
    "Restaurant Reservation": "_restaurant_reservation",
    "Gastronomy": "_gastronomy",
}

GEO_KEYS = {
    "city", "geoid", "geo_id", "location_id", "locationid",
    "lat", "lon", "latitude", "longitude", "place_id",
    "destination", "origin", "address", "region", "country",
}


class DiscoveryAgent:
    def __init__(self, tenant_id: str = "", user_id: str = "", session_id: str = ""):
        self.tenant_id     = tenant_id
        self.tenant_config = {}
        self.user_id       = user_id
        self.session_id    = session_id
        self.entities      = {}
        self.language      = ""
        self.api_keys      = {}
        self.sub_category  = ""
        self.agent_type    = "discovery"
        self.response_type = "api"
        self.subcat_config = {}
        self.raw_text      = ""
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
                tenant_id="", user_id="", session_id="",
                response_type="api", subcat_config=None, raw_text="", **kwargs) -> dict:
        self.tenant_id     = tenant_id or self.tenant_id
        self.tenant_config = tenant_config
        self.user_id       = user_id or self.user_id
        self.session_id    = session_id or self.session_id
        self.entities      = entities
        self.language      = language
        self.sub_category  = sub_category or ""
        self.response_type = response_type
        self.subcat_config = subcat_config or {}
        self.raw_text      = raw_text

        self.api_keys = await self._load_api_keys(self.tenant_id)

        method_name = SUBCAT_DISPATCH.get(sub_category)
        if not method_name and sub_category:
            subcat_only = sub_category.split(":")[-1].strip()
            method_name = next(
                (v for k, v in SUBCAT_DISPATCH.items() if k.endswith(subcat_only))
            )
        method = getattr(self, method_name)
        return await method(entities)
    
    async def _resolve_rag_only(self, entities: dict, result_key: str = "restaurants") -> dict:
        locs = entities.get("LOC", [])
        city = entities.get("destination") or entities.get("city") or (locs[0] if locs else "")
        city = self._clean_city(city)

        rag_query = f"{result_key} à {city}" if city else result_key

        rag_result = await self.rag.search_with_subcategory(
            query=rag_query,
            sub_category=self.sub_category,
            k=10,
            filter_by_loc=None
        )

        if rag_result and rag_result.get("context"):
            items_list = await self._gemini_parse_rag_context(
                rag_result["context"], city, result_key
            )

            if not isinstance(items_list, list):
                items_list = []

            if not items_list:
                return {
                    "status": "rag_only",
                    "type": "info_results",
                    "message": f"Aucun {result_key} trouvé pour {city}.",
                    "sections": [{
                        "title": "Aucun résultat",
                        "content": f"Notre base de données ne contient pas de {result_key} pour {city}.",
                        "items": ["Essayez une autre ville", "Contactez notre équipe"]
                    }],
                    "results": [],
                    "user_id": self.user_id,
                    "session_id": self.session_id
                }

            result_type = (
                "specialty_search_results" if result_key == "specialties"
                else "restaurant_search_results"
            )

            return {
                "status":     "rag_only",
                "source":     "RAG+GPT-4o-mini",
                "type":       result_type,
                result_key:   items_list,
                "results":    items_list,
                "user_id":    self.user_id,
                "session_id": self.session_id
            }

        return {
            "status":     "rag_only",
            "type":       "info_results",
            "message":    f"Aucune information trouvée{f' pour {city}' if city else ''}",
            "results":    [],
            "user_id":    self.user_id,
            "session_id": self.session_id
        }
    
    async def _gemini_parse_rag_context(self, rag_text: str, city: str,
                                        result_key: str = "restaurants") -> list:
        rag_hash  = hashlib.sha256(rag_text[:6000].encode()).hexdigest()[:16]
        cache_key = f"rag_{result_key}_{city}_{rag_hash}"
 
        cached = _llm_cache.get(cache_key)
        if cached:
            val, ts = cached
            if _time.time() - ts < _PARSE_CACHE_TTL:
                print(f"[RAGDiscoveryParser] ✅ Cache hit → {len(val)} items")
                return val
 
        if result_key == "specialties":
            item_label = "spécialité culinaire"
            item_format = """{
        "id": "identifiant ou genere un slug depuis le nom",
        "name": "nom exact du plat",
        "type": "specialty",
        "description": "description trouvée ou vide",
        "image": "url image ou vide",
        "origin": "CITY_PLACEHOLDER",
        "where_to_try": "lieu pour gouter ou vide",
        "actions": [
            {"label": "Voir la recette", "action": "recipe", "url": ""}
        ]
        }"""
        else:
            item_label = "restaurant"
            item_format = """{
            "id": "identifiant ou genere un slug depuis le nom",
            "name": "nom exact du restaurant",
            "type": "restaurant",
            "description": "description ou vide",
            "image": "premiere url image trouvee ou vide",
            "categories": [{"name": "type de cuisine exact", "short_name": "", "icon": ""}],
            "location": {
                "address": "adresse trouvée ou vide",
                "locality": "CITY_PLACEHOLDER",
                "country": "Tunisie ou pays trouvé",
                "formatted_address": "adresse complète",
                "latitude": 0.0,
                "longitude": 0.0
            },
            "price": 0.0,
            "currency": "devise trouvée ou vide",
            "rating": 0.0,
            "specialties": [],
            "services": [],
            "opening_hours": {"open": "", "close": ""},
            "closed_days": [],
            "contact": {
                "tel": "cherche sous telephone/tel/phone/mobile/contact.telephone",
                "tel2": "deuxieme numero si present",
                "email": "email trouvé ou vide",
                "website": "url site web trouvée ou vide"
            },
            "actions": [
                {"label": "Voir détails", "action": "details", "url": ""},
                {"label": "Réserver une table", "action": "reserve", "url": ""}
            ]
            }"""
 
        item_format = item_format.replace("CITY_PLACEHOLDER", city)
        items_format = '{\n  "items": [\n    ' + item_format + '\n  ]\n}'
 
        prompt = f"""Tu es un expert en extraction de données.
Tu reçois un texte qui peut être dans N'IMPORTE QUEL FORMAT :
- JSON brut (champs en français, anglais, arabe...)
- Texte narratif avec des emojis
- Markdown avec des liens [texte](url)
- Liste structurée
 
MISSION : Extraire TOUS les {item_label}s mentionnés.
⚠️ RÈGLE ABSOLUE DE FILTRAGE :
- Ne retourne QUE Ville cible : "{city}" pas des autres city voisines.
- Exemple : si city="{city}", ignorer Kairouan, Tunis, Sousse, etc.
 
COMMENT LIRE LE TEXTE :
- nom/name/titre → name
- prix_moyen/prix/price + devise/currency → price + currency
- adresse/address + ville/city/locality → location
- telephone/tel/phone → contact.tel
- email → contact.email
- site/website/url → contact.website
- images[0] ou image → image (prendre la premiere URL)
- cuisine/categories/type → categories[0].name
 
EXTRACTION DES PRIX :
- "80 TND" ou prix_moyen:80 + devise:TND → price=80.0, currency="TND"
- Si absent → price=null
 
EXTRACTION DES URLS :
- "[Site web](http://example.com)" → extraire "http://example.com"
- "www.dareljeld.com" → "http://www.dareljeld.com"
- Ignorer les URLs localhost
 
REGLES :
1. Extraire TOUS les {item_label}s
2. Si adresse absente → locality="{city}"
3. N'invente rien
4. Génère un id slug si absent (ex: "dar-el-jeld")
5. Max 10 items
 
REPONDS UNIQUEMENT avec ce JSON (sans backticks) :
{items_format}
 
TEXTE SOURCE :
{rag_text[:6000]}"""
 
        try:
            raw = await self._call_openai(prompt, timeout=60.0)
 
            if not raw or not raw.strip():
                return []
 
            start = raw.find("{")
            end   = raw.rfind("}")
            if start != -1 and end != -1:
                raw = raw[start:end + 1]
 
            try:
                mapping = json.loads(raw)
            except json.JSONDecodeError as e:
                print(f"[RAGDiscoveryParser] ⚠ JSON invalide: {e}")
                return []
 
            items_raw = mapping.get("items", [])
            if not isinstance(items_raw, list):
                return []
 
            print(f"[RAGDiscoveryParser] ✅ {len(items_raw)} {item_label}s extraits")
 
            seen  = set()
            dedup = []
            for item in items_raw:
                if not isinstance(item, dict):
                    continue
                name = (item.get("name") or "").strip().lower()
                if not name or name in seen:
                    continue
                seen.add(name)
 
                if not item.get("id"):
                    slug = re.sub(r'[^a-z0-9]+', '-', name).strip('-')
                    item["id"] = slug[:30]
 
                contact = item.get("contact") or {}
                website = contact.get("website", "")
                if website and "localhost" in website:
                    match = re.search(r'(www\.[^\s"\')\]]+)', website)
                    contact["website"] = f"http://{match.group(1)}" if match else ""
                    item["contact"] = contact
 
                dedup.append(item)
 
            print(f"[RAGDiscoveryParser] ✅ {len(dedup)} après déduplication")
 
            normalized = self._normalize_items(dedup, result_key, city)
 
            for item in normalized:
                item_id = (item.get("id") or "").strip()
                if item_id:
                    _llm_cache[f"rag_detail:{result_key}:{item_id}"] = (
                        item, _time.time() - (_PARSE_CACHE_TTL - 86400)
                    )
 
            _llm_cache[cache_key] = (normalized, _time.time())
            return normalized
 
        except asyncio.TimeoutError:
            print("[RAGDiscoveryParser] ⏱ Timeout")
            return []
        except Exception as e:
            print(f"[RAGDiscoveryParser] ❌ {e}")
            return []

    def _normalize_items(self, items_raw: list, result_key: str, city: str = "",
                        cuisine: str = "", image_base_url: str = "") -> list:

        def _safe_str(v, d=""):
            return str(v).strip() if v is not None else d

        def _safe_float(v):
            try:    return float(str(v).replace(",", "."))
            except: return 0.0

        def _safe_int(v):
            try:    return int(v)
            except: return 0

        def _safe_list(v) -> list:
            if isinstance(v, list): return v
            if isinstance(v, str) and v: return [v]
            return []

        def _complete_image(url: str) -> str:
            if not url: return ""
            if url.startswith("http"): return url
            if image_base_url:
                return f"{image_base_url.rstrip('/')}/{url.lstrip('/')}"
            return url

        def _clean_website(website: str) -> str:
            if not website:
                return ""
            if "localhost" in website:
                match = re.search(r'(www\.[^\s"\')\]]+)', website)
                return f"http://{match.group(1)}" if match else ""
            if website.startswith("www."):
                return f"http://{website}"
            return website

        def _extract_contact(item: dict) -> dict:
            raw_contact = item.get("contact") or {}

            tel = _safe_str(
                raw_contact.get("tel") or raw_contact.get("telephone") or
                raw_contact.get("phone") or raw_contact.get("mobile") or
                item.get("tel") or item.get("telephone") or item.get("phone")
            )

            tel2 = _safe_str(
                raw_contact.get("tel2") or raw_contact.get("telephone2") or
                raw_contact.get("mobile") or item.get("telephone2") or item.get("mobile")
            )
            if tel2 == tel:
                tel2 = ""

            email = _safe_str(raw_contact.get("email") or item.get("email"))

            website = _clean_website(_safe_str(
                raw_contact.get("website") or raw_contact.get("site") or
                raw_contact.get("url") or item.get("website") or item.get("site")
            ))

            return {"tel": tel, "tel2": tel2, "email": email, "website": website}

        def _extract_hours(item: dict) -> dict:
            raw = item.get("opening_hours") or item.get("horaires") or {}
            if isinstance(raw, dict):
                return {
                    "open":       _safe_str(raw.get("open") or raw.get("ouvert")),
                    "close":      _safe_str(raw.get("close") or raw.get("fermeture")),
                    "open_eve":   _safe_str(raw.get("open_eve") or raw.get("ouvert_soir", "")),
                    "close_eve":  _safe_str(raw.get("close_eve") or raw.get("fermeture_soir", "")),
                }
            return {"open": "", "close": "", "open_eve": "", "close_eve": ""}

        results = []

        if result_key == "restaurants":
            for i, item in enumerate(items_raw[:10]):
                if not isinstance(item, dict):
                    continue

                raw_cats = item.get("categories") or []
                if isinstance(raw_cats, str):
                    raw_cats = [{"name": c.strip()} for c in raw_cats.replace("/", ",").split(",")]
                categories = [
                    {
                        "name":       _safe_str(c.get("name"), "Restaurant"),
                        "short_name": _safe_str(c.get("short_name") or c.get("name")),
                        "icon":       _safe_str(c.get("icon")),
                    }
                    for c in raw_cats[:3] if isinstance(c, dict)
                ] or [{"name": "Restaurant", "short_name": "Resto", "icon": ""}]

                raw_loc  = item.get("location") or {}
                address  = _safe_str(raw_loc.get("address") or item.get("adresse") or item.get("address"))
                locality = _safe_str(raw_loc.get("locality") or item.get("ville") or item.get("city") or city)
                location = {
                    "address":           address,
                    "locality":          locality,
                    "region":            _safe_str(raw_loc.get("region")),
                    "postcode":          _safe_str(raw_loc.get("postcode")),
                    "country":           _safe_str(raw_loc.get("country") or "Tunisie"),
                    "formatted_address": _safe_str(
                        raw_loc.get("formatted_address") or
                        ", ".join(filter(None, [address, locality]))
                    ),
                    "latitude":          _safe_float(raw_loc.get("latitude")),
                    "longitude":         _safe_float(raw_loc.get("longitude")),
                }

                contact = _extract_contact(item)

                price    = None
                currency = ""
                raw_price = item.get("price") or item.get("prix_moyen") or item.get("prix")
                raw_curr  = item.get("currency") or item.get("devise") or "TND"
                if raw_price is not None:
                    try:
                        pv = float(str(raw_price).replace(",", "."))
                        if pv > 0:
                            price    = pv
                            currency = _safe_str(raw_curr)
                    except: pass

                raw_image = item.get("image") or item.get("image_url") or ""
                if not raw_image:
                    imgs = _safe_list(item.get("images"))
                    raw_image = imgs[0] if imgs and isinstance(imgs[0], str) else ""
                image = _complete_image(_safe_str(raw_image))

                rating      = _safe_float(item.get("rating") or item.get("note"))
                specialties = _safe_list(item.get("specialties") or item.get("specialites") or item.get("specialités"))
                services    = _safe_list(item.get("services") or item.get("amenities") or item.get("facilities"))
                hours       = _extract_hours(item)
                closed_days = _safe_list(item.get("closed_days") or item.get("jours_fermeture") or item.get("closed"))
                description = _safe_str(item.get("description") or item.get("desc") or item.get("about"))

                item_id  = _safe_str(item.get("id"), f"resto_{i}")
                raw_acts = _safe_list(item.get("actions"))
                actions  = [
                    {
                        "label":  _safe_str(a.get("label")),
                        "action": _safe_str(a.get("action")),
                        "url":    _safe_str(a.get("url")),
                    }
                    for a in raw_acts if isinstance(a, dict)
                ] or [
                    {"label": "Voir détails",       "action": "details", "url": ""},
                    {"label": "Réserver une table", "action": "reserve", "url": contact["website"]},
                ]

                results.append({
                    "id":            item_id,
                    "type":          "restaurant",
                    "name":          _safe_str(item.get("name") or item.get("nom"), "Restaurant"),
                    "description":   description,
                    "categories":    categories,
                    "location":      location,
                    "distance":      _safe_int(item.get("distance")),
                    "price":         price,
                    "price_range":   None,
                    "currency":      currency,
                    "rating":        rating,
                    "specialties":   specialties,
                    "services":      services,
                    "opening_hours": hours,
                    "closed_days":   closed_days,
                    "contact":       contact,
                    "social_media":  {"facebook_id": "", "instagram": "", "twitter": ""},
                    "image":         image,
                    "website":       contact["website"],
                    "photos":        [],
                    "bestPhoto":     None,
                    "best_photo":    None,
                    "actions":       actions,
                })

        else:
            for i, item in enumerate(items_raw[:10]):
                if not isinstance(item, dict):
                    continue

                item_id  = _safe_str(item.get("id"), f"specialty_{i}")
                image    = _complete_image(_safe_str(item.get("image")))
                raw_acts = _safe_list(item.get("actions"))
                actions  = [
                    {
                        "label":  _safe_str(a.get("label")),
                        "action": _safe_str(a.get("action")),
                        "url":    _safe_str(a.get("url")),
                    }
                    for a in raw_acts if isinstance(a, dict)
                ] or [
                    {"label": "Voir la recette", "action": "recipe",
                     "url": f"https://www.themealdb.com/meal/{item_id}"}
                ]

                results.append({
                    "id":           item_id,
                    "type":         "specialty",
                    "name":         _safe_str(item.get("name") or item.get("nom"), "Spécialité"),
                    "image":        image,
                    "description":  item.get("description"),
                    "origin":       _safe_str(item.get("origin") or cuisine or city),
                    "where_to_try": _safe_str(item.get("where_to_try"), "Restaurants locaux"),
                    "actions":      actions,
                })

        print(f"[DiscoveryNormalizer] ✅ {len(results)} {result_key} normalisés")
        return results

    async def _resolve_llm_only(self, entities: dict, result_key: str = "restaurants") -> dict:
        locs = entities.get("LOC", [])
        city = entities.get("destination") or entities.get("city") or (locs[0] if locs else "")
        city = self._clean_city(city)
        user_query      = self.raw_text if hasattr(self, "raw_text") else ""
        target_currency = self.tenant_config.get("currency", "EUR")
        prompt_override = self.subcat_config.get("llm_prompt_override", "")

        cache_key = f"llm_disc_{result_key}_{hashlib.sha256(f'{city}_{user_query[:50]}'.encode()).hexdigest()[:16]}"
        cached = _llm_cache.get(cache_key)
        if cached:
            val, ts = cached
            if _time.time() - ts < _PARSE_CACHE_TTL:
                print(f"[LLMDiscovery] ✅ Cache hit")
                return val

        if prompt_override:
            prompt = prompt_override
            replacements = {
                "{city}":        city or "non spécifié",
                "{result_key}":  result_key,
                "{user_query}":  user_query,
                "{language}":    self.language or "fr",
                "{currency}":    target_currency,
            }
            for k, v in replacements.items():
                prompt = prompt.replace(k, v)

            instruction = f"""
⚠️ INSTRUCTION IMPORTANTE :
- La VILLE est : {city if city else 'extrais-la de la requête'}
- Le TYPE de résultat attendu : {result_key}
- La DEVISE : {target_currency}
- Ne laisse AUCUNE variable non remplacée dans le JSON.
"""
            final_prompt = instruction + "\n" + prompt
            print(f"[DiscoveryAgent] 🎯 Prompt personnalisé GPT-4o-mini pour {result_key}")

            try:
                raw = await self._call_openai(final_prompt, timeout=60.0)

                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                raw = raw.strip()

                result = json.loads(raw)
                result_dict = {
                    "status":     "llm_only",
                    "source":     "GPT-4o-mini Custom",
                    "type":       result.get("type", "info_results"),
                    "query":      result.get("query", {"city": city} if city else {}),
                    "results":    result.get("results", []),
                    "message":    result.get("message", ""),
                    "sections":   result.get("sections", []),
                    "user_id":    self.user_id,
                    "session_id": self.session_id,
                }
                _llm_cache[cache_key] = (result_dict, _time.time())
                return result_dict

            except asyncio.TimeoutError:
                print("[LLMDiscovery] ⏱ Timeout prompt custom → fallback GPT")
            except Exception as e:
                print(f"[LLMDiscovery] ❌ Prompt personnalisé échoué: {e} → fallback GPT")

        has_city = bool(city and city.strip())

        if result_key == "specialties" and not has_city:
            prompt = f"""Tu es un expert en gastronomie mondiale. L'utilisateur demande : "{user_query}". Réponds en JSON :
    {{"type":"info_results","message":"Spécialités culinaires du monde","sections":[{{"title":"Europe","content":"Cuisine méditerranéenne, française, italienne.","items":["Pizza napolitaine","Paella valencienne","Croissant parisien"]}},{{"title":"Asie","content":"Cuisines riches en épices.","items":["Sushi japonais","Pad thaï","Biryani indien"]}},{{"title":"Afrique du Nord","content":"Tajines, couscous et pastillas.","items":["Tajine marocain","Couscous tunisien","Harira"]}}],"actions":[{{"label":"Préciser une destination","action":"explore"}}]}}"""
            fallback = {"type": "info_results", "message": "Spécialités culinaires", "sections": [], "actions": []}

        elif result_key == "specialties" and has_city:
            prompt = f"""Génère 5 spécialités culinaires RÉELLES de {city} en JSON :
    {{"type":"specialty_search_results","query":{{"city":"{city}"}},"results":[{{"id":"specialty_1","type":"specialty","name":"Nom du plat","image":"","description":"Description","origin":"{city}","where_to_try":"Restaurants locaux","actions":[{{"label":"Voir la recette","action":"recipe","url":""}}]}}]}}"""
            fallback = {"type": "specialty_search_results", "query": {"city": city}, "results": []}

        elif result_key == "restaurants" and not has_city:
            prompt = f"""Tu es un expert en gastronomie. L'utilisateur demande : "{user_query}". Réponds en JSON :
    {{"type":"info_results","message":"Recommandations de restaurants","sections":[{{"title":"Comment choisir ?","content":"Recherchez les avis locaux.","items":["Consultez TripAdvisor","Lisez les blogs locaux"]}},{{"title":"Destinations gastronomiques","content":"Villes reconnues mondialement.","items":["Paris","Tokyo","Barcelone"]}}],"actions":[{{"label":"Préciser une ville","action":"explore"}}]}}"""
            fallback = {"type": "info_results", "message": "Recommandations", "sections": [], "actions": []}

        else:
            prompt = f"""Génère 5 restaurants réalistes à {city} en JSON :
    {{"type":"restaurant_search_results","query":{{"city":"{city}"}},"results":[{{"id":"resto_1","type":"restaurant","name":"Nom restaurant","categories":[{{"name":"Type cuisine","short_name":"","icon":""}}],"location":{{"address":"Adresse","locality":"{city}","country":"Pays","formatted_address":"Adresse complète","latitude":0.0,"longitude":0.0}},"distance":500,"contact":{{"tel":"","website":""}},"image":"","actions":[{{"label":"Voir détails","action":"details","url":""}}]}}]}}"""
            fallback = {"type": "restaurant_search_results", "query": {"city": city}, "results": []}

        try:
            raw = await self._call_openai(prompt, timeout=60.0)
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = json.loads(raw.strip())
        except Exception as e:
            print(f"[LLMDiscovery] ❌ GPT fallback échoué: {e}")
            result = fallback

        result_dict = {
            "status":     "llm_only",
            "source":     "GPT-4o-mini",
            "type":       result.get("type", "info_results"),
            "query":      result.get("query", {"city": city} if city else {}),
            "results":    result.get("results", []),
            "message":    result.get("message", ""),
            "sections":   result.get("sections", []),
            "user_id":    self.user_id,
            "session_id": self.session_id,
        }
        _llm_cache[cache_key] = (result_dict, _time.time())
        return result_dict

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
            pool = await get_pool()
            rows = await pool.fetch(
                """
                SELECT label, api_key, api_url, http_method,
                    payload_template, headers_template, result_path,
                    param_mapping, timeout_ms, retry_count
                FROM tenant_api_keys
                WHERE tenant_id = $1 AND is_active = TRUE AND agent_type = $2
                ORDER BY priority ASC
                """,
                uuid.UUID(tenant_id), self.agent_type,
            )
            print(f"[DiscoveryAgent] 🔍 {len(rows)} API keys trouvées")
            return {
                r["label"]: {
                    "key":              r["api_key"],
                    "url":              r["api_url"],
                    "http_method":      r["http_method"],
                    "payload_template": r["payload_template"],
                    "headers_template": r["headers_template"],
                    "result_path":      r["result_path"],
                    "param_mapping":    r["param_mapping"],
                    "timeout_ms":       r.get("timeout_ms", 15000),
                    "retry_count":      r.get("retry_count", 1),
                }
                for r in rows
            }
        except Exception as e:
            print(f"[DiscoveryAgent] ❌ Erreur API keys: {e}")
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
        if transform == "upper":      return value.upper()
        if transform == "lower":      return value.lower()
        if transform == "upper3":     return value[:3].upper()
        if transform == "lower2":     return value[:2].lower()
        if transform == "slug":       return value.lower().replace(" ", "-")
        if transform == "nospace":    return value.replace(" ", "")
        if transform == "first_word": return value.split()[0] if value.split() else value
        if transform == "ascii_lower":
            n = unicodedata.normalize("NFD", value)
            return "".join(c for c in n if unicodedata.category(c) != "Mn").lower()
        if transform.startswith("max"):
            try:    return value[:int(transform[3:])]
            except: return value
        if transform.startswith("map:"):
            mapping = {}
            for pair in transform[4:].split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    mapping[k.strip().lower()] = v.strip()
            city = value.lower().strip().replace("-", " ")
            return mapping.get(city, mapping.get(value.lower().strip(), value))
        return value

    def _map_entities(self, entities, param_mapping, api_payload=None, context=None) -> dict:
        result = {}
        if not param_mapping:
            return result
        if isinstance(param_mapping, str):
            try:    param_mapping = json.loads(param_mapping)
            except: return result

        entity_lists = {k: entities.get(k, []) for k in ("LOC", "DATE", "PRICE", "ORG")}

        for entity_key, mapping_val in param_mapping.items():
            if isinstance(mapping_val, dict):
                api_param     = mapping_val.get("param")
                transform     = mapping_val.get("transform")
                is_optional   = mapping_val.get("optional", False)
                default_value = mapping_val.get("default")
            else:
                api_param = mapping_val; transform = None
                is_optional = False; default_value = None

            if not api_param:
                continue
            if is_optional and default_value is not None and api_param not in result:
                result[api_param] = default_value

            m = re.match(r'^(\w+)(?:\[(\d+)\])?(?:\.(\w+))?$', entity_key)
            if not m:
                if entity_key in entity_lists and entity_lists[entity_key]:
                    value = entity_lists[entity_key][0]
                    if transform:
                        value = self._apply_transform(str(value), transform)
                    result[api_param] = value
                continue

            etype  = m.group(1).upper()
            index  = int(m.group(2)) if m.group(2) else None
            field  = m.group(3)
            values = entity_lists.get(etype, [])
            value  = None

            if field == "count":    value = len(values)
            elif field == "join":   value = ", ".join(values)
            elif index is not None and index < len(values):
                val = values[index]
                if field == "first":  value = val.split()[0] if isinstance(val, str) else val
                elif field == "last": value = val.split()[-1] if isinstance(val, str) else val
                else:                 value = val
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
        template      = creds.get("payload_template", {}) if creds else {}
        param_mapping = creds.get("param_mapping", {})    if creds else {}
        for attr in (template, param_mapping):
            if isinstance(attr, str):
                try:    attr = json.loads(attr)
                except: attr = {}

        def _get_param_name(v):
            return v.get("param", "") if isinstance(v, dict) else str(v)

        api_keys_lower = (
            {k.lower() for k in (template if isinstance(template, dict) else {}).keys()} |
            {_get_param_name(v).lower() for v in (param_mapping if isinstance(param_mapping, dict) else {}).values()}
        )
        if not (api_keys_lower & GEO_KEYS):
            return True

        city = str(api_payload.get("city") or "").strip()
        lat  = str(api_payload.get("lat")  or "").strip()
        lon  = str(api_payload.get("lon")  or "").strip()

        if method == "GET" and not city and not (lat and lon):
            print(f"[DiscoveryAgent] ⚠ {label} GET → localisation vide")
            return False
        return True

    async def _resolve_api(self, api_payload: dict, rag_query: str,
                            steps: list = None, result_key: str = "restaurants",
                            expected_method: str = None) -> dict:

        print(f"[DEBUG] _resolve_api appelé:")
        print(f"  - sub_category: {self.sub_category}")
        print(f"  - api_payload: {api_payload}")
        print(f"  - API keys: {list(self.api_keys.keys())}")

        steps = steps or []

        if result_key == "specialties":
            key_name = "Gastronomy:Local food specialties"
        else:
            key_name = "Gastronomy:Dining Recommendations"

        if key_name in self.api_keys:
            target_keys = {key_name: self.api_keys[key_name]}
        elif self.sub_category in self.api_keys:
            target_keys = {self.sub_category: self.api_keys[self.sub_category]}
        else:
            target_keys = {
                k: v for k, v in self.api_keys.items()
                if not expected_method or v.get("http_method", "GET").upper() == (expected_method or "GET").upper()
            }

        client = httpx.AsyncClient()
        try:
            for label, creds in target_keys.items():
                try:
                    method = creds.get("http_method", "GET").upper()
                    print(f"[DEBUG] Tentative API: {label} ({method})")

                    if not self._validate_payload(label, api_payload, method, creds=creds):
                        continue

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
                        "language":   self.language,
                    }

                    dynamic_values = self._map_entities(
                        self.entities, param_mapping, api_payload, context=context_values
                    )

                    parsed_mapping_temp = param_mapping
                    if isinstance(parsed_mapping_temp, str):
                        try:    parsed_mapping_temp = json.loads(parsed_mapping_temp)
                        except: parsed_mapping_temp = {}

                    for entity_key, mapping_config in (parsed_mapping_temp or {}).items():
                        if not isinstance(mapping_config, dict):
                            continue
                        api_param   = mapping_config.get("param")
                        default_val = mapping_config.get("default")
                        if not api_param:
                            continue
                        current = dynamic_values.get(api_param)
                        is_still_default = (
                            current is not None and default_val is not None
                            and str(current) == str(default_val)
                        )
                        if not current or is_still_default:
                            if api_payload.get(api_param) is not None:
                                dynamic_values[api_param] = api_payload[api_param]
                            elif default_val is not None and not current:
                                dynamic_values[api_param] = default_val

                    fill_values = {
                        "api_key": creds.get("key"),
                        **context_values,
                        **api_payload,
                        **dynamic_values,
                    }

                    url = creds.get("url", "")
                    for k, v in fill_values.items():
                        if v is not None:
                            url = url.replace(f"{{{k}}}", str(v))

                    payload = self._fill_template(template, fill_values)
                    headers = self._fill_template(headers_template, fill_values) or {}

                    print(f"[DEBUG] URL: {url}")

                    def _clean(p):
                        if not p:
                            return None
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
                            else:
                                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                                print(f"[DiscoveryAgent] ⚠ {label} {last_error}")

                        except httpx.TimeoutException as e:
                            last_error = f"Timeout: {e}"
                        except httpx.RequestError as e:
                            last_error = f"Request error: {e}"
                        except Exception as e:
                            last_error = f"Unexpected error: {e}"

                        if attempt < retry_count - 1:
                            await asyncio.sleep(min(2 ** attempt, 10))

                    if not response:
                        print(f"[DiscoveryAgent] ❌ {label}: {last_error}")
                        continue

                    print(f"[DiscoveryAgent] API {label} → HTTP {response.status_code}")

                    if response.status_code >= 400:
                        print(f"[DiscoveryAgent] ❌ {label}: {response.text[:300]}")
                        continue

                    try:
                        data = response.json()
                    except json.JSONDecodeError as e:
                        print(f"[DiscoveryAgent] ❌ {label}: JSON decode error: {e}")
                        continue

                    if data:
                        print(f"[DiscoveryAgent] 🤖 GPT Parser pour {label} ({result_key})")
                        parsed = await self._gemini_parse_response(data, api_payload, result_key)
                        result_type = (
                            "specialty_search_results" if result_key == "specialties"
                            else "restaurant_search_results"
                        )
                        if parsed:
                            print(f"[DiscoveryAgent] ✅ {len(parsed)} {result_key} parsés")
                            return {
                                "status":     "success",
                                "source":     label,
                                "type":       result_type,
                                result_key:   parsed,
                                "results":    parsed,
                                "user_id":    self.user_id,
                                "session_id": self.session_id,
                                "steps":      steps,
                            }

                    return {
                        "status": "api_error",
                        "source": label,
                        "type":   "info_results",
                        "message": f"Aucun {result_key} trouvé pour votre recherche.",
                        "sections": [{
                            "title":   "Aucun résultat",
                            "content": f"Nous n'avons pas trouvé de {result_key} pour ces critères.",
                            "items":   ["Essayez une autre ville", "Modifiez vos critères de recherche"]
                        }],
                        "user_id":    self.user_id,
                        "session_id": self.session_id,
                        "steps":      steps,
                    }

                except httpx.TimeoutException as e:
                    print(f"[DiscoveryAgent] ❌ {label} Timeout: {e}")
                    continue
                except Exception as e:
                    print(f"[DiscoveryAgent] ❌ {label} → {e}")
                    continue
        finally:
            await client.aclose()

        return {
            "status": "api_error",
            "type":   "info_results",
            "message": f"Désolé, le service de {result_key} est temporairement indisponible.",
            "sections": [{
                "title":   "Service indisponible",
                "content": "L'API de recherche n'a pas répondu.",
                "items":   ["Veuillez réessayer plus tard", "Contactez notre support si le problème persiste"]
            }],
            "actions": [{"label": "Réessayer", "action": "retry"}],
            "user_id":    self.user_id,
            "session_id": self.session_id,
            "steps":      steps,
        }

    async def _restaurant_reservation(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        city = entities.get("destination") or entities.get("city") or (locs[0] if locs else "")
        city = self._clean_city(city)
        print(f"[DiscoveryAgent] Spécialités - city='{city}', response_type={self.response_type}")

        steps = [
            f"Voici les meilleurs restaurants à {city}" if city else "Précisez une ville pour des recommandations",
            "Réservez à l'avance pour les restaurants populaires",
        ]

        if self.response_type == "api":
            if not city:
                return await self._resolve_llm_only(entities, "restaurants")
            return await self._resolve_api(
                api_payload={"city": city},
                rag_query=f"restaurants à {city} recommandations gastronomie",
                steps=steps,
                result_key="restaurants",
                expected_method="GET",
            )
        elif self.response_type == "rag":
            return await self._resolve_rag_only(entities, "restaurants")
        elif self.response_type == "llm":
            return await self._resolve_llm_only(entities, "restaurants")
        elif self.response_type == "human":
            contact = self.subcat_config.get("human_contact", "")
            return await self._resolve_human_only(contact)

        return await self._resolve_llm_only(entities, "restaurants")

    async def _gastronomy(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        city = entities.get("destination") or entities.get("city") or (locs[0] if locs else "")
        city = self._clean_city(city)
        print(f"[DiscoveryAgent] Spécialités - city='{city}', response_type={self.response_type}")

        steps = [
            f"Découvrez les plats emblématiques de {city}" if city else "Spécialités culinaires",
            "Goûtez aux spécialités dans les marchés locaux",
        ]

        if self.response_type == "api":
            if not city:
                return await self._resolve_llm_only(entities, "specialties")
            return await self._resolve_api(
                api_payload={"city": city},
                rag_query=f"spécialités culinaires de {city} plats traditionnels",
                steps=steps,
                result_key="specialties",
                expected_method="GET",
            )
        elif self.response_type == "rag":
            return await self._resolve_rag_only(entities, "specialties")
        elif self.response_type == "llm":
            return await self._resolve_llm_only(entities, "specialties")
        elif self.response_type == "human":
            contact = self.subcat_config.get("human_contact", "")
            return await self._resolve_human_only(contact)

        return await self._resolve_llm_only(entities, "specialties")

    async def _gemini_parse_response(self, raw_data: dict, api_payload: dict,
                                     result_key: str = "restaurants") -> list:
        city    = api_payload.get("city", "")
        cuisine = api_payload.get("cuisine", "")

        result_hash = hashlib.sha256(
            json.dumps(raw_data, ensure_ascii=False, sort_keys=True)[:3000].encode()
        ).hexdigest()[:20]
        cache_key = f"disc_result:{result_key}:{result_hash}"

        cached = _llm_cache.get(cache_key)
        if cached:
            val, ts = cached
            if _time.time() - ts < _PARSE_CACHE_TTL:
                print(f"[DiscoveryParser] ✅ Cache hit → {len(val)} items")
                return val

        full_json = json.dumps(raw_data, ensure_ascii=False)
        if len(full_json) > 10000:
            full_json = full_json[:10000] + "..."

        if result_key == "restaurants":
            target_format = """{
"image_base_url": "domaine de base si images relatives, sinon vide",
"items": [
    {
    "id": "identifiant unique",
    "name": "nom du restaurant",
    "categories": [{"name": "type cuisine", "short_name": "", "icon": ""}],
    "location": {
        "address": "", "locality": "ville", "country": "pays",
        "formatted_address": "", "latitude": 0.0, "longitude": 0.0
    },
    "price": 0.0,
    "currency": "devise trouvée ou vide",
    "rating": 0.0,
    "specialties": [],
    "services": [],
    "opening_hours": {"open": "", "close": ""},
    "closed_days": [],
    "contact": {"tel": "", "email": "", "website": ""},
    "image": "url image principale ou ''",
    "actions": [
        {"label": "Voir détails", "action": "details", "url": ""},
        {"label": "Site web", "action": "website", "url": ""}
    ]
    }
]
}"""
        else:
            target_format = """{
"items": [
    {
    "id": "identifiant unique",
    "name": "nom du plat",
    "image": "url image ou ''",
    "description": "description ou null",
    "origin": "ville ou pays",
    "where_to_try": "où gouter",
    "actions": [{"label": "Voir la recette", "action": "recipe", "url": ""}]
    }
]
}"""

        prompt = f"""Tu es un expert en extraction de données JSON.
 
REPONSE API COMPLETE :
{full_json}
 
CONTEXTE : {result_key} pour "{city}"{f', cuisine "{cuisine}"' if cuisine else ''}
 
MISSION : extrais TOUS les {result_key} et retourne ce JSON (sans backticks) :
{target_format}
 
REGLES :
- Cherche sous toutes les clés (results, items, data, venues, meals...)
- N'invente rien
- Max 10 items
- image_base_url : si les URLs images sont relatives (ex: "/img/photo.jpg"), donne le domaine de base"""

        try:
            raw = await self._call_openai(prompt, timeout=60.0)

            start = raw.find("{")
            end   = raw.rfind("}")
            if start != -1 and end != -1:
                raw = raw[start:end + 1]

            mapping       = json.loads(raw)
            all_items_raw = mapping.get("items", [])
            image_base    = mapping.get("image_base_url", "")
            print(f"[DiscoveryParser] ✅ {len(all_items_raw)} {result_key} extraits")

        except asyncio.TimeoutError:
            print("[DiscoveryParser] ⏱ Timeout")
            return []
        except Exception as e:
            print(f"[DiscoveryParser] ❌ {e}")
            return []

        results = self._normalize_items(all_items_raw, result_key, city, cuisine, image_base)
        _llm_cache[cache_key] = (results, _time.time())
        return results

    def _parse_foursquare_response(self, data: list, api_payload: dict) -> list:
        parsed = []
        for item in data:
            categories = item.get("categories", [])
            main_cat   = categories[0] if categories else {}
            icon       = main_cat.get("icon", {})
            icon_url   = f"{icon.get('prefix','')}bg_64{icon.get('suffix','')}" if icon else ""
            location   = item.get("location", {})
            social     = item.get("social_media", {})
            parsed.append({
                "id":   item.get("fsq_place_id"),
                "type": "restaurant",
                "name": item.get("name"),
                "categories": [
                    {"name": c.get("name"), "short_name": c.get("short_name"),
                     "icon": f"{c.get('icon',{}).get('prefix','')}bg_64{c.get('icon',{}).get('suffix','')}"}
                    for c in categories[:3]
                ],
                "location": {
                    "address":           location.get("address"),
                    "locality":          location.get("locality"),
                    "region":            location.get("region"),
                    "postcode":          location.get("postcode"),
                    "country":           location.get("country"),
                    "formatted_address": location.get("formatted_address"),
                    "latitude":          item.get("latitude"),
                    "longitude":         item.get("longitude"),
                },
                "distance": item.get("distance"),
                "contact":  {"tel": item.get("tel"), "email": item.get("email"), "website": item.get("website")},
                "social_media": {
                    "facebook_id": social.get("facebook_id"),
                    "instagram":   social.get("instagram"),
                    "twitter":     social.get("twitter"),
                },
                "image": icon_url,
                "actions": [
                    {"label": "Voir détails", "action": "details", "url": item.get("link", "")},
                    {"label": "Site web",     "action": "website", "url": item.get("website", "")},
                ],
            })
        return parsed

    def _parse_themealdb_response(self, data: list, api_payload: dict) -> list:
        return [
            {
                "id":           item.get("idMeal"),
                "type":         "specialty",
                "name":         item.get("strMeal"),
                "image":        item.get("strMealThumb"),
                "description":  None,
                "origin":       api_payload.get("cuisine"),
                "where_to_try": "Restaurants locaux et brasseries traditionnelles",
                "actions": [
                    {"label": "Voir la recette", "action": "recipe",
                     "url": f"https://www.themealdb.com/meal/{item.get('idMeal')}"},
                ],
            }
            for item in data
        ]


async def discovery_agent(entities, language, tenant_config, sub_category=None,
                          tenant_id="", user_id="", session_id="",
                          response_type="api", subcat_config=None,
                          raw_text="", **kwargs) -> dict:
    agent = DiscoveryAgent(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
    return await agent.run(entities, language, tenant_config, sub_category=sub_category,
                           tenant_id=tenant_id, user_id=user_id, session_id=session_id,
                           response_type=response_type, subcat_config=subcat_config,
                           raw_text=raw_text, **kwargs)