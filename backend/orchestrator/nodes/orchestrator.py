# orchestrator/nodes/orchestrator_node.py
import json
from core.streaming import emit  
from services.shield import shield
from services.redis import (
    get_redis_client, get_session_state,
    append_history, get_graph_state,
    get_slots_for_category, _translate_to_french,save_graph_state
)

from orchestrator.state import AgentState
import logging
from services.intents import detect_intent, RESPONSES
from services.profile_memory import profile_memory_service
from services.conversation_context import conversation_context_service

logger = logging.getLogger("chat_logger")


def _build_form_summary(form_data: dict) -> str:
    parts = []
    if form_data.get("city"):           parts.append(f"ville : {form_data['city']}")
    if form_data.get("destination"):    parts.append(f"destination : {form_data['destination']}")
    if form_data.get("origin"):         parts.append(f"origine : {form_data['origin']}")
    if form_data.get("check_in"):       parts.append(f"arrivée : {form_data['check_in']}")
    if form_data.get("check_out"):      parts.append(f"départ : {form_data['check_out']}")
    if form_data.get("departure_date"): parts.append(f"départ : {form_data['departure_date']}")
    if form_data.get("return_date"):    parts.append(f"retour : {form_data['return_date']}")
    if form_data.get("guests"):         parts.append(f"personnes : {form_data['guests']}")
    if form_data.get("adults"):         parts.append(f"adultes : {form_data['adults']}")
    if form_data.get("cabin_class"):    parts.append(f"classe : {form_data['cabin_class']}")
    if not parts:
        return "Formulaire soumis"
    return "Formulaire soumis — " + ", ".join(parts)


async def orchestrator_node(state: AgentState) -> AgentState:
    redis    = await get_redis_client()
    prefix   = f"{state['tenant_id']}:{state['user_id']}:{state['session_id']}"
    raw_text         = state.get("raw_text", "")
    form_data        = state.get("form_data", {})
    is_form_response = bool(form_data)
    context     = {}
    graph_state = {}

    is_anonymous = state.get("is_anonymous", False)

    profile = await profile_memory_service.load_profile(
        user_id=state["user_id"],
        tenant_id=state["tenant_id"],
        is_anonymous=is_anonymous,
        conversation_history=None,
    )

    profile_prompt = profile_memory_service.build_context_prompt(profile)
    if profile_prompt:
        logger.info(f"[Orchestrator] 👤 Profil chargé :\n{profile_prompt}")
    else:
        logger.info("[Orchestrator] 👤 Visiteur anonyme — pas de profil")

    conv_context = await conversation_context_service.load_conversation_context(
        user_id=state["user_id"],
        tenant_id=state["tenant_id"],
        session_id=state["session_id"],
        is_anonymous=is_anonymous,
        loyalty_summary=state.get("loyalty_summary", {}),
    )

    conv_context_prompt = conversation_context_service.build_context_prompt(conv_context)
    if conv_context_prompt:
        logger.info(f"[Orchestrator] 💬 Contexte conversation chargé :\n{conv_context_prompt}")

    if redis:
        from services.redis import debug_session
        debug_session(redis, state["session_id"], state["tenant_id"], state["user_id"])

        if is_form_response:
            form_summary = _build_form_summary(form_data)
            logger.info(f"[Orchestrator] 📋 form_data reçu : {form_data}")
            try:
                last_raw_text = redis.hget(f"{prefix}:meta", "last_raw_text")
                if last_raw_text:
                    state["raw_text"] = last_raw_text if isinstance(last_raw_text, str) else last_raw_text.decode("utf-8")
                                
                last_language = redis.hget(f"{prefix}:meta", "last_language")
                if last_language:
                    lang = last_language if isinstance(last_language, str) else last_language.decode("utf-8")
                    current_lang = state.get("language") or state.get("form_language")
                    if not current_lang or current_lang == "query_fr":
                        state["language"] = lang
                        logger.info(f"[Orchestrator] 🌐 Langue restaurée depuis Redis: {lang}")
                    else:
                        logger.info(f"[Orchestrator] 🌐 Langue formulaire conservée: {current_lang} (Redis: {lang})")
            except Exception:
                pass
            text_fields = ["city", "origin", "destination"]
            for field in text_fields:
                val = form_data.get(field, "")
                if val and isinstance(val, str) and any('\u0600' <= c <= '\u06ff' for c in val):
                    translated = _translate_to_french(val.strip(), "query_ar")
                    if translated != val:
                        logger.info(f"[Form] 🌐 Traduit {field}: '{val}' → '{translated}'")
                        form_data[field] = translated
            state["form_data"] = form_data
            append_history(
                redis, prefix,
                role     = "user",
                content  = form_summary,
                msg_type = None,
                intent   = state.get("classification", [{}])[0].get("sub_category"),
            )

            has_flight = any(k in form_data for k in (
                "origin", "destination", "departure_date", "return_date", "cabin_class", "adults"
            ))
            has_hotel = any(k in form_data for k in (
                "city", "check_in", "check_out", "rooms", "guests"
            ))

            FIELDS_TO_RESET = ["destination", "origine", "date_depart", "date_retour", "passagers"]
            cats_to_reset   = ["global"]
            if has_flight:
                flight_key = f"{prefix}:slots:flight"
                redis.hset(flight_key, "origine",     form_data.get("origin", ""))
                redis.hset(flight_key, "destination", form_data.get("destination", ""))
                redis.hset(flight_key, "date_depart", form_data.get("departure_date", ""))
                redis.hset(flight_key, "date_retour", form_data.get("return_date", ""))
                redis.hset(flight_key, "passagers",   str(form_data.get("adults", "")))
                redis.expire(flight_key, 3600)

            if has_hotel:
                hotel_key = f"{prefix}:slots:hotel"
                redis.hset(hotel_key, "destination", form_data.get("city", ""))
                redis.hset(hotel_key, "date_depart", form_data.get("check_in", ""))
                redis.hset(hotel_key, "date_retour", form_data.get("check_out", ""))
                redis.hset(hotel_key, "passagers",   str(form_data.get("guests", "")))
                redis.expire(hotel_key, 3600)

            for cat in cats_to_reset:
                slot_key = f"{prefix}:slots:{cat}"
                existing = redis.hgetall(slot_key)
                if existing:
                    for field in FIELDS_TO_RESET:
                        redis.hset(slot_key, field, "")
                    redis.expire(slot_key, 3600)

            for entity_type in ("LOC", "DATE", "PERSONS", "DATE_RANGE"):
                redis.delete(f"{prefix}:{entity_type}")

            logger.info(
                f"[Orchestrator] 🗑 Reset formulaire | "
                f"cats={cats_to_reset} | fields={FIELDS_TO_RESET}"
            )

        elif raw_text and not is_form_response:
            shield_result = shield(raw_text)
            if shield_result["status"] == "ok":
                append_history(
                    redis, prefix,
                    role     = "user",
                    content  = raw_text,
                    msg_type = None,
                    intent   = None,
                )
                redis.hset(f"{prefix}:meta", "last_raw_text", raw_text)
                redis.hset(f"{prefix}:meta", "last_language", state.get("language", "query_fr"))
                redis.hset(f"{prefix}:meta", "last_query_language", state.get("language", "query_fr"))
                redis.expire(f"{prefix}:meta", 3600)
        else:
            try:
                last_language = redis.hget(f"{prefix}:meta", "last_query_language") \
             or redis.hget(f"{prefix}:meta", "last_language")
                if last_language:
                    lang = last_language if isinstance(last_language, str) else last_language.decode("utf-8")
                    if not state.get("language") or state.get("language") == "query_fr":
                        state["language"] = lang
            except Exception:
                pass

        context = get_session_state(
            redis,
            state["session_id"],
            state["tenant_id"],
            state["user_id"],
        )

        graph_state = get_graph_state(redis, prefix)
        logger.info(
            f"[Orchestrator] 📖 State précédent | "
            f"node={graph_state.get('current_node')} | "
            f"intent={graph_state.get('last_intent')} | "
            f"waiting={graph_state.get('waiting_for')}"
        )

        # ── CAS waiting_for = clarification : forcer classification ─────────
        if (not is_form_response
            and graph_state.get("waiting_for") == "clarification"
            and graph_state.get("last_intent")):

            last_intent = graph_state.get("last_intent", [])
            if isinstance(last_intent, str):
                import json as _json
                try:    last_intent = _json.loads(last_intent)
                except: last_intent = [last_intent]

            try:
                from models.mdeberta_subcat import SUBCAT_TO_CAT
            except Exception:
                SUBCAT_TO_CAT = {}

            forced_classification = [
                {
                    "sub_category": intent,
                    "category":     SUBCAT_TO_CAT.get(intent, ""),
                    "confidence":   1.0,
                    "resolved_by":  "context_forced",
                }
                for intent in last_intent
            ]

            from models.ner_xlm import predict_ner
            ner_payload = predict_ner(raw_text)
            entities    = {}
            locs  = []
            dates = []

            for ent_type, values in ner_payload.items():
                if not values or not isinstance(values, list):
                    continue
                entities[ent_type] = values
                if ent_type == "LOC":  locs  = values
                if ent_type == "DATE": dates = values

            slot_cat = graph_state.get("slots_category", "global")

            if locs:
                entities["LOC"] = locs
                if slot_cat == "flight":
                    if len(locs) >= 2:
                        entities["origin"]      = locs[0]
                        entities["destination"] = locs[1]
                    elif len(locs) == 1:
                        existing_slots = get_slots_for_category(redis, prefix, "flight")
                        if existing_slots.get("origine"):
                            entities["destination"] = locs[0]
                        else:
                            entities["origin"] = locs[0]
                else:
                    from models.ner_xlm import detect_loc_context
                    ctx = detect_loc_context(raw_text, locs[0])
                    if ctx == "origin":
                        entities["origin"] = locs[0]
                    else:
                        entities["destination"] = locs[0]
                        entities["city"]        = locs[0]
                        entities["hotel_city"]  = locs[0]
                        entities["tour_city"]   = locs[0]
            if dates:
                entities["departure_date"] = dates[0]
                entities["check_in"]       = dates[0]
                entities["DATE"]           = dates
                if len(dates) > 1:
                    entities["return_date"] = dates[1]
                    entities["check_out"]   = dates[1]

            if entities and redis:
                slot_key = f"{prefix}:slots:{slot_cat}"
                
                if slot_cat == "flight":
                    if len(locs) >= 2:
                        redis.hset(slot_key, "origine",     locs[0])
                        redis.hset(slot_key, "destination", locs[1])
                    elif len(locs) == 1:
                        existing_slots = get_slots_for_category(redis, prefix, slot_cat)
                        if existing_slots.get("origine"):
                            redis.hset(slot_key, "destination", locs[0])
                        else:
                            redis.hset(slot_key, "origine", locs[0])
                else:
                    if locs:
                        redis.hset(slot_key, "destination", locs[0])

                if dates:
                    redis.hset(slot_key, "date_depart", dates[0])
                    if len(dates) > 1:
                        redis.hset(slot_key, "date_retour", dates[1])

                persons = entities.get("PERSONS", [])
                if persons:
                    redis.hset(slot_key, "passagers", persons[0])

                redis.expire(slot_key, 3600)
                logger.info(
                    f"[Orchestrator] 💾 Slots sauvegardés | "
                    f"cat={slot_cat} | entities={list(entities.keys())}"
                )

            save_graph_state(
                redis, prefix,
                current_node   = "resolving_clarification",
                last_intent    = last_intent,
                waiting_for    = None,
                slots_category = graph_state.get("slots_category", "global"),
            )

            logger.info(
                f"[Orchestrator] 🔒 Classification forcée depuis Redis : {last_intent} | "
                f"entities extraites : {list(entities.keys())}"
            )

            return {
                **state,
                "language":       state.get("language", "query_fr"), 
                "form_language":  state.get("form_language"),
                "classification": forced_classification,
                "entities":       entities,
                "context": {
                    **context,
                    "profile":             profile,
                    "profile_prompt":      profile_prompt,
                    "conversation":        conv_context,
                    "conv_context_prompt": conv_context_prompt,
                },
                "graph_state": graph_state,
            }

    # réponse statique, bypass graphe
    if not is_form_response:
        intent = detect_intent(raw_text)
        if intent:
            response = RESPONSES[intent]
            logger.info(f"[Orchestrator] ⚡ Intent='{intent}' → réponse statique, bypass graphe")

            await emit({
                "type":            "stream_done",
                "status":          "ok",
                "message":         response,
                "loyalty_summary": {},
                "follow_up":       "",
                "total_segments":  1,
            })

            return {
                **state,
                "language": state.get("language", "query_fr"),
                "form_language": state.get("form_language"),
                "context": {
                    **context,
                    "profile":             profile,
                    "profile_prompt":      profile_prompt,
                    "conversation":        conv_context,
                    "conv_context_prompt": conv_context_prompt,
                },
                "graph_state":    graph_state,
                "intent":         intent,
                "final_response": json.dumps({
                    "message": response,
                    "_meta":   {"intent": intent, "static": True}
                }, ensure_ascii=False),
            }

    return {
        **state,
        "language": state.get("language", "query_fr"),
        "form_language": state.get("form_language"),
        "context": {
            **context,
            "profile":             profile,
            "profile_prompt":      profile_prompt,
            "conversation":        conv_context,
            "conv_context_prompt": conv_context_prompt,
        },
        "graph_state": graph_state,
    }