# transport.py
import re
import uuid
import httpx
import asyncio
import hashlib
import json
import time
from typing import Any
from core.config import settings
from core.database import get_pool
from orchestrator.rag.rag_engine import RAGEngine

# ─── Cache global avec TTL ────────────────────────────────────────────────────
# Structure : { cache_key: (valeur, timestamp) }
_CACHE_TTL = 3600  # 1 heure
_llm_cache: dict[str, tuple] = {}

def _cache_get(key: str):
    if key in _llm_cache:
        value, ts = _llm_cache[key]
        if time.time() - ts < _CACHE_TTL:
            return value
        else:
            del _llm_cache[key]
    return None

def _cache_set(key: str, value, use_llm: bool = False):
    _llm_cache[key] = (value, time.time())

# ─── Mapping sub_category → méthode métier ────────────────────────────────────
SUBCAT_DISPATCH = {
    "Flight Booking": "_book_flight",
    "Car Rental Booking": "_book_car",
    "Transportation Search": "_search_transport",
    "Flight Management": "_modify_flight",
    "Car Rental Management": "_modify_car",
}

# ─── Clés de localisation reconnues dans les payloads ─────────────────────────
GEO_KEYS = {
    "city", "geoid", "geo_id", "location_id", "locationid",
    "lat", "lon", "latitude", "longitude", "place_id",
    "destination", "origin", "address", "region", "country",
    "arr_iata", "dep_iata",
}


class TransportAgent:
    def __init__(self, tenant_id: str = "", user_id: str = "", session_id: str = ""):
        self.tenant_id = tenant_id
        self.tenant_config = {}
        self.user_id = user_id
        self.session_id = session_id
        self.entities = {}
        self.language = ""
        self.api_keys = {}
        self.sub_category = ""
        self.agent_type = "transport"
        self.response_type = "api"
        self.subcat_config = {}
        self.raw_text = ""
        self.rag_engine = None
    def _clean_city(self, city: str) -> str:
        """Traduit la ville si elle est en arabe."""
        if not city:
            return city
        if any('\u0600' <= c <= '\u06ff' for c in city):
            from services.redis import _translate_to_french
            return _translate_to_french(city, "query_ar")
        return city

    # =========================================================================
    # POINT D'ENTRÉE
    # =========================================================================

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
        self.subcat_config = subcat_config or {}
        self.raw_text = raw_text
        self.rag_engine = RAGEngine(tenant_id, self.agent_type, user_id, session_id)

        self.api_keys = await self._load_api_keys(tenant_id)

        method_name = SUBCAT_DISPATCH.get(sub_category)
        if not method_name and sub_category:
            subcat_only = sub_category.split(":")[-1].strip()
            method_name = next(
                (v for k, v in SUBCAT_DISPATCH.items() if k.endswith(subcat_only)))
        method = getattr(self, method_name)
        return await method(entities)
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
    # =========================================================================
    # API KEYS — ajout de provider_code
    # =========================================================================

    async def _load_api_keys(self, tenant_id: str) -> dict:
        try:
            pool = await get_pool()
            rows = await pool.fetch(
                """
                SELECT
                    tak.label,
                    tak.api_key,
                    tak.api_url,
                    tak.http_method,
                    tak.payload_template,
                    tak.headers_template,
                    tak.result_path,
                    tak.param_mapping,
                    tak.timeout_ms,
                    tak.retry_count,
                    COALESCE(p.code, '') AS provider_code
                FROM tenant_api_keys tak
                LEFT JOIN providers p ON p.provider_id = tak.provider_id
                WHERE tak.tenant_id = $1
                  AND tak.is_active  = TRUE
                  AND tak.agent_type = $2
                ORDER BY tak.priority ASC
                """,
                uuid.UUID(tenant_id),
                self.agent_type,
            )
            return {
                r["label"]: {
                    "key":              r["api_key"],
                    "url":              r["api_url"],
                    "http_method":      r["http_method"],
                    "payload_template": r["payload_template"],
                    "headers_template": r["headers_template"],
                    "result_path":      r["result_path"],
                    "param_mapping":    r["param_mapping"],
                    "timeout_ms":       r["timeout_ms"] or 15000,
                    "retry_count":      r["retry_count"] or 1,
                    "provider_code":    r["provider_code"] or "",
                }
                for r in rows
            }
        except Exception as e:
            print(f"[TransportAgent] ❌ Erreur chargement API keys: {e}")
            return {}

    # =========================================================================
    # UTILITAIRES TEMPLATE
    # =========================================================================

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
        if transform == "city_to_airport_code":
            airport_mapping = {
                "paris": "CDG", "paris cdg": "CDG", "paris orly": "ORY",
                "london": "LHR", "london heathrow": "LHR", "london gatwick": "LGW",
                "new york": "JFK", "newark": "EWR", "los angeles": "LAX",
                "chicago": "ORD", "miami": "MIA", "san francisco": "SFO",
                "boston": "BOS", "washington": "IAD", "atlanta": "ATL",
                "dallas": "DFW", "denver": "DEN", "seattle": "SEA",
                "toronto": "YYZ", "montreal": "YUL", "vancouver": "YVR",
                "rome": "FCO", "milan": "MXP", "berlin": "BER", "munich": "MUC",
                "frankfurt": "FRA", "amsterdam": "AMS", "brussels": "BRU",
                "madrid": "MAD", "barcelona": "BCN", "lisbon": "LIS",
                "vienna": "VIE", "zurich": "ZRH", "geneva": "GVA",
                "prague": "PRG", "warsaw": "WAW", "budapest": "BUD",
                "copenhagen": "CPH", "stockholm": "ARN", "oslo": "OSL",
                "helsinki": "HEL", "dublin": "DUB", "athens": "ATH",
                "istanbul": "IST", "istanbul sabiha": "SAW", "ankara": "ESB",
                "moscow": "SVO", "tokyo": "HND", "tokyo narita": "NRT",
                "osaka": "KIX", "beijing": "PEK", "shanghai": "PVG",
                "hong kong": "HKG", "seoul": "ICN", "bangkok": "BKK",
                "singapore": "SIN", "kuala lumpur": "KUL", "dubai": "DXB",
                "abu dhabi": "AUH", "doha": "DOH", "cairo": "CAI",
                "casablanca": "CMN", "tunis": "TUN", "algiers": "ALG",
                "johannesburg": "JNB", "sydney": "SYD", "melbourne": "MEL",
                "auckland": "AKL", "sao paulo": "GRU", "buenos aires": "EZE",
            }
            city = value.lower().strip()
            if city in airport_mapping:
                code = airport_mapping[city]
            else:
                first_word = city.split()[0] if city.split() else city
                code = airport_mapping.get(first_word, value.upper())
            return f"{code}.AIRPORT"
        if transform == "upper":        return value.upper()
        if transform == "lower":        return value.lower()
        if transform == "upper3":       return value[:3].upper()
        if transform == "lower2":       return value[:2].lower()
        if transform == "slug":         return value.lower().replace(" ", "-")
        if transform == "nospace":      return value.replace(" ", "")
        if transform == "first_word":   return value.split()[0] if value.split() else value
        if transform == "ascii_lower":
            n = unicodedata.normalize("NFD", value)
            return "".join(c for c in n if unicodedata.category(c) != "Mn").lower()
        if transform.startswith("max"):
            try:    return value[:int(transform[3:])]
            except: return value
        if transform.startswith("map:"):
            mapping = {k.strip().lower(): v.strip()
                       for pair in transform[4:].split(",")
                       if "=" in pair for k, v in [pair.split("=", 1)]}
            return mapping.get(value.lower().strip(), value[:3].upper())
        return value

    # =========================================================================
    # MÉTHODES DE RÉSOLUTION
    # =========================================================================

    async def _resolve_rag_only(self, entities: dict) -> dict:
        """Résolution RAG + Gemini """
        locs = entities.get("LOC", [])
        dates = entities.get("DATE", [])
        flight_numbers = self.entities.get("FLIGHT", [])
        orgs = self.entities.get("ORG", [])
        origin      = entities.get("origin")      or (locs[0] if locs else "")
        destination = entities.get("destination") or (locs[1] if len(locs) > 1 else "")
        origin      = self._clean_city(origin)       # ← AJOUTER
        destination = self._clean_city(destination)  # ← AJOUTER    
        travel_date = dates[0] if dates else ""

        rag_query = f"vol {origin} {destination}"
        if flight_numbers:
            rag_query += f" {flight_numbers[0]}"
        if orgs:
            rag_query += f" {orgs[0]}"
       
        api_payload = {
            "origin": origin,
            "destination": destination,
            "travel_date": travel_date,
            "currency": self.tenant_config.get("currency", "EUR")
        }

        rag_query = f"transport vols trains bus {origin} {destination}"

        rag_result = await self.rag_engine.search_with_subcategory(
            query=rag_query,
            sub_category=self.sub_category,
            k=10,
            filter_by_loc=None
        )

        if rag_result and rag_result.get("context"):
            flights_list = await self._gemini_parse_rag_context(rag_result["context"], api_payload)

            if not isinstance(flights_list, list):
                flights_list = []

            return {
                "status": "rag_only",
                "source": "RAG+GPT-4o-mini",
                "type": "flight_search_results",
                "flights": flights_list,  # ✅ Déjà formatée par _build_frontend_flights
                "results": flights_list,  # ✅ Pour compatibilité
                "query": api_payload,  # ✅ Utiliser api_payload directement
                "user_id": self.user_id,
                "session_id": self.session_id
            }
        
        return {
            "status": "rag_only",
            "type": "info_results",
            "message": f"Aucun vol disponible pour la route {origin} → {destination}",
            "results": [],
            "user_id": self.user_id,
            "session_id": self.session_id
        }

    async def _gemini_parse_rag_context(self, rag_text: str, api_payload: dict) -> list:
        """
        GPT-4o-mini lit le texte RAG et extrait les vols.
        - Extrait uniquement ce qui est présent dans le texte source
        - Champs absents → null / 0 / "" selon le type
        - N'invente aucune valeur
        - Max 3 vols, max 2 fare_options par vol
        """
        origin      = api_payload.get("origin", "")
        destination = api_payload.get("destination", "")
        travel_date = api_payload.get("travel_date", "")
        currency    = api_payload.get("currency", "EUR")
 
        # ── Cache par hash du texte RAG ───────────────────────────────────────
        rag_hash  = hashlib.sha256(rag_text[:6000].encode()).hexdigest()[:16]
        cache_key = f"rag_flight_{origin}_{destination}_{rag_hash}"
 
        cached = _cache_get(cache_key)
        if cached is not None:
            print(f"[RAGFlightParser] ✅ Cache hit → {len(cached)} vols")
            return cached
 
        prompt = f"""Tu reçois un texte brut issu d'une base de données de vols (fichier RAG).
 
MISSION : Extraire UNIQUEMENT les vols qui correspondent EXACTEMENT à cette route :
- Ville/aéroport de départ : {origin}
- Ville/aéroport d'arrivée : {destination}
- Période de voyage : {travel_date if travel_date else "toute date"}
 
⚠️ RÈGLE CRITIQUE DE CORRESPONDANCE :
Un vol correspond UNIQUEMENT si :
1. Son aéroport ou ville de DÉPART correspond à "{origin}"
   (même partiellement : "Alger", "ALG", "Algiers" → tous valides pour "Alger")
2. Son aéroport ou ville d'ARRIVÉE correspond à "{destination}"
   (même partiellement : "Berlin", "BER", "Berlin Brandenburg" → tous valides pour "Berlin")
3. Les deux conditions doivent être vraies EN MÊME TEMPS
 
Si AUCUN vol du texte ne satisfait ces deux conditions → retourne {{"flights": []}}
Ne retourne JAMAIS un vol dont le départ ou l'arrivée ne correspond pas à la route demandée.
 
FORMAT DE RÉPONSE (sans backticks) :
{{
"flights": [
    {{
    "id":               "identifiant trouvé dans le texte ou null",
    "airline":          "nom exact de la compagnie trouvé dans le texte",
    "airline_logo":     "url du logo si présente dans le texte ou ''",
    "flight_number":    "numéro de vol ou ''",
    "stops":            0,
    "stop_details":     null,
    "duration":         "durée exacte trouvée ou ''",
    "departure_airport":"code IATA aéroport départ ou ''",
    "departure_city":   "ville de départ ou '{origin}'",
    "departure_time":   "HH:MM ou ''",
    "departure_date":   "YYYY-MM-DD ou '{travel_date}'",
    "arrival_airport":  "code IATA aéroport arrivée ou ''",
    "arrival_city":     "ville d'arrivée ou '{destination}'",
    "arrival_time":     "HH:MM ou ''",
    "arrival_date":     "YYYY-MM-DD ou ''",
    "total_price": "prix NUMÉRIQUE total — si 'prix' est un objet avec 'economie'/'economy'/'business'/'affaires', prendre la valeur la plus basse. Ex: si prix={{economie: 890, affaires: 2580}} → 890.0",
    "currency":         "code devise si trouvé ou ''",
    "baggage_cabin":    "info bagage cabine ou ''",
    "baggage_checked":  "info bagage soute ou ''",
    "fare_options": [
        {{
        "name":     "nom du tarif ou 'Standard'",
        "price":    0.0,
        "currency": "",
        "features": ["feature 1", "feature 2"]
        }}
    ]
    }}
]
}}
 
RÈGLES ABSOLUES :
- Mets UNIQUEMENT les valeurs que tu vois dans le texte source
- Si un champ est absent → laisse 0, "", [] ou null selon le type
- N'invente AUCUNE valeur (pas de prix estimés, pas de noms génériques)
- Si le prix n'est pas mentionné → total_price = 0.0
- stops = nombre d'escales réel (0 = vol direct)
- Max 3 vols, max 2 fare_options par vol
 
TEXTE SOURCE :
{rag_text[:6000]}"""
 
        try:
            raw = await self._call_openai(prompt, timeout=60.0)
 
            for fence in ["```json", "```"]:
                raw = raw.replace(fence, "")
            raw = raw.strip()
            if not raw:
                print("[RAGFlightParser] ⚠ Réponse vide")
                return []
 
            mapping     = json.loads(raw)
            flights_raw = mapping.get("flights", [])
            print(f"[RAGFlightParser] ✅ {len(flights_raw)} vols extraits du RAG")
 
            result = self._build_frontend_flights(flights_raw, api_payload)
            _cache_set(cache_key, result,use_llm=True)
            return result
 
        except asyncio.TimeoutError:
            print("[RAGFlightParser] ⏱ Timeout")
            return []
        except Exception as e:
            print(f"[RAGFlightParser] ❌ Erreur: {e}")
            return []

    def _build_frontend_flights(self, flights_raw: list, api_payload: dict) -> list:
        """
        Builder partagé RAG + API → format frontend uniforme.
        Équivalent de _build_frontend_hotels dans stay.py.
        Produit le format complet avec fare_options, amenities, price.
        """
        origin      = api_payload.get("origin", "")
        destination = api_payload.get("destination", "")
        currency    = api_payload.get("currency", "EUR")

        def _safe_float(val) -> float:
            try:    return float(str(val).replace(",", "."))
            except: return 0.0

        def _build_amenities(f: dict) -> list:
            """Construit les amenities depuis les champs bagages du vol"""
            amenities = []
            baggage_cabin   = f.get("baggage_cabin", "")
            baggage_checked = f.get("baggage_checked", "")

            if baggage_cabin:
                amenities.append({
                    "icon": "luggage",
                    "name": "Bagage cabine",
                    "value": str(baggage_cabin)
                })
            else:
                # Valeur par défaut si rien trouvé dans le RAG
                amenities.append({
                    "icon": "luggage",
                    "name": "Bagage cabine",
                    "value": "1 pièce"
                })

            if baggage_checked:
                amenities.append({
                    "icon": "suitcase",
                    "name": "Bagage soute",
                    "value": str(baggage_checked)
                })

            return amenities

        frontend_flights = []

        for i, f in enumerate(flights_raw):
            if not isinstance(f, dict):
                continue

            total_price       = _safe_float(f.get("total_price") or 0)
            detected_currency = f.get("currency") or currency

            # ── fare_options : depuis le RAG si disponibles ───────────────────
            raw_fares = f.get("fare_options") or []
            built_fares = []

            for j, fare in enumerate(raw_fares[:2]):
                if not isinstance(fare, dict):
                    continue
                fare_price = _safe_float(fare.get("price") or total_price or 0)
                if fare_price == 0:
                    continue
                built_fares.append({
                    "name":      fare.get("name") or "Standard",
                    "price":     round(fare_price, 2),
                    "currency":  detected_currency,
                    "features":  fare.get("features") or [],
                    "selected":  False,
                    "recommended": j == 0
                })

            # ── Fallback fare_option depuis total_price ───────────────────────
            if not built_fares and total_price > 0:
                built_fares = [{
                    "name":      "Standard",
                    "price":     round(total_price, 2),
                    "currency":  detected_currency,
                    "features":  ["Bagage cabine inclus"],
                    "selected":  False,
                    "recommended": True
                }]

            # ── Ignorer si toujours pas de tarif ─────────────────────────────
            # ── Si pas de tarif trouvé → afficher quand même avec prix=0 ──────
            # Le vol existe dans le RAG, on l'affiche même sans prix connu
            if not built_fares:
                built_fares = [{
                    "name":        "Standard",
                    "price":       0.0,
                    "currency":    detected_currency,
                    "features":    [],
                    "selected":    False,
                    "recommended": True,
                    "price_label": "Prix sur demande"
                }]
                print(f"[FlightBuilder] ℹ️ Vol '{f.get('airline')}' → prix absent dans RAG, affiché sans tarif")

            # Recalculer total_price depuis les fares si absent
            if total_price == 0 and built_fares and built_fares[0]["price"] > 0:
                total_price = built_fares[0]["price"]

            # ── Logo compagnie ────────────────────────────────────────────────
            airline_logo = f.get("airline_logo") or ""
            if not airline_logo:
                # Fallback CDN depuis le code IATA de l'aéroport de départ
                dep_airport = f.get("departure_airport", "")
                if dep_airport and len(dep_airport) >= 2:
                    airline_logo = self._get_fallback_logo(dep_airport[:2])

            # ── Amenities ─────────────────────────────────────────────────────
            amenities = _build_amenities(f)

            # ── Construction du vol frontend ──────────────────────────────────
            flight = {
                "id":           str(f.get("id") or f"flight_{i}"),
                "type":         "flight",
                "airline":      f.get("airline") or "Compagnie",
                "airline_logo": airline_logo,
                "flight_number": f.get("flight_number") or f"FL{i}",
                "stops":        int(f.get("stops") or 0),
                "stop_details": f.get("stop_details") or None,
                "duration":     f.get("duration") or "",
                "departure": {
                    "airport": f.get("departure_airport") or "",
                    "city":    f.get("departure_city") or origin,
                    "time":    f.get("departure_time") or "--:--",
                    "date":    f.get("departure_date") or ""
                },
                "arrival": {
                    "airport": f.get("arrival_airport") or "",
                    "city":    f.get("arrival_city") or destination,
                    "time":    f.get("arrival_time") or "--:--",
                    "date":    f.get("arrival_date") or ""
                },
                "amenities":    amenities,
                "fare_options": built_fares,
                "price": {
                    "min":      built_fares[0]["price"] if built_fares else total_price,
                    "max":      built_fares[-1]["price"] if built_fares else total_price,
                    "currency": detected_currency,
                    "total":    round(total_price, 2)
                },
                "source":      "RAG",
                "recommended": 90 - (i * 5),
                "actions": [
                    {
                        "label":  "Voir détails",
                        "action": "details",
                        "url":    f"/flight/{f.get('id', i)}"
                    },
                    {
                        "label":  "Réserver",
                        "action": "book",
                        "url":    f"/book/flight/{f.get('id', i)}"
                    }
                ]
            }
            dep = (f.get("departure_city") or "").lower()
            arr = (f.get("arrival_city") or "").lower()
            origin_lower      = origin.lower()
            destination_lower = destination.lower()

            if origin and destination:
                dep_match = origin_lower in dep or dep in origin_lower
                arr_match = destination_lower in arr or arr in destination_lower
                if not dep_match or not arr_match:
                    print(f"[FlightBuilder] ⚠️ Vol ignoré — route incorrecte : "
                        f"{f.get('departure_city')} → {f.get('arrival_city')} "
                        f"(attendu: {origin} → {destination})")
                    continue
            frontend_flights.append(flight)

        print(f"[FlightBuilder] ✈️ {len(frontend_flights)} vols construits pour le frontend")
        for fl in frontend_flights:
            print(f"  - {fl['airline']} {fl['flight_number']}: "
                  f"{fl['departure']['time']} → {fl['arrival']['time']} | "
                  f"{fl['price']['total']} {fl['price']['currency']}")

        return frontend_flights
    async def _resolve_llm_only(self, entities: dict, prompt_override: str = "") -> dict:
        """Résolution par GPT-4o-mini — remplace _gemini_transport_search"""
        locs   = entities.get("LOC",   [])
        dates  = entities.get("DATE",  [])
        prices = entities.get("PRICE", [])
 
        origin      = entities.get("origin")      or (locs[0] if locs else "")
        destination = entities.get("destination") or (locs[1] if len(locs) > 1 else "")
        origin      = self._clean_city(origin)
        destination = self._clean_city(destination)

        travel_date = entities.get("departure_date") or entities.get("date_depart") or (dates[0] if dates else "")
        return_date = entities.get("return_date")    or entities.get("date_retour") or (dates[1] if len(dates) > 1 else "")
        _passengers_raw = entities.get("guests") or entities.get("adults") or (prices[0] if prices else None)
        passengers = int(_passengers_raw) if _passengers_raw and str(_passengers_raw).isdigit() else 1
        user_query  = self.raw_text if hasattr(self, "raw_text") else ""
 
        trip_type      = "aller-retour" if return_date and return_date != travel_date else "aller simple"
        target_currency = self.tenant_config.get("currency", "EUR")
 
        # ── Cache ──────────────────────────────────────────────────────────────
        origin_key = origin or "_"
        cache_key = f"llm_transport_{hashlib.sha256(f'{origin_key}_{destination}_{travel_date}_{user_query[:50]}'.encode()).hexdigest()[:16]}" 
        cached = _cache_get(cache_key)
        if cached is not None:
            print(f"[LLMTransport] ✅ Cache hit")
            return cached
 
        # ── Construire le prompt ───────────────────────────────────────────────
        if prompt_override:
            prompt = prompt_override
            prompt = prompt.replace("{user_query}",      user_query)
            prompt = prompt.replace("{origin}",          origin or "non spécifié (propose options locales à destination)")  # ← MODIFIER
            prompt = prompt.replace("{destination}",     destination or "non spécifié")
            prompt = prompt.replace("{travel_date}",     travel_date or "à déterminer")
            prompt = prompt.replace("{return_date}",     return_date or "non applicable")
            prompt = prompt.replace("{trip_type}",       trip_type)
            prompt = prompt.replace("{passengers}",      str(passengers))
            prompt = prompt.replace("{target_currency}", target_currency)
        else:
            prompt = f"""Tu es un expert en transport public mondial.
 
REQUÊTE: "{user_query}"
DÉPART: {origin or 'non spécifié'}
ARRIVÉE: {destination or 'non spécifié'}
DATE: {travel_date or 'à déterminer'}
TYPE: {trip_type} | PASSAGERS: {passengers} | DEVISE: {target_currency}
 
Retourne 3 options de transport réalistes en JSON strict (sans backticks) :
{{
  "type": "transport_search_results",
  "query": {{"origin": "{origin}", "destination": "{destination}"}},
  "results": [
    {{
      "id": "transport_1",
      "type": "bus",
      "icon": "🚌",
      "name": "Bus / Car",
      "color": "#4caf50",
      "company": "Compagnie locale",
      "duration": "2h 30m",
      "price": 25.0,
      "currency": "{target_currency}",
      "departure": {{"time": "08:00", "location": "{origin}"}},
      "arrival":   {{"time": "10:30", "location": "{destination}"}},
      "actions": [{{"label": "Voir détails", "action": "details", "url": "/transport/bus"}}]
    }}
  ],
  "message": "Options de transport de {origin} à {destination}"
}}
 
Tous les prix en {target_currency}. JSON uniquement."""
 
        # ── Appel GPT-4o-mini ─────────────────────────────────────────────────
        try:
            raw = await self._call_openai(prompt, timeout=60.0)
 
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()
 
            result = json.loads(raw)
 
            # Normaliser la structure
            transport_options = []
            if isinstance(result, dict):
                if "results" in result and isinstance(result["results"], list):
                    transport_options = result["results"]
                elif "transports" in result:
                    transport_options = result["transports"]
                else:
                    # ← AJOUTER : parser le format clé/valeur du prompt override
                    TYPE_MAP = {
                        "bus": ("🚌", "#4caf50"),
                        "train": ("🚆", "#2196f3"),
                        "taxi": ("🚕", "#ff9800"),
                        "covoiturage": ("🚗", "#9c27b0"),
                        "transport_local": ("🛺", "#607d8b"),
                        "metro_tram": ("🚊", "#00bcd4"),
                        "ferry_bateau": ("⛴️", "#03a9f4"),
                        "location_voiture": ("🚙", "#795548"),
                    }
                    i = 0
                    for key, val in result.items():
                        if not isinstance(val, dict):
                            continue
                        if val.get("disponible") is False:
                            continue
                        icon, color = TYPE_MAP.get(key, ("🚌", "#607d8b"))
                        price_raw = val.get("prix", 0)
                        try:
                            price = float(str(price_raw).replace(",", "."))
                        except:
                            price = 0.0
                        transport_options.append({
                            "id": f"transport_{key}_{i}",
                            "type": key,
                            "icon": icon,
                            "color": color,
                            "name": key.replace("_", " ").title(),
                            "company": val.get("compagnie") or val.get("plateforme") or "",
                            "duration": val.get("duree") or "",
                            "price": price,
                            "currency": target_currency,
                            "departure": {
                                "time": (val.get("depart") or {}).get("heure") or "",
                                "location": (val.get("depart") or {}).get("ville") or origin or destination,
                            },
                            "arrival": {
                                "time": (val.get("arrivee") or {}).get("heure") or "",
                                "location": (val.get("arrivee") or {}).get("ville") or destination,
                            },
                            "actions": [{"label": "Voir détails", "action": "details", "url": f"/transport/{key}"}]
                        })
                        i += 1
            elif isinstance(result, list):
                transport_options = result
 
            if not transport_options:
                transport_options = self._get_fallback_transport_options(origin, destination)["results"]
 
            result_dict = {
                "status":     "llm_only",
                "source":     "GPT-4o-mini",
                "type":       "transport_search_results",
                "query":      {"origin": origin, "destination": destination},
                "results":    transport_options,
                "transports": transport_options,
                "message":    f"Options de transport de {origin} à {destination}",
                "user_id":    self.user_id,
                "session_id": self.session_id,
            }
 
            _cache_set(cache_key, result_dict, use_llm=True)
            print(f"[LLMTransport] ✅ {len(transport_options)} options de transport")
            return result_dict
 
        except asyncio.TimeoutError:
            print("[LLMTransport] ⏱ Timeout → fallback")
        except Exception as e:
            print(f"[LLMTransport] ❌ Erreur: {e} → fallback")
 
        # ── Fallback statique si GPT-4o-mini échoue ───────────────────────────
        fallback = self._get_fallback_transport_options(origin, destination)
        return {
            "status":     "llm_only",
            "source":     "fallback",
            "type":       "transport_search_results",
            "query":      {"origin": origin, "destination": destination},
            "results":    fallback["results"],
            "transports": fallback["results"],
            "user_id":    self.user_id,
            "session_id": self.session_id,
        }


    def _clean_price(self, price_value) -> tuple:
        if isinstance(price_value, (int, float)):
            return float(price_value), None
        if not isinstance(price_value, str):
            return 0.0, None
        import re
        price_lower = price_value.lower().strip()
        if price_lower in ["sur demande", "on request", "variable", "selon trajet"]:
            return 0.0, None
        currency_match = re.search(r'(TND|EUR|USD|GBP|JPY|MAD|DZD|CAD|CHF|AED)', price_value)
        currency = currency_match.group(1) if currency_match else None
        numbers = re.findall(r'(\d+(?:[.,]\d+)?)', price_value)
        if numbers:
            first_num = float(numbers[0].replace(',', '.'))
            if len(numbers) > 1:
                second_num = float(numbers[1].replace(',', '.'))
                price = (first_num + second_num) / 2
            else:
                price = first_num
            return price, currency
        return 0.0, currency

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
            "MISC": entities.get("MISC", [])
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

    # =========================================================================
    # VALIDATE PAYLOAD
    # =========================================================================

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

        api_keys_lower = {k.lower() for k in (template if isinstance(template, dict) else {}).keys()} | \
                         {_get_param_name(v).lower() for v in (param_mapping if isinstance(param_mapping, dict) else {}).values()}

        if not (api_keys_lower & GEO_KEYS):
            return True

        origin      = str(api_payload.get("origin")      or "").strip()
        destination = str(api_payload.get("destination") or "").strip()

        if method == "GET":
            if not origin and not destination:
                print(f"[TransportAgent] ⚠ {label} GET → origin/destination vides")
                return False
        elif method == "POST":
            if not (origin and destination) or len(origin) != 3 or len(destination) != 3:
                print(f"[TransportAgent] ⚠ {label} POST → IATA invalide ({origin}/{destination})")
                return False
            date = str(api_payload.get("departure_date") or "").strip()
            if not date or len(date) != 10:
                print(f"[TransportAgent] ⚠ {label} POST → date invalide ({date})")
                return False
        elif method in ("PUT", "DELETE"):
            if not str(api_payload.get("flight_number") or "").strip() and \
               not str(api_payload.get("booking_ref")   or "").strip():
                print(f"[TransportAgent] ⚠ {label} {method} → références manquantes")
                return False

        return True

    # =========================================================================
    # RESOLVE API — utilise Gemini pour TOUS les cas (comme stay.py)
    # =========================================================================

    async def _resolve_api(self, api_payload: dict, rag_query: str,
                            steps: list = None, result_key: str = "offers",
                            expected_method: str = None) -> dict:

        print(f"[DEBUG] _resolve_api appelé:")
        print(f"  - sub_category: {self.sub_category}")
        print(f"  - api_payload: {api_payload}")
        print(f"  - API keys: {list(self.api_keys.keys())}")

        steps = steps or []
        target_keys = {}

        if self.sub_category and self.sub_category in self.api_keys:
            target_keys = {self.sub_category: self.api_keys[self.sub_category]}
        else:
            target_keys = {
                label: creds for label, creds in self.api_keys.items()
                if not expected_method or creds.get("http_method", "GET").upper() == expected_method.upper()
            }

        client = httpx.AsyncClient()
        try:

            for label, creds in target_keys.items():
                timeout_ms  = creds.get("timeout_ms", 15000)
                timeout_sec = timeout_ms / 1000
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
                    provider_code    = creds.get("provider_code", "")

                    if isinstance(template, str):
                        try:    template = json.loads(template)
                        except: template = {}
                    if isinstance(headers_template, str):
                        try:    headers_template = json.loads(headers_template)
                        except: headers_template = {}

                    context_values = {
                        "user_id": self.user_id,
                        "session_id": self.session_id,
                        "tenant_id": self.tenant_id,
                        "language": self.language,
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
                        **dynamic_values
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
                                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                                print(f"[TransportAgent] ⚠ {label} {last_error}")

                        except httpx.TimeoutException as e:
                            last_error = f"Timeout: {e}"
                        except httpx.RequestError as e:
                            last_error = f"Request error: {e}"
                        except Exception as e:
                            last_error = f"Unexpected error: {e}"

                        if attempt < retry_count - 1:
                            wait_time = min(2 ** attempt, 10)
                            await asyncio.sleep(wait_time)

                    if not response:
                        print(f"[TransportAgent] ❌ {label}: {last_error}")
                        continue

                    print(f"[TransportAgent] API {label} → HTTP {response.status_code}")

                    if response.status_code >= 400:
                        print(f"[TransportAgent] ❌ {label}: {response.text[:300]}")
                        continue

                    try:
                        data = response.json()
                        print(f"[FlightParser] 🔍 Clés racine: {list(data.keys())}")
                        if "data" in data:
                            print(f"[FlightParser] 🔍 Clés sous 'data': {list(data['data'].keys()) if isinstance(data['data'], dict) else type(data['data'])}")
                    except json.JSONDecodeError as e:
                        print(f"[TransportAgent] ❌ {label}: JSON decode error: {e}")
                        continue

                    # ✅ Gemini parse TOUS les cas (comme stay.py)
                    if data:
                        print(f"[TransportAgent] 🤖 Gemini Parser pour {label} (provider: {provider_code})")
                        data = self._filter_top_flights(data, max_flights=3)
                        parsed_flights = await self._gemini_parse_flight_response(
                            data, api_payload, provider_code=provider_code
                        )
                        if parsed_flights:
                            print(f"[TransportAgent] ✅ {label} → {len(parsed_flights)} vols parsés")
                            return {
                                "status": "success",
                                "source": label,
                                result_key: parsed_flights,
                                "results": parsed_flights,
                                "user_id": self.user_id,
                                "session_id": self.session_id,
                                "steps": steps,
                            }

                    return {
                        "status": "api_error",
                        "source": label,
                        "type": "info_results",
                        "message": "Aucun vol trouvé pour votre recherche.",
                        "sections": [{
                            "title": "Aucun résultat",
                            "content": "Nous n'avons pas trouvé de vols pour ces critères.",
                            "items": ["Essayez des dates différentes", "Vérifiez les villes de départ et d'arrivée"]
                        }],
                        "user_id": self.user_id,
                        "session_id": self.session_id,
                        "steps": steps,
                    }

                except httpx.TimeoutException as e:
                    print(f"[TransportAgent] ❌ {label} Timeout: {e}")
                    continue
                except Exception as e:
                    print(f"[TransportAgent] ❌ {label} → {e}")
                    continue
        finally:
            await client.aclose()    

        return {
            "status": "api_error",
            "type": "info_results",
            "message": "Service temporairement indisponible. Veuillez réessayer plus tard.",
            "sections": [{
                "title": "Erreur technique",
                "content": "Nous ne pouvons pas traiter votre demande pour le moment.",
                "items": ["Réessayez dans quelques minutes", "Contactez notre support si le problème persiste"]
            }],
            "user_id": self.user_id,
            "session_id": self.session_id,
            "steps": steps,
        }

    # =========================================================================
    # GEMINI PARSE — 1 seul appel, cache par provider_code, dédoublonnage Python
    # =========================================================================

    async def _gemini_parse_flight_response(self, raw_data: dict, api_payload: dict,
                                            provider_code: str = "") -> list:
        """
        Parse universel via GPT-4o-mini — marche avec n'importe quelle API de vols.
        Python valide la cohérence et dédoublonne.
        """
        origin      = api_payload.get("origin", "")
        destination = api_payload.get("destination", "")
        currency    = api_payload.get("currency", "EUR")
 
        # ── Cache par hash de la réponse ──────────────────────────────────────
        try:
            response_hash = hashlib.sha256(
                json.dumps(raw_data, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()[:16]
        except Exception:
            response_hash = None
 
        result_cache_key = f"flight_result_{response_hash}"
        cached = _cache_get(result_cache_key)
        if cached is not None:
            print(f"[FlightParser] ✅ Cache hit → {len(cached)} vols")
            return cached
 
        # ── Extraire la liste de vols depuis n'importe quelle structure ────────
        def _find_flights(data, depth=0) -> list:
            if depth > 4 or not isinstance(data, dict):
                return []
            PRIORITY_KEYS = [
                "flightOffers", "flights", "itineraries", "offers",
                "results", "items", "slices", "routes"
            ]
            STRICT_INDICATORS = [
                "segments", "token", "priceBreakdown", "itineraries",
                "flightInfo", "slices", "booking_token", "flightKey",
                "travellerPrices", "carriers", "carriersData", "legs",
                "price", "fare", "departure", "arrival"
            ]
            for key in PRIORITY_KEYS:
                val = data.get(key)
                if isinstance(val, list) and val and isinstance(val[0], dict):
                    if any(k in val[0] for k in STRICT_INDICATORS):
                        return val
            for key, val in data.items():
                if isinstance(val, list) and val and isinstance(val[0], dict):
                    if any(k in val[0] for k in STRICT_INDICATORS):
                        return val
                elif isinstance(val, dict):
                    found = _find_flights(val, depth + 1)
                    if found:
                        return found
            return []
 
        flight_list = _find_flights(raw_data)
        if not flight_list:
            print(f"[FlightParser] ⚠ Aucune liste de vols trouvée")
            return []
 
        print(f"[FlightParser] 📋 {len(flight_list)} vols trouvés")
 
        # ── Logos depuis aggregation si disponible ─────────────────────────────
        airline_logos = {}
        aggregation = raw_data.get("aggregation", {}) or \
                      raw_data.get("data", {}).get("aggregation", {})
        for airline in aggregation.get("airlines", []):
            iata = airline.get("iataCode", "")
            logo = airline.get("logoUrl", "")
            if iata and logo:
                airline_logos[iata] = logo
 
        # ── Alléger chaque vol avant envoi à GPT-4o-mini ──────────────────────
        def _slim_segment(seg: dict) -> dict:
            s = {}
            for k in ["departureTime", "arrivalTime", "departureAirport",
                      "arrivalAirport", "totalTime", "cabinClass"]:
                if k in seg:
                    s[k] = seg[k]
            if "legs" in seg:
                s["legs"] = [_slim_leg(leg) for leg in seg["legs"]]
            return s
 
        def _slim_leg(leg: dict) -> dict:
            l = {}
            for k in ["departureTime", "arrivalTime", "departureAirport",
                      "arrivalAirport", "totalTime", "cabinClass"]:
                if k in leg:
                    l[k] = leg[k]
            if "carriersData" in leg:
                carriers = leg["carriersData"]
                if carriers and isinstance(carriers, list):
                    l["carriersData"] = [{
                        "name": carriers[0].get("name", ""),
                        "code": carriers[0].get("code", ""),
                        "logo": carriers[0].get("logo", "")
                    }]
            if "flightInfo" in leg:
                l["flightInfo"] = {
                    "flightNumber": leg["flightInfo"].get("flightNumber", "")
                }
            return l
 
        def _slim_flight(offer: dict) -> dict:
            slim = {}
            for id_key in ["token", "id", "flightKey", "booking_token", "offerKeyToHighlight"]:
                if id_key in offer:
                    slim[id_key] = str(offer[id_key])[:50]
                    break
            for price_key in ["priceBreakdown", "price", "fare", "totalAmount", "total_price"]:
                if price_key in offer:
                    slim[price_key] = offer[price_key]
                    break
            for seg_key in ["segments", "itineraries", "slices", "legs", "route"]:
                if seg_key in offer and isinstance(offer[seg_key], list):
                    slim[seg_key] = [_slim_segment(s) for s in offer[seg_key]]
                    break
            if "includedProducts" in offer:
                ip = offer["includedProducts"]
                if isinstance(ip, dict) and "segments" in ip:
                    slim["includedProducts"] = {"segments": ip["segments"][:1]}
            return slim
 
        slimmed     = [_slim_flight(f) for f in flight_list[:5]]
        flights_json = json.dumps(slimmed, ensure_ascii=False)
        print(f"[FlightParser] 📤 JSON allégé: {len(flights_json)} chars")
 
        # ── Prompt GPT-4o-mini ─────────────────────────────────────────────────
        prompt = f"""Tu es un expert en APIs de vols. Analyse ces vols bruts et extrais les informations.
 
VOLS BRUTS DE L'API :
{flights_json}
 
CONTEXTE :
- Ville de départ attendue : {origin}
- Ville d'arrivée attendue : {destination}
- Devise cible : {currency}
 
MISSION : Pour chaque vol, extrais les vraies valeurs et retourne ce JSON (sans backticks) :
{{
"flights": [
    {{
    "id": "identifiant unique du vol (token, id, bookingToken...)",
    "airline": "nom de la compagnie principale du vol",
    "airline_code": "code IATA de la compagnie (ex: TK, AF, LH)",
    "flight_number": "TOUS les numéros de vol concaténés avec ' - ' (ex: '801 - 235 - 202')",
    "departure_time": "HH:MM heure de départ INITIALE du voyage",
    "departure_date": "YYYY-MM-DD date de départ",
    "departure_airport": "code IATA aéroport de départ INITIAL (ex: ALG)",
    "departure_city": "ville de départ INITIALE (ex: Alger)",
    "arrival_time": "HH:MM heure d'arrivée FINALE à destination (pas une escale)",
    "arrival_date": "YYYY-MM-DD date d'arrivée FINALE",
    "arrival_airport": "code IATA aéroport d'arrivée FINALE (ex: BER)",
    "arrival_city": "ville d'arrivée FINALE (ex: Berlin)",
    "duration": "durée TOTALE du voyage (ex: 14h 50m)",
    "stops": 0,
    "stop_details": "description des escales ou null si vol direct",
    "total_price": 0.0,
    "currency": "{currency}",
    "baggage_cabin": "info bagage cabine ou ''",
    "baggage_checked": "info bagage soute ou ''"
    }}
]
}}
 
RÈGLES CRITIQUES :
- "flight_number" = TOUS les numéros de TOUS les legs/segments concaténés
- "departure_time" = heure du PREMIER départ (pas une escale)
- "arrival_time" = heure d'arrivée à la DESTINATION FINALE
- "departure_city" doit être "{origin}" ou la ville correspondante
- "arrival_city" doit être "{destination}" ou la ville correspondante
- "stops" = nombre d'escales (legs - 1 ou segments - 1 selon la structure)
- "stop_details" = "Escale à XXX (Xh Xm)" pour chaque escale, null si direct
- "total_price" = prix total en float (converti depuis units+nanos si nécessaire)
- N'invente rien — utilise uniquement ce qui est dans les données
- JSON uniquement, sans texte"""
 
        try:
            raw = await self._call_openai(prompt, timeout=90.0)
 
            for fence in ["```json", "```"]:
                raw = raw.replace(fence, "")
            raw = raw.strip()
 
            result      = json.loads(raw)
            flights_raw = result.get("flights", [])
            print(f"[FlightParser] ✅ {len(flights_raw)} vols extraits")
 
        except asyncio.TimeoutError:
            print("[FlightParser] ⏱ Timeout")
            return []
        except Exception as e:
            print(f"[FlightParser] ❌ Erreur: {e}")
            return []
 
        # ── Construction Python du format frontend (inchangée) ─────────────────
        def _safe_float(val) -> float:
            try:    return float(str(val).replace(",", "."))
            except: return 0.0
 
        frontend_flights = []
 
        for i, f in enumerate(flights_raw):
            if not isinstance(f, dict):
                continue
 
            total_price       = _safe_float(f.get("total_price") or 0)
            detected_currency = f.get("currency") or currency
            airline_code      = f.get("airline_code", "")
 
            if total_price == 0:
                print(f"[FlightParser] ⚠ Vol '{f.get('airline')}' ignoré → prix = 0")
                continue
 
            airline_logo = (
                airline_logos.get(airline_code) or
                self._get_fallback_logo(airline_code)
            )
 
            amenities = []
            if f.get("baggage_cabin"):
                amenities.append({"icon": "luggage",   "name": "Bagage cabine", "value": str(f["baggage_cabin"])})
            if f.get("baggage_checked"):
                amenities.append({"icon": "suitcase",  "name": "Bagage soute",  "value": str(f["baggage_checked"])})
            if not amenities:
                amenities = [{"icon": "luggage", "name": "Bagage cabine", "value": "1 pièce"}]
 
            arr_city = f.get("arrival_city") or destination
            dep_city = f.get("departure_city") or origin
            if arr_city == dep_city:
                arr_city = destination
 
            flight = {
                "id":            str(f.get("id") or f"flight_{i}"),
                "type":          "flight",
                "airline":       f.get("airline") or "Compagnie",
                "airline_logo":  airline_logo,
                "flight_number": f.get("flight_number") or f"FL{i}",
                "stops":         int(f.get("stops") or 0),
                "stop_details":  f.get("stop_details") or None,
                "duration":      f.get("duration") or "",
                "departure": {
                    "airport": f.get("departure_airport") or "",
                    "city":    dep_city,
                    "time":    f.get("departure_time") or "--:--",
                    "date":    f.get("departure_date") or ""
                },
                "arrival": {
                    "airport": f.get("arrival_airport") or "",
                    "city":    arr_city,
                    "time":    f.get("arrival_time") or "--:--",
                    "date":    f.get("arrival_date") or ""
                },
                "amenities": amenities,
                "fare_options": [{
                    "name":        "Standard",
                    "price":       round(total_price, 2),
                    "currency":    detected_currency,
                    "features":    ["Bagage cabine inclus", "Modification selon conditions"],
                    "selected":    False,
                    "recommended": True
                }],
                "price": {
                    "min":      round(total_price, 2),
                    "max":      round(total_price, 2),
                    "currency": detected_currency,
                    "total":    round(total_price, 2)
                }
            }
            frontend_flights.append(flight)
 
        print(f"[FlightParser] ✈️ {len(frontend_flights)} vols construits (avant dédoublonnage)")
 
        # ── Dédoublonnage (inchangé) ───────────────────────────────────────────
        seen_keys      = set()
        unique_flights = []
        for flight in frontend_flights:
            dedup_key = (
                flight.get("airline", ""),
                flight["departure"]["time"],
                flight["arrival"]["time"],
                round(flight["price"]["total"], 0)
            )
            if dedup_key not in seen_keys:
                seen_keys.add(dedup_key)
                unique_flights.append(flight)
 
        print(f"[FlightParser] ✅ {len(unique_flights)} vols uniques (après dédoublonnage)")
        for f in unique_flights:
            print(f"  - {f['airline']} {f['flight_number']}: "
                  f"{f['departure']['time']} → {f['arrival']['time']} | "
                  f"{f['price']['total']} {f['price']['currency']}")
 
        if result_cache_key:
            _cache_set(result_cache_key, unique_flights, use_llm=True)
 
        return unique_flights


    def _get_fallback_transport_options(self, origin: str, destination: str) -> dict:
        return {
            "type": "transport_search_results",
            "query": {"origin": origin, "destination": destination},
            "results": [
                {"id": "transport_bus_fallback", "type": "bus", "icon": "🚌", "name": "Bus / Car", "color": "#4caf50", "company": "Compagnie locale", "duration": "Variable", "price": 0.0, "departure": {"time": "Horaires multiples", "location": origin}, "arrival": {"time": "Variable", "location": destination}, "actions": []},
                {"id": "transport_train_fallback", "type": "train", "icon": "🚆", "name": "Train", "color": "#2196f3", "company": "Compagnie ferroviaire", "duration": "Variable", "price": 0.0, "departure": {"time": "Consulter horaires", "location": origin}, "arrival": {"time": "Consulter horaires", "location": destination}, "actions": []}
            ],
            "message": f"Options de transport de {origin} à {destination}"
        }

    # =========================================================================
    # UTILITAIRES
    # =========================================================================

    def _get_fallback_logo(self, iata_code: str) -> str:
        if not iata_code:
            return ""
        return f"https://r-xx.bstatic.com/data/airlines_logo/{iata_code}.png"

    def _filter_top_flights(self, data: dict, max_flights: int = 5) -> dict:
        """
        Filtre la liste de vols à max_flights avant d'envoyer à Gemini.
        Stratégie :
        - Si budget (price_max) défini → vols dont le prix ≤ budget, triés par prix
        - Sinon → les max_flights moins chers
        - Si prix indisponible → garder quand même (tri en dernier)
        """
        # ── Trouver la liste de vols dans la structure ────────────────────────
        list_key = None
        flight_list = None
        for key in ["flightOffers", "flights", "offers", "results", "items"]:
            if key in data and isinstance(data.get(key), list):
                list_key = key
                flight_list = data[key]
                break
            # Chercher sous "data"
            nested = data.get("data", {})
            if isinstance(nested, dict) and key in nested and isinstance(nested[key], list):
                list_key = key
                flight_list = nested[key]
                break

        if not flight_list:
            return data  # Rien à filtrer

        print(f"[TransportAgent] 📊 {len(flight_list)} vols reçus → filtre top {max_flights}")

        # ── Extraire le prix d'une offre ─────────────────────────────────────
        def _get_price(offer: dict) -> float:
            try:
                pb = offer.get("priceBreakdown", {})
                total = pb.get("total", {})
                units = float(total.get("units", 0) or 0)
                nanos = float(total.get("nanos", 0) or 0)
                price = units + nanos / 1_000_000_000
                if price > 0:
                    return price
                # Fallback : chercher price.amount, fare.total, totalAmount...
                for path in ["price.amount", "price.total", "fare.total",
                             "totalAmount", "total_price", "amount"]:
                    parts = path.split(".")
                    current = offer
                    for p in parts:
                        current = current.get(p, {}) if isinstance(current, dict) else None
                    if isinstance(current, (int, float)) and current > 0:
                        return float(current)
                return 0.0
            except Exception:
                return 0.0

        # ── Récupérer le budget depuis api_payload ────────────────────────────
        # On cherche price_max dans l'api_payload du contexte courant
        # (passé via self.entities ou depuis la dernière requête)
        price_max = None
        price_min = None
        prices = self.entities.get("PRICE", [])
        if len(prices) > 0:
            try: price_min = float(prices[0])
            except: pass
        if len(prices) > 1:
            try: price_max = float(prices[1])
            except: pass

        # ── Trier et filtrer ──────────────────────────────────────────────────
        # Séparer vols avec prix connu et sans prix
        with_price    = [(o, _get_price(o)) for o in flight_list if _get_price(o) > 0]
        without_price = [o for o in flight_list if _get_price(o) == 0]

        # Trier par prix croissant
        with_price.sort(key=lambda x: x[1])

        if price_max:
            # Garder uniquement les vols dans le budget
            in_budget  = [(o, p) for o, p in with_price if p <= price_max]
            if price_min:
                in_budget = [(o, p) for o, p in in_budget if p >= price_min]

            if in_budget:
                selected = [o for o, _ in in_budget[:max_flights]]
                print(f"[TransportAgent] 💰 Budget {price_min or 0}–{price_max} → {len(selected)} vols sélectionnés")
            else:
                # Aucun dans le budget → prendre les plus proches du budget
                selected = [o for o, _ in with_price[:max_flights]]
                print(f"[TransportAgent] ⚠ Aucun vol dans le budget → {len(selected)} moins chers")
        else:
            # Pas de budget → les max_flights moins chers
            selected = [o for o, _ in with_price[:max_flights]]
            # Compléter avec des vols sans prix si nécessaire
            if len(selected) < max_flights:
                selected += without_price[:max_flights - len(selected)]
            print(f"[TransportAgent] 📉 Pas de budget → {len(selected)} moins chers sélectionnés")

        # ── Reconstruire le data avec la liste filtrée ────────────────────────
        import copy
        filtered_data = copy.deepcopy(data)

        if "data" in filtered_data and isinstance(filtered_data["data"], dict) and list_key in filtered_data["data"]:
            filtered_data["data"][list_key] = selected
        elif list_key in filtered_data:
            filtered_data[list_key] = selected

        prices_str = [f"{_get_price(o):.0f}€" for o in selected if _get_price(o) > 0]
        print(f"[TransportAgent] ✅ Vols filtrés: {prices_str}")

        return filtered_data

    def _filter_flights_by_price(self, flights: list, price_min: float = None, price_max: float = None) -> list:
        if not price_min and not price_max:
            return flights
        filtered = []
        for flight in flights:
            price = float(flight.get("price", {}).get("total", 0) or 0)
            if not price:
                fare_options = flight.get("fare_options", [])
                if fare_options:
                    price = float(fare_options[0].get("price", 0) or 0)
            if price and price > 0:
                if price_min and price < price_min:
                    continue
                if price_max and price > price_max:
                    continue
            filtered.append(flight)
        return filtered

    def _format_time(self, datetime_str: str) -> str:
        if not datetime_str:
            return "--:--"
        try:
            time_part = datetime_str.split("T")[1].split(":")
            return f"{time_part[0]}:{time_part[1]}"
        except:
            return "--:--"

    def _format_date(self, datetime_str: str) -> str:
        if not datetime_str:
            return ""
        try:
            return datetime_str.split("T")[0]
        except:
            return ""

    def _build_search_form(self, missing_fields: list) -> dict:
        form = {"type": "form", "title": "Recherche de vols", "description": "Veuillez fournir les informations suivantes :", "fields": []}
        field_templates = {
            "origin":         {"name": "origin",         "label": "Départ",          "type": "text",   "required": True,  "placeholder": "Ville de départ"},
            "destination":    {"name": "destination",    "label": "Arrivée",          "type": "text",   "required": True,  "placeholder": "Ville d'arrivée"},
            "departure_date": {"name": "departure_date", "label": "Date de départ",   "type": "date",   "required": True,  "placeholder": "jj/mm/aaaa"},
            "return_date":    {"name": "return_date",    "label": "Date de retour",   "type": "date",   "required": False, "placeholder": "jj/mm/aaaa"},
            "adults":         {"name": "adults",         "label": "Voyageurs",        "type": "number", "required": True,  "min": 1, "max": 10, "value": 1},
            "cabin_class":    {"name": "cabin_class",    "label": "Classe",           "type": "select", "required": False, "options": ["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"], "value": "ECONOMY"},
            "price_min":      {"name": "price_min",      "label": "Prix minimum",     "type": "number", "required": False, "placeholder": "Prix min (optionnel)", "min": 0, "step": 10},
            "price_max":      {"name": "price_max",      "label": "Prix maximum",     "type": "number", "required": False, "placeholder": "Prix max (optionnel)", "min": 0, "step": 10}
        }
        for field in missing_fields:
            field_name = field.get("field")
            if field_name in field_templates:
                form["fields"].append(field_templates[field_name])
        missing_names = [f.get("field") for f in missing_fields]
        if "origin" not in missing_names and "destination" not in missing_names and "departure_date" not in missing_names:
            form["fields"].extend([field_templates["adults"], field_templates["cabin_class"],
                                   field_templates["return_date"], field_templates["price_min"], field_templates["price_max"]])
        return form

    # =========================================================================
    # MÉTHODES MÉTIERS
    # =========================================================================

    async def _book_flight(self, entities: dict) -> dict:
        print(f"[TransportAgent] 📋 entities reçues: {entities}")

        locs    = entities.get("LOC",     [])
        dates   = entities.get("DATE",    [])
        prices  = entities.get("PRICE",   [])
        misc    = entities.get("MISC",    [])
        persons = entities.get("PERSONS", [])

        # ── Résoudre origin/destination depuis clés explicites OU LOC[] ──────
        origin_val      = entities.get("origin")      or (locs[0] if len(locs) > 0 else "")
        destination_val = entities.get("destination") or (locs[1] if len(locs) > 1 else "")
        origin_val      = self._clean_city(origin_val)
        destination_val = self._clean_city(destination_val)
        depart_val      = (
            entities.get("departure_date")
            or (dates[0] if dates else "")
        )

        missing_fields = []
        if not origin_val or not destination_val:
            missing_fields.append({"field": "origin",      "type": "LOC",  "label": "Origine",     "description": "Ville de départ",  "placeholder": "Paris, Lyon, New York"})
            missing_fields.append({"field": "destination", "type": "LOC",  "label": "Destination", "description": "Ville d'arrivée",  "placeholder": "Paris, Lyon, New York"})
        elif origin_val.lower().strip() == destination_val.lower().strip():
            missing_fields.append({"field": "origin", "type": "LOC", "label": "Ville de départ",  "description": "Ville de départ", "placeholder": "Paris, Lyon, New York"})
        if not depart_val:
            missing_fields.append({"field": "departure_date", "type": "DATE", "label": "Date de départ", "description": "Date du vol", "format": "YYYY-MM-DD", "placeholder": "2026-04-15"})

        if missing_fields:
            return {
                "status": "need_more_info", "source": "validation",
                "message": "Veuillez compléter les informations ci-dessous",
                "required_fields": missing_fields, "action": "complete_form",
                "form": self._build_search_form(missing_fields),
                "steps": ["Veuillez compléter les informations ci-dessous", "Envoyez le formulaire rempli"]
            }

        origin      = origin_val
        destination = destination_val
        depart_date = depart_val
        return_date = (
            entities.get("return_date")
            or (dates[1] if len(dates) > 1 else "")
        )
        self.entities["LOC"] = [origin, destination]
        self.entities["DATE"] = [d for d in [depart_date, return_date] if d]

        adults = 1
        if persons:
            try:    adults = int(persons[0])
            except: adults = 1

        price_min = None
        price_max = None
        if len(prices) > 0:
            try: price_min = float(prices[0])
            except: pass
        if len(prices) > 1:
            try: price_max = float(prices[1])
            except: pass

        cabin_class = "ECONOMY"
        sort = "CHEAPEST" if price_max else "BEST"
        if misc:
            cabin_class = misc[0].upper() if misc[0] else "ECONOMY"

        api_payload = {
            "origin": origin, "destination": destination,
            "departure_date": depart_date, "return_date": return_date,
            "adults": adults, "cabin_class": cabin_class, "sort": sort,
            "price_min": price_min, "price_max": price_max,
            "currency": self.tenant_config.get("currency", "EUR"),
        }

        rag_query = f"vols disponibles {origin} {destination} {depart_date} compagnies horaires prix"
        steps = ["Voici les vols disponibles pour votre recherche", "Précisez vos dates ou destinations pour affiner"]

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

    async def _search_transport(self, entities: dict) -> dict:
        locs  = entities.get("LOC",  [])
        dates = entities.get("DATE", [])

        origin      = entities.get("origin")      or (locs[0] if locs else "")
        destination = (entities.get("destination") 
                    or self.entities.get("destination")  # ← AJOUTER
                    or (locs[1] if len(locs) > 1 else ""))
        origin      = self._clean_city(origin)
        destination = self._clean_city(destination)
        travel_date = entities.get("departure_date") or entities.get("date_depart") or (dates[0] if dates else "")

        if origin and destination and origin.lower() == destination.lower():
            origin = ""
        elif origin and not destination:
            destination = origin
            origin = ""

        api_payload = {"origin": origin, "destination": destination, "travel_date": travel_date}
        rag_query = f"train bus {origin} {destination} {travel_date}"
        steps = ["Voici les options de transport", "Choisissez votre trajet", "Réservez en ligne"]

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

    async def _book_car(self, entities: dict) -> dict:
        locs  = entities.get("LOC",  [])
        dates = entities.get("DATE", [])
        pickup_location  = locs[0]  if len(locs) > 0  else ""
        dropoff_location = locs[1]  if len(locs) > 1  else ""
        pickup_date      = dates[0] if dates           else ""
        dropoff_date     = dates[1] if len(dates) > 1  else None

        api_payload = {"pickup_location": pickup_location, "dropoff_location": dropoff_location, "pickup_date": pickup_date, "dropoff_date": dropoff_date}
        rag_query = f"location voiture {pickup_location} {dropoff_location}"
        steps = ["Choisissez votre véhicule", "Confirmez les options", "Procédez au paiement"]

        if self.response_type == "api":
            return await self._resolve_api(api_payload, rag_query, steps, "booking", "POST")
        elif self.response_type == "rag":
            return await self._resolve_rag_only(entities)
        elif self.response_type == "llm":
            prompt_override = self.subcat_config.get("llm_prompt_override", "")
            return await self._resolve_llm_only(entities, prompt_override)
        elif self.response_type == "human":
            contact = self.subcat_config.get("human_contact", "")
            return await self._resolve_human_only(contact)
        return await self._resolve_api(api_payload, rag_query, steps, "booking", "POST")

    async def _modify_flight(self, entities: dict) -> dict:
        flights = entities.get("FLIGHT", [])
        dates   = entities.get("DATE",   [])
        api_payload = {"flight_number": flights[0] if flights else None, "new_date": dates[0] if dates else None}
        rag_query = "modification vol conditions frais"
        steps = ["Vérifiez les conditions", "Des frais peuvent s'appliquer", "Confirmation modifiée"]

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

    async def _modify_car(self, entities: dict) -> dict:
        dates       = entities.get("DATE",   [])
        booking_ref = entities.get("FLIGHT", [])
        api_payload = {"booking_reference": booking_ref[0] if booking_ref else None, "new_pickup_date": dates[0] if dates else None}
        rag_query = "modification location voiture"
        steps = ["Vérifiez les conditions", "Des frais peuvent s'appliquer", "Confirmation modifiée"]

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


# =========================================================================
# POINT D'ENTRÉE MODULE
# =========================================================================

async def transport_agent(entities, language, tenant_config, sub_category=None,
                          tenant_id="", user_id="", session_id="",
                          response_type="api", subcat_config=None,
                          raw_text="", **kwargs) -> dict:
    agent = TransportAgent(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
    return await agent.run(entities, language, tenant_config, sub_category=sub_category,
                           tenant_id=tenant_id, user_id=user_id, session_id=session_id,
                           response_type=response_type, subcat_config=subcat_config,
                           raw_text=raw_text, **kwargs)