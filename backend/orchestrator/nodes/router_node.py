# orchestrator/nodes/router_node.py
import asyncio
import uuid
from orchestrator.state import AgentState
from core.database import get_pool
from core.streaming import emit
from services.redis import _translate_to_french
import json
from orchestrator.nodes.agents.transport import transport_agent
from orchestrator.nodes.agents.stay import stay_agent
from orchestrator.nodes.agents.activity import activity_agent
from orchestrator.nodes.agents.discovery import discovery_agent
from orchestrator.nodes.agents.info import info_agent
from orchestrator.nodes.agents.security import security_agent
from orchestrator.nodes.agents.claims import claims_agent
from orchestrator.nodes.agents.logistics import logistics_agent
from orchestrator.nodes.agents.compliance import compliance_agent
from orchestrator.nodes.agents.assistance import assistance_agent
from orchestrator.nodes.agents.general import general_agent

import logging
logger = logging.getLogger("chat_logger")

# =============================================================================
# MAPPING DIRECT SOUS-CATÉGORIE → AGENT
# =============================================================================

SUBCATEGORY_TO_AGENT = {
    "Luggage & Safety":                  compliance_agent,
    "Special Accessibility Needs":       compliance_agent,
    "Vaccination & Health Requirements": compliance_agent,
    "Visa & Passport":                   compliance_agent,
    "Weather & Best Seasons":            info_agent,
    "Internet & Connectivity":           info_agent,
    "Cultural Norms":                    info_agent,
    "Car Rental Booking":                transport_agent,
    "Flight Booking":                    transport_agent,
    "Transportation Search":             transport_agent,
    "Car Rental Management":             transport_agent,
    "Flight Management":                 transport_agent,
    "Restaurant Reservation":            discovery_agent,
    "Gastronomy":                        discovery_agent,
    "Event Ticket Booking":              activity_agent,
    "Tour/Excursion Booking":            activity_agent,
    "Tour/Excursion Management":         activity_agent,
    "Hotel Booking":                     stay_agent,
    "Hotel Management":                  stay_agent,
    "Emergency Contacts":                security_agent,
    "Fraud Prevention":                  security_agent,
    "Lost Or Stolen Items":              security_agent,
    "Insurance & Refund Policies":       security_agent,
    "Flight Disruption":                 claims_agent,
    "Overbooking":                       claims_agent,
    "Baggage Damage":                    claims_agent,
    "Complaint Submission":              claims_agent,
    "Airport Logistics":                 logistics_agent,
    "Check-In":                          logistics_agent,
    "Late Check-Out":                    logistics_agent,
    "Child Services":                    assistance_agent,
    "Pet Policy":                        assistance_agent,
    "Agency Details & Contact": general_agent,
    "Chatbot Capabilities":     general_agent,
    "Human Handoff":            general_agent,
    "Feedback & Reviews":       general_agent,

}


REQUIRED_ENTITIES = {
    "Hotel Booking":          ["city"],
    "Flight Booking":         ["origin", "destination"],
    "Tour/Excursion Booking": ["city"],
    "Car Rental Booking":     ["city"],
    "Restaurant Reservation": ["city"],
}

# =============================================================================
# SLOTS PAR CATÉGORIE
# =============================================================================

SLOT_FIELDS = {
    "hotel": {
        "city":        ["hotel_city", "city", "destination"],
        "check_in":    ["check_in", "departure_date"],
        "check_out":   ["check_out", "return_date"],
        "guests":      ["guests", "adults"],
        "budget":      ["price_max", "budget"],     
    },
    "flight": {
        "origin":         ["origin"],
        "destination":    ["destination"],
        "departure_date": ["departure_date", "check_in"],
        "return_date":    ["return_date", "check_out"],
        "adults":         ["adults", "guests"],
        "budget":         ["price_max", "budget"],   
    },
    "tour": {
        "city":           ["tour_city", "city", "destination"],
        "departure_date": ["tour_departure_date", "departure_date"],
    },
    "car": {
        "city":           ["city", "destination"],
        "departure_date": ["departure_date"],
    },
    "restaurant": {
        "city":           ["city", "destination"],
    },
}

SUBCAT_TO_SLOT_CATEGORY = {
    "Hotel Booking":          "hotel",
    "Hotel Management":       "hotel",
    "Late Check-Out":         "hotel",
    "Flight Booking":         "flight",
    "Flight Management":      "flight",
    "Car Rental Booking":     "car",
    "Car Rental Management":  "car",
    "Tour/Excursion Booking": "tour",
    "Tour/Excursion Management": "tour",
    "Restaurant Reservation": "restaurant",
    "Airport Logistics":      "flight",
    "Check-In":               "global",
}

# =============================================================================
# SOUS-CATÉGORIES DE MANAGEMENT
# =============================================================================

MANAGEMENT_SUBCATS = {
    "Hotel Management",
    "Flight Management",
    "Tour/Excursion Management",
    "Car Rental Management",
    "Late Check-Out",
    "Flight Disruption",
    "Overbooking",
    "Baggage Damage",
}

MANAGEMENT_REQUIRED = {
    "Hotel Management":          ["city"],
    "Late Check-Out":            ["city"],
    "Flight Management":         ["origin", "destination"],
    "Flight Disruption":         ["origin", "destination"],
    "Overbooking":               ["origin", "destination"],
    "Tour/Excursion Management": ["city"],
    "Car Rental Management":     ["city"],
    "Baggage Damage":            [],  
}

MANAGEMENT_FIELD_LABELS = {
    "city":           "la destination",
    "origin":         "la ville de départ",
    "destination":    "la ville d'arrivée",
    "check_in":       "la date d'arrivée",
    "departure_date": "la date de départ",
}


def _will_trigger_form(sub_category: str, entities: dict) -> bool:
    """
    Retourne True si l'agent va demander un formulaire (entités obligatoires manquantes).
    Dans ce cas on ne doit PAS injecter les slots — laisser le formulaire se déclencher.
    """
    required = REQUIRED_ENTITIES.get(sub_category)
    if not required:
        return False  

    locs  = entities.get("LOC", [])
    dates = entities.get("DATE", [])
    city  = (
        entities.get("tour_city", [None])[0]
        or entities.get("hotel_city")
        or entities.get("city", [None])[0] if isinstance(entities.get("city"), list)
        else entities.get("city")
        or locs[0] if locs else None
    )
    origin      = entities.get("origin") or (locs[0] if len(locs) > 0 else None)
    destination = entities.get("destination") or (locs[1] if len(locs) > 1 else None)

    for field in required:
        if field == "city" and not city:
            return True
        if field == "origin" and not origin:
            return True
        if field == "destination" and not destination:
            return True

    return False

def _filter_entities_for_category(entities: dict, slot_category: str) -> dict:
    """
    Retourne uniquement les entités pertinentes selon la catégorie.
    Évite de passer les dates vol à l'agent hôtel et vice versa.
    """
    entities = dict(entities)

    if slot_category == "hotel":
        for k in ("origin", "departure_date", "cabin_class"):
            entities.pop(k, None)
        if not entities.get("departure_date") and entities.get("check_in"):
            entities["departure_date"] = entities["check_in"]

    elif slot_category == "flight":
        for k in ("city", "hotel_city", "check_in", "check_out", "rooms", "guests"):
            entities.pop(k, None)

    return entities


def _inject_slots_into_entities(
    entities:      dict,
    slots:         dict,
    slot_category: str,
    language:      str = "query_fr",  
) -> dict:
    if not slots:
        return entities
    
    def _clean_slot_value(val: str) -> str:
        if val and any('\u0600' <= c <= '\u06ff' for c in val):
            return _translate_to_french(val, "query_ar")
        return val

    slots = {k: _clean_slot_value(v) if isinstance(v, str) else v 
             for k, v in slots.items()}

    entities = dict(entities)
    locs  = list(entities.get("LOC", []))
    dates = list(entities.get("DATE", []))
    field_map = SLOT_FIELDS.get(slot_category, {})

    # ── Destination / city ────────────────────────────────────────────
    dest_slot = slots.get("destination", "")
    if dest_slot:
        city_keys = field_map.get("city", ["city", "destination"])
        has_valid_city = any(
            entities.get(k) and not _needs_translation(entities.get(k), language)
            for k in city_keys
        ) or any(l for l in locs if not _needs_translation(l, language))

        if not has_valid_city:
            locs = [dest_slot] + [l for l in locs if not _needs_translation(l, language)]
            for k in city_keys:
                entities[k] = dest_slot
            logger.info(f"[Router:slots] 📍 Injecté destination='{dest_slot}' depuis slots:{slot_category}")

    # ── Origine (vol) ─────────────────────────────────────────────────
    if slot_category == "flight":
        origine_slot = slots.get("origine", "")
        has_valid_origin = (
            entities.get("origin") and 
            not _needs_translation(entities.get("origin"), language)
        )
        if origine_slot and not has_valid_origin:
            entities["origin"] = origine_slot
            if not any(l == origine_slot for l in locs):
                locs.append(origine_slot)
            logger.info(f"[Router:slots] 📍 Injecté origin='{origine_slot}' depuis slots:flight")

    # ── Dates ─────────────────────────────────────────────────────────
    date_depart = slots.get("date_depart", "")
    date_retour = slots.get("date_retour", "")

    checkin_keys  = field_map.get("check_in",  ["check_in",  "departure_date"])
    checkout_keys = field_map.get("check_out", ["check_out", "return_date"])

    has_valid_date_in = any(
        entities.get(k) and not _needs_translation(entities.get(k), language)
        for k in checkin_keys
    ) or any(d for d in dates if not _needs_translation(d, language))

    has_valid_date_out = any(
        entities.get(k) and not _needs_translation(entities.get(k), language)
        for k in checkout_keys
    ) or len([d for d in dates if not _needs_translation(d, language)]) > 1

    if date_depart and not has_valid_date_in:
        clean_dates = [d for d in dates if not _needs_translation(d, language)]
        clean_dates.insert(0, date_depart)
        dates = clean_dates
        for k in checkin_keys:
            entities[k] = date_depart
        logger.info(f"[Router:slots] 📅 Injecté date_depart='{date_depart}' depuis slots:{slot_category}")

    if date_retour and not has_valid_date_out:
        clean_dates = [d for d in dates if not _needs_translation(d, language)]
        if len(clean_dates) < 2:
            clean_dates.append(date_retour)
        dates = clean_dates
        for k in checkout_keys:
            entities[k] = date_retour
        logger.info(f"[Router:slots] 📅 Injecté date_retour='{date_retour}' depuis slots:{slot_category}")

    # ── Passagers ─────────────────────────────────────────────────────
    passagers = slots.get("passagers", "")
    guest_keys = field_map.get("guests", ["guests", "adults"])
    has_guests = any(entities.get(k) for k in guest_keys)

    if passagers and not has_guests:
        for k in guest_keys:
            entities[k] = [passagers]
        entities["PERSONS"] = [passagers]
        logger.info(f"[Router:slots] 👥 Injecté passagers='{passagers}' depuis slots:{slot_category}")
    # ── Budget ────────────────────────────────────────────────────────
    budget = slots.get("budget", "")
    budget_keys = field_map.get("budget", ["price_max", "budget"])
    has_budget = any(entities.get(k) for k in budget_keys)

    if budget and not has_budget:
        for k in budget_keys:
            entities[k] = budget
        entities["PRICE"] = [budget]
        logger.info(f"[Router:slots] 💰 Injecté budget='{budget}' depuis slots:{slot_category}")

    entities["LOC"]  = [l for l in locs  if l and not _needs_translation(l, language)]
    entities["DATE"] = [d for d in dates if d and not _needs_translation(d, language)]

    return entities


def _needs_translation(value: str, language: str) -> bool:
    """
    Retourne True si la valeur doit être remplacée par le slot traduit.
    Basé sur la langue de la requête — plus fiable que la détection caractère.
    """
    if not value or not isinstance(value, str):
        return True  
    
    if language in ("query_ar", "query_derja_a"):
        return True
    
    return False

def _check_management_context(sub_category: str, entities: dict) -> dict:
    """
    Vérifie si le contexte est suffisant pour un agent de management.
    Retourne {"ok": True} ou {"ok": False, "missing": [...]}
    """
    required = MANAGEMENT_REQUIRED.get(sub_category, [])
    if not required:
        return {"ok": True}

    locs = entities.get("LOC", [])
    city = (
        entities.get("hotel_city") or
        entities.get("tour_city") or
        entities.get("city") or
        (locs[0] if locs else None)
    )
    origin      = entities.get("origin")      or (locs[0] if locs else None)
    destination = entities.get("destination") or (locs[1] if len(locs) > 1 else None)

    available = {"city": city, "origin": origin, "destination": destination}
    missing   = [f for f in required if not available.get(f)]

    if missing:
        return {"ok": False, "missing": missing}
    return {"ok": True}


async def load_tenant_subcategory_config(tenant_id: str) -> dict:
    if not tenant_id:
        return {}
    try:
        pool = await get_pool()
        rows = await pool.fetch(
            """
            SELECT sub_category, is_active, response_type,
                llm_prompt_override, human_contact, agent_type 
            FROM tenant_subcategory_config
            WHERE tenant_id = $1
            """,
            uuid.UUID(tenant_id)
        )
        return {
            row["sub_category"]: {
                "is_active":           row["is_active"],
                "response_type":       row["response_type"],
                "llm_prompt_override": row["llm_prompt_override"],
                "human_contact":       row["human_contact"],
                "agent_type":          row["agent_type"], 
            }
            for row in rows
        }
    except Exception as e:
        print(f"[Router] ❌ Erreur chargement config tenant: {e}")
        return {}


# =============================================================================
# ROUTER NODE
# =============================================================================

async def router_node(state: AgentState) -> AgentState:
    classification       = state.get("classification", [])
    tenant_id            = state.get("tenant_id", "")
    tenant_config        = state.get("tenant_config", {})
    tenant_subcat_config = await load_tenant_subcategory_config(tenant_id)

    active_subcategories = [
        k for k, v in tenant_subcat_config.items()
        if isinstance(v, dict) and v.get("is_active")
    ]

    print(f"[Router] classification reçue: {classification}")
    print(f"[Router] entities: {list(state.get('entities', {}).keys())}")
    print(f"[Router] tenant_subcat_config keys: {list(tenant_subcat_config.keys())}")
    print(f"[Router] active_subcategories ({len(active_subcategories)}): {active_subcategories}")

    redis  = None
    prefix = None
    try:
        from services.redis import get_redis_client, get_slots_for_category
        redis  = await get_redis_client()
        prefix = f"{state.get('tenant_id')}:{state.get('user_id')}:{state.get('session_id')}"
    except Exception as e:
        logger.warning(f"[Router] Redis non disponible : {e}")

    coros: dict[str, object] = {}
    results: dict = {}
    for item in classification:
        sub_category = item.get("sub_category", "")
        category     = item.get("category", "")
        key          = f"{category}:{sub_category}"
            
        language = _normalize_language(state.get("language", "query_fr"))


        print(f"[Router] traitement item: sub_category='{sub_category}' | category='{category}' | key='{key}'")

        segment_entities = dict(item.get("entities") or state.get("entities", {}))
        slot_category = SUBCAT_TO_SLOT_CATEGORY.get(sub_category, "global")
        segment_entities = _filter_entities_for_category(segment_entities, slot_category)
        segment_text     = item.get("segment") or state.get("raw_text", "")

        if redis and prefix:
            slot_category = SUBCAT_TO_SLOT_CATEGORY.get(sub_category, "global")

            if not _will_trigger_form(sub_category, segment_entities):
                try:
                    from services.redis import get_slots_for_category
                    slots = get_slots_for_category(redis, prefix, slot_category)

                    if any(slots.get(k) for k in ("destination", "date_depart", "passagers")):
                        logger.info(
                            f"[Router:slots] 💉 Injection slots:{slot_category} "
                            f"pour '{sub_category}' | slots={slots}"
                        )
                        segment_entities = _inject_slots_into_entities(
                            segment_entities, slots, slot_category,language=language
                        )
                    else:
                        logger.info(
                            f"[Router:slots] ⏭ Slots:{slot_category} vides "
                            f"→ pas d'injection pour '{sub_category}'"
                        )
                except Exception as e:
                    logger.warning(f"[Router:slots] ❌ Injection échouée : {e}")
            else:
                logger.info(
                    f"[Router:slots] ⏭ need_more_info prévu pour '{sub_category}' "
                    f"→ pas d'injection slots"
                )

        if sub_category in MANAGEMENT_SUBCATS and redis and prefix:
            slot_category = SUBCAT_TO_SLOT_CATEGORY.get(sub_category, "global")
            try:
                slots = get_slots_for_category(redis, prefix, slot_category)
                specific_raw = redis.hgetall(f"{prefix}:slots:{slot_category}") or {}
                has_specific = any(specific_raw.get(k) for k in ("destination", "origine", "city"))

                if not has_specific:
                    try:
                        booking_type = (
                            "flight" if slot_category == "flight" else
                            "hotel"  if slot_category == "hotel"  else
                            "tour"   if slot_category == "tour"   else
                            "car"    if slot_category == "car"    else
                            slot_category
                        )
                        pool = await get_pool()
                        last_booking = await pool.fetchrow(
                            """
                            SELECT * FROM user_bookings
                            WHERE user_id      = $1
                            AND   tenant_id    = $2
                            AND   booking_type = $3
                            AND   session_id   = $4
                            AND   status != 'cancelled'
                            ORDER BY created_at DESC LIMIT 1
                            """,
                            uuid.UUID(state.get("user_id")),
                            uuid.UUID(state.get("tenant_id")),
                            booking_type,
                            state.get("session_id"),
                        )

                        if not last_booking:
                            last_booking = await pool.fetchrow(
                                """
                                SELECT * FROM user_bookings
                                WHERE user_id      = $1
                                AND   tenant_id    = $2
                                AND   booking_type = $3
                                AND   status != 'cancelled'
                                ORDER BY created_at DESC LIMIT 1
                                """,
                                uuid.UUID(state.get("user_id")),
                                uuid.UUID(state.get("tenant_id")),
                                booking_type,
                            )

                        if last_booking:
                            b = dict(last_booking)
                            if booking_type == "flight":
                                if b.get("origin"):
                                    slots["origine"]           = b["origin"]
                                    segment_entities["origin"] = b["origin"]
                                if b.get("destination"):
                                    slots["destination"]            = b["destination"]
                                    segment_entities["destination"] = b["destination"]
                                if b.get("departure_date"):
                                    slots["date_depart"] = str(b["departure_date"])
                                locs = []
                                if b.get("origin"):      locs.append(b["origin"])
                                if b.get("destination"): locs.append(b["destination"])
                                if locs:
                                    segment_entities["LOC"] = locs
                            else:
                                city = b.get("city") or b.get("destination") or b.get("activity_city")
                                if city:
                                    slots["destination"]            = city
                                    slots["city"]                   = city
                                    segment_entities["city"]        = city
                                    segment_entities["hotel_city"]  = city
                                    segment_entities["tour_city"]   = city
                                    segment_entities["destination"] = city
                                    if not segment_entities.get("LOC"):
                                        segment_entities["LOC"] = [city]
                                date = b.get("check_in") or b.get("activity_date")
                                if date:
                                    slots["date_depart"] = str(date)

                            logger.info(
                                f"[Router:management] 🗄 Contexte depuis DB | "
                                f"type={booking_type} | "
                                f"city={slots.get('city')} | "
                                f"origine={slots.get('origine')} | "
                                f"destination={slots.get('destination')}"
                            )
                    except Exception as e:
                        logger.warning(f"[Router:management] ❌ DB lookup: {e}")

                if has_specific:
                    segment_entities = _inject_slots_into_entities(
                        segment_entities, slots, slot_category,language=language
                    )
                    logger.info(
                        f"[Router:management] 💉 Slots Redis propres injectés | "
                        f"cat={slot_category} | slots={slots}"
                    )
                else:
                    logger.info(
                        f"[Router:management] 💉 Contexte DB injecté directement | "
                        f"cat={slot_category} | "
                        f"city={segment_entities.get('city')} | "
                        f"origin={segment_entities.get('origin')} | "
                        f"destination={segment_entities.get('destination')}"
                    )

            except Exception as e:
                logger.warning(f"[Router:management] ❌ Injection slots: {e}")

            ctx = _check_management_context(sub_category, segment_entities)
            if not ctx["ok"]:
                try:
                    from services.redis import save_graph_state
                    slot_cat = SUBCAT_TO_SLOT_CATEGORY.get(sub_category, "global")
                    save_graph_state(
                        redis, prefix,
                        current_node   = "waiting_clarification",
                        last_intent    = [sub_category],
                        waiting_for    = "clarification",
                        slots_category = slot_cat,
                    )
                    logger.info(
                        f"[Router:management] 💾 waiting_for=clarification | "
                        f"intent={sub_category} | missing={ctx['missing']}"
                    )
                except Exception as e:
                    logger.warning(f"[Router:management] ❌ Redis save : {e}")

                miss_str = " et ".join(
                    MANAGEMENT_FIELD_LABELS.get(f, f) for f in ctx["missing"]
                )
                results[key] = {
                    "status":         "need_more_info",
                    "form_type":      "natural",
                    "missing_fields": ctx["missing"],
                    "missing_str":    miss_str,
                    "sub_category":   sub_category,
                    "message":        "",
                    "user_id":        state.get("user_id", ""),
                    "session_id":     state.get("session_id", ""),
                }
                logger.info(
                    f"[Router:management] ⏭ Contexte insuffisant pour '{sub_category}' "
                    f"→ question naturelle | missing={ctx['missing']}"
                )
                continue

        # ── Config tenant ─────────────────────────────────────────────────
        subcat_config = tenant_subcat_config.get(sub_category, {})
        print(f"[Router] subcat_config pour '{sub_category}': {subcat_config}")
        print(f"[Router] is_active: {subcat_config.get('is_active', True)}")

        if not subcat_config.get("is_active", True):
            message = "Le service n'est pas disponible pour votre agence. Contactez-nous pour plus d'informations."
            await emit({
                "type":            "stream_done",
                "status":          "disabled",
                "message":         message,
                "loyalty_summary": {},
                "follow_up":       "",
                "total_segments":  1,
            })
            return {
                **state,
                "results":              {},
                "active_subcategories": active_subcategories,
                "final_response": json.dumps({
                    "message": message,
                    "_meta":   {"status": "disabled", "sub_category": sub_category}
                }, ensure_ascii=False),
            }

        agent_func = SUBCATEGORY_TO_AGENT.get(sub_category)
        print(f"[Router] agent_func pour '{sub_category}': {agent_func}")

        if not agent_func:
            continue

        response_type = subcat_config.get("response_type", "api")
        coros[key] = agent_func(
            entities=segment_entities,
            language=language,
            tenant_config=tenant_config,
            sub_category=sub_category,
            tenant_id=tenant_id,
            user_id=state.get("user_id", ""),
            session_id=state.get("session_id", ""),
            response_type=response_type,
            subcat_config=subcat_config,
            raw_text=segment_text,
        )

    print(f"[Router] coros finaux: {list(coros.keys())}")

    if not coros:
        if results:
            return {**state, "results": results, "active_subcategories": active_subcategories}
        return {**state, "results": {}, "active_subcategories": active_subcategories}

    for key in coros:
        category, sub_cat = key.split(":", 1)
        await emit({
            "type":         "status",
            "step":         "agent_start",
            "segment_key":  key,
            "sub_category": sub_cat,
            "message":      _search_label(sub_cat, language)
        })

    async def _run(k: str, coro):
        result = await coro
        return k, result

    tasks = [asyncio.create_task(_run(k, c)) for k, c in coros.items()]

    for fut in asyncio.as_completed(tasks):
        try:
            key, result = await fut
        except Exception as e:
            print(f"[Router] ❌ Agent error: {e}")
            continue

        results[key] = result

        if isinstance(result, dict) and result.get("status") in (
            "success", "gemini_fallback", "rag_only", "llm_only"
        ):
            _, sub_cat = key.split(":", 1)
            await emit({
                "type":         "segment_result",
                "segment_key":  key,
                "sub_category": sub_cat,
                "data":         result
            })

    return {**state, "results": results, "active_subcategories": active_subcategories}

def _normalize_language(language: str) -> str:
    """Normalise les variantes de langue vers les codes standards."""
    if language in ("query_derja_a", "query_derja_l"):
        return "query_ar"
    return language

def _search_label(sub_category: str, language: str) -> str:
    labels = {
        "query_fr": {
            "Flight Booking":           "Recherche de vols...",
            "Hotel Booking":            "Recherche d'hôtels...",
            "Restaurant Reservation":   "Recherche de restaurants...",
            "Tour/Excursion Booking":   "Recherche de voyages...",
            "Car Rental Booking":       "Recherche de voitures...",
            "Transportation Search":    "Recherche de transports...",
            "Event Ticket Booking":     "Recherche de billets...",
            "Visa & Passport":          "Vérification visas...",
            "Weather & Best Seasons":   "Consultation météo...",
            "Cultural Norms":           "Recherche culturelle...",
            "Luggage & Safety":         "Informations bagages...",
            "Emergency Contacts":       "Contacts urgence...",
            "Insurance & Refund":       "Informations assurances...",
            "Flight Disruption":        "Vérification vol...",
            "Baggage Damage":           "Assistance bagages...",
            "Airport Logistics":        "Logistique aéroport...",
            "Check-In":                 "Préparation enregistrement...",
            "Late Check-Out":           "Vérification horaires...",
            "Pet Policy":               "Règlement animaux...",
            "Child Services":           "Services enfants...",
            "Hotel Management":         "Gestion réservation hôtel...",
            "Flight Management":        "Gestion réservation vol...",
            "Tour/Excursion Management":"Gestion réservation de voyage...",
            "Agency Details & Contact": "Informations agence...",
            "Chatbot Capabilities":     "Capacités chatbot...",
            "Human Handoff":            "Transfert humain...",
            "Feedback & Reviews":       "Avis et commentaires...",
        },
        "query_en": {
            "Flight Booking":           "Searching flights...",
            "Hotel Booking":            "Searching hotels...",
            "Restaurant Reservation":   "Searching restaurants...",
            "Tour/Excursion Booking":   "Searching activities...",
            "Hotel Management":         "Managing hotel booking...",
            "Flight Management":        "Managing flight booking...",
            "Agency Details & Contact": "Managing agency details...",
            "Chatbot Capabilities":     "Managing chatbot capabilities...",
            "Human Handoff":            "Managing human handoff...",
            "Feedback & Reviews":       "Managing feedback and reviews...",
            "Car Rental Booking":       "Searching car rentals...",
            "Transportation Search":    "Searching transportation...",
            "Event Ticket Booking":     "Searching event tickets...",
            "Visa & Passport":          "Checking visa requirements...",
            "Weather & Best Seasons":   "Checking weather and seasons...",
            "Cultural Norms":           "Checking cultural norms...",
            "Luggage & Safety":         "Checking luggage and safety info...",
            "Emergency Contacts":       "Checking emergency contacts...",
            "Insurance & Refund":       "Checking insurance and refund policies...",
            "Flight Disruption":        "Checking flight status...",
            "Baggage Damage":           "Assisting with baggage issues...",
            "Airport Logistics":        "Checking airport logistics...",
            "Check-In":                 "Preparing for check-in...",
            "Late Check-Out":           "Checking late check-out policies...",
            "Pet Policy":               "Checking pet policies...",
            "Child Services":           "Checking child services...",
            "Tour/Excursion Management":"Managing tour/excursion booking...",
            "Car Rental Management":    "Managing car rental booking...",
        },
        "query_ar": {
            "Flight Booking":           "البحث عن رحلات طيران...",
            "Hotel Booking":            "البحث عن فنادق...",
            "Restaurant Reservation":   "البحث عن مطاعم...",
            "Tour/Excursion Booking":   "البحث عن أنشطة سياحية...",
            "Car Rental Booking":       "البحث عن تأجير سيارات...",
            "Transportation Search":    "البحث عن وسائل نقل...",
            "Event Ticket Booking":     "البحث عن تذاكر فعاليات...",
            "Visa & Passport":          "التحقق من متطلبات التأشيرة...",
            "Weather & Best Seasons":   "التحقق من الطقس والفصول المثالية...",
            "Cultural Norms":           "البحث عن العادات الثقافية...",
            "Luggage & Safety":         "معلومات الأمتعة والسلامة...",
            "Emergency Contacts":       "جهات الاتصال للطوارئ...",
            "Insurance & Refund":       "معلومات التأمين واسترداد الأموال...",
            "Flight Disruption":        "التحقق من حالة الرحلة...",
            "Baggage Damage":           "المساعدة في مشاكل الأمتعة...",
            "Airport Logistics":        "الخدمات اللوجستية للمطار...",
            "Check-In":                 "التحضير لتسجيل الدخول...",
            "Late Check-Out":           "التحقق من سياسات المغادرة المتأخرة...",
            "Pet Policy":               "سياسة الحيوانات الأليفة...",
            "Child Services":           "خدمات الأطفال...",
            "Hotel Management":         "إدارة حجز الفندق...",
            "Flight Management":        "إدارة حجز الرحلة...",
            "Tour/Excursion Management":"إدارة حجز الجولة\/النشاط...",
            "Car Rental Management":    "إدارة حجز تأجير السيارات...",
            "Agency Details & Contact": "تفاصيل الوكالة ومعلومات الاتصال...",
            "Chatbot Capabilities":     "إمكانيات الدردشة الآلية...",
            "Human Handoff":            "تحويل إلى موظف بشري...",
            "Feedback & Reviews":       "الآراء والتقييمات...",
        }
    }

    lang_map = labels.get(language, labels["query_fr"])
    if sub_category in lang_map:
        return lang_map[sub_category]

    generic = {
        "query_fr": f"Recherche {sub_category}...",
        "query_en": f"Searching {sub_category}...",
        "query_ar": f"بحث {sub_category}...",
    }
    return generic.get(language, f"Searching {sub_category}...")


