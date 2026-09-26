# orchestrator/nodes/classifier_node.py

import json
import hashlib
import logging
from core.streaming import emit
import asyncio
from services.gpt import (
    ask_gpt_clarification, ask_gpt_correct_classification,
    ask_gpt_resolve,
    translate_to_en,
    SUBCATS_MAP,
)
from services.redis import (
    get_redis_client, store_entities_pipeline,
    subcat_to_slot_key, reset_slots_for_category
)
from orchestrator.state import AgentState
from datetime import date as _date
logger = logging.getLogger("chat_logger")

SCORE_HIGH = 0.8
SCORE_LOW  = 0.5
CACHE_TTL_TRANSLATION = 60 * 60 * 24 * 7


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def make_key(prefix: str, text: str) -> str:
    return f"{prefix}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _flatten_entities(ner_payload: dict) -> list:
    result = []
    for entity_type, values in ner_payload.items():
        if entity_type == "DATE_RANGE":
            continue
        if values is None:
            continue
        if not isinstance(values, list):
            continue
        for v in values:
            result.append({"entity": entity_type, "value": v})
    return result


async def _translate_for_model(seg: str, redis) -> str:
    cache_key = f"translation:{hashlib.sha256(seg.encode()).hexdigest()}"
    try:
        cached = await redis.get(cache_key)
        if cached:
            import json as _json
            logger.info(f"[Redis] Cache HIT pour {cache_key}")
            data = _json.loads(cached)
            logger.info(f"[Redis] Contenu: {data}")
            if "translated" in data:
                return data["translated"]
        else:
            logger.info(f"[Redis] Cache MISS pour {cache_key}")
    except Exception:
        pass

    translated = translate_to_en(seg)

    try:
        await redis.set(
            cache_key,
            json.dumps({"translated": translated}, ensure_ascii=False),
            ex=CACHE_TTL_TRANSLATION,
        )
    except Exception:
        pass

    return translated


def _all_subcats_list() -> list[dict]:
    flat = []
    idx  = 0
    for cat, subcats in SUBCATS_MAP.items():
        for sub in subcats:
            flat.append({"id": idx, "label": sub, "category": cat})
            idx += 1
    return flat


def _extract_entities(
    segments:    list[str],
    session_id:  str,
    tenant_id:   str,
    user_id:     str,
    redis_client,
    language:    str = "query_fr",
) -> dict:
    all_entities: dict = {}
    from models.ner_xlm import predict_ner

    segments_ner = []
    for seg in segments:
        ner_payload   = predict_ner(seg)
        entities_list = []

        for ent_type, values in ner_payload.items():
            for v in values:
                if isinstance(v, dict):
                    if ent_type == "DATE_RANGE":
                        all_entities.setdefault(ent_type, [])
                        if v not in all_entities[ent_type]:
                            all_entities[ent_type].append(v)
                        continue
                    v = v.get("value", "")
                if not isinstance(v, str):
                    v = str(v)
                all_entities.setdefault(ent_type, [])
                if v not in all_entities[ent_type]:
                    all_entities[ent_type].append(v)
                    entities_list.append({"entity": ent_type, "value": v})

        loc_count = sum(1 for e in entities_list if e["entity"] == "LOC")
        segments_ner.append({
            "seg":       seg,
            "entities":  entities_list,
            "loc_count": loc_count,
        })
        logger.info(
            f"[NER] Segment '{seg[:40]}' → "
            f"{len(entities_list)} entités | {loc_count} LOC"
        )

    segments_ner.sort(key=lambda x: x["loc_count"])
    logger.info(
        f"[NER] Ordre de stockage : "
        f"{[s['loc_count'] for s in segments_ner]} LOC par segment"
    )

    if redis_client:
        for item in segments_ner:
            if not item["entities"]:
                continue
            try:
                logger.info(
                    f"[NER] store_entities_pipeline | "
                    f"loc_count={item['loc_count']} | "
                    f"entities={item['entities']}"
                )
                store_entities_pipeline(
                    redis_client, session_id, item["entities"],
                    tenant_id, user_id, item["seg"],
                    language=language,
                    category="global",
                )
            except Exception as e:
                logger.warning(
                    f"[NER] Redis store échoué : {e}", exc_info=True
                )

    date_range = all_entities.get("DATE_RANGE", [])
    if date_range and isinstance(date_range[0], dict):
        dr = date_range[0]
        all_entities["DATE"] = [dr["departure_date"], dr["return_date"]]

    from models.ner_xlm import detect_loc_context

    locs  = all_entities.get("LOC",  [])
    dates = all_entities.get("DATE", [])
    raw_seg = segments[0] if segments else ""

    if len(locs) == 0:
        all_entities["origin"]      = None
        all_entities["destination"] = None

    elif len(locs) == 1:
        context = detect_loc_context(raw_seg, locs[0])
        if context == "origin":
            all_entities["origin"]      = locs[0]
            all_entities["destination"] = None
        else:
            all_entities["origin"]      = None
            all_entities["destination"] = locs[0]
        logger.info(f"[NER] LOC unique '{locs[0]}' → context={context}")

    else:
        ctx0 = detect_loc_context(raw_seg, locs[0])
        ctx1 = detect_loc_context(raw_seg, locs[1])

        if ctx0 == "origin" and ctx1 == "destination":
            all_entities["origin"]      = locs[0]
            all_entities["destination"] = locs[1]
        elif ctx0 == "destination" and ctx1 == "origin":
            all_entities["origin"]      = locs[1]
            all_entities["destination"] = locs[0]
        else:
            # Défaut : ordre naturel
            all_entities["origin"]      = locs[0]
            all_entities["destination"] = locs[1]
        logger.info(f"[NER] 2 LOC → origin={all_entities['origin']} | destination={all_entities['destination']}")
    today = _date.today().isoformat()
    dates = list(dict.fromkeys(d for d in dates if d != today)) or dates
    dates = sorted(dates)
    all_entities["DATE"]           = dates
    all_entities["departure_date"] = dates[0] if len(dates) > 0 else None
    all_entities["return_date"]    = dates[1] if len(dates) > 1 else None

    return all_entities


# ══════════════════════════════════════════════════════════════════════════════
# DURATION — résolution post-NER
# ══════════════════════════════════════════════════════════════════════════════

async def _resolve_duration_if_needed(
    seg_entities: dict,
    item:         dict,
    session_id:   str,
    tenant_id:    str,
    user_id:      str,
    redis_client,
) -> dict:
    """
    Si le NER a détecté une DURATION mais pas de dates complètes,
    calcule departure_date et return_date puis les injecte dans seg_entities.
    Modifie seg_entities en place ET retourne le dict modifié.
    """
    from services.duration import parse_duration_to_days, resolve_duration_dates
    from services.redis import subcat_to_slot_key
    from core.database import get_pool

    durations = seg_entities.get("DURATION", [])
    if not durations:
        return seg_entities

    # Pas besoin de calculer si les deux dates sont déjà présentes
    dep_already = seg_entities.get("departure_date")
    ret_already = seg_entities.get("return_date")
    if dep_already and ret_already:
        logger.info(
            f"[Duration] DURATION présent mais dates déjà extraites "
            f"({dep_already} → {ret_already}) → skip"
        )
        return seg_entities

    duration_str  = durations[0]
    duration_days = parse_duration_to_days(duration_str)

    if duration_days <= 0:
        logger.warning(f"[Duration] parse_duration_to_days('{duration_str}') = 0 → skip")
        return seg_entities

    # Catégorie courante ("hotel", "flight", …)
    current_category = subcat_to_slot_key(item.get("sub_category", ""), [])

    try:
        pool = await get_pool()
    except Exception as e:
        logger.error(f"[Duration] get_pool() échoué : {e}")
        pool = None

    dates = await resolve_duration_dates(
        duration_days    = duration_days,
        departure_date   = dep_already,   # None si CAS 2/3
        pool             = pool,
        user_id          = user_id,
        tenant_id        = tenant_id,
        current_category = current_category,
    )

    if not dates.get("departure_date"):
        return seg_entities

    dep = dates["departure_date"]
    ret = dates["return_date"]

    # Injection dans seg_entities
    seg_entities["departure_date"] = dep
    seg_entities["return_date"]    = ret
    seg_entities["DATE"]           = [dep, ret]
    seg_entities["DATE_RANGE"]     = [{"departure_date": dep, "return_date": ret}]

    logger.info(
        f"[Duration] ✅ Injecté | '{duration_str}' ({duration_days}j) | "
        f"cat={current_category} | {dep} → {ret}"
    )

    # Mise à jour Redis (slots de la catégorie)
    if redis_client:
        prefix   = f"{tenant_id}:{user_id}:{session_id}"
        cat_key  = subcat_to_slot_key(item.get("sub_category", ""), [])
        slot_key = f"{prefix}:slots:{cat_key}"
        try:
            redis_client.hset(slot_key, "date_depart", dep)
            redis_client.hset(slot_key, "date_retour", ret)
            redis_client.expire(slot_key, 3600)
            logger.info(
                f"[Duration] 💾 Redis slots:{cat_key} → "
                f"date_depart={dep} | date_retour={ret}"
            )
        except Exception as e:
            logger.warning(f"[Duration] Redis write échoué : {e}")

    return seg_entities


# ══════════════════════════════════════════════════════════════════════════════
# FORM DATA → SLOTS
# ══════════════════════════════════════════════════════════════════════════════

async def _store_form_data_to_slots(
    redis_client,
    session_id: str,
    tenant_id:  str,
    user_id:    str,
    form_data:  dict,
    language:   str = "query_fr",
) -> None:
    from services.redis import SLOTS_DEFAULT, _translate_to_french, get_slots_for_category

    prefix    = f"{tenant_id}:{user_id}:{session_id}"
    slots_key = f"{prefix}:slots:global"

    has_flight = any(k in form_data for k in ("origin", "destination", "departure_date", "return_date", "cabin_class", "adults"))
    has_hotel  = any(k in form_data for k in ("city", "check_in", "check_out", "rooms", "guests"))

    if has_flight:
        reset_slots_for_category(redis_client, prefix, "flight")
    if has_hotel:
        reset_slots_for_category(redis_client, prefix, "hotel")
        reset_slots_for_category(redis_client, prefix, "global")

    current_slots = SLOTS_DEFAULT.copy()
    FORM_TO_SLOT = {
        "city":           "destination",
        "check_in":       "date_depart",
        "check_out":      "date_retour",
        "checkin":        "date_depart",
        "checkout":       "date_retour",
        "guests":         "passagers",
        "price_min":      "budget",
        "price_max":      "budget",
        "origin":         "origine",
        "destination":    "destination",
        "departure_date": "date_depart",
        "return_date":    "date_retour",
        "adults":         "passagers",
        "cabin_class":    "ton_voyage",
        "date_arrivee":   "date_depart",
        "nb_personnes":   "passagers",
        "passengers":     "passagers",
        "budget":         "budget",
        "max_price":      "budget",
        "flight_number":  "vol",
        "location":       "destination",
    }

    ALWAYS_OVERWRITE = {"date_depart", "date_retour", "destination", "origine", "passagers"}

    updated = False
    for form_key, form_val in form_data.items():
        if form_val is None or form_val == "":
            continue

        slot_name = FORM_TO_SLOT.get(form_key.lower())
        if not slot_name:
            logger.debug(f"[Classifier:form] ⏭ '{form_key}' pas dans FORM_TO_SLOT → ignoré")
            continue

        val_str = str(form_val).strip()

        if slot_name in ("destination", "origine"):
            val_str = _translate_to_french(val_str, language)

        if slot_name == "budget":
            existing = current_slots.get("budget", "")
            try:
                new_val = float(val_str)
                old_val = float(existing) if existing else 0
                if new_val > old_val:
                    current_slots["budget"] = val_str
                    updated = True
                    logger.info(f"[Classifier:form] 💰 budget mis à jour (max) = '{val_str}'")
            except ValueError:
                if not existing:
                    current_slots["budget"] = val_str
                    updated = True
            continue

        if slot_name in ALWAYS_OVERWRITE:
            current_slots[slot_name] = val_str
            updated = True
            logger.info(f"[Classifier:form] ✅ FORCE '{form_key}' → slots['{slot_name}'] = '{val_str}'")
        elif not current_slots.get(slot_name):
            current_slots[slot_name] = val_str
            updated = True
            logger.info(f"[Classifier:form] 📋 '{form_key}' → slots['{slot_name}'] = '{val_str}'")
        else:
            logger.info(f"[Classifier:form] ⏭ '{form_key}' ignoré — slots['{slot_name}'] = '{current_slots[slot_name]}'")

    if updated and redis_client:
        redis_client.hset(slots_key, mapping=current_slots)
        redis_client.expire(slots_key, 3600)
        logger.info(f"[Classifier:form] ✅ Slots globaux mis à jour : {current_slots}")
        hotel_city = form_data.get("city")
        if hotel_city:
            hotel_key = f"{prefix}:slots:hotel"
            hotel_slots = redis_client.hgetall(hotel_key) or SLOTS_DEFAULT.copy()
            hotel_slots["destination"] = hotel_city
            redis_client.hset(hotel_key, mapping=hotel_slots)
            redis_client.expire(hotel_key, 3600)
            logger.info(f"[Classifier:form] 🏨 slots:hotel['destination'] = '{hotel_city}'")

        flight_origin = form_data.get("origin")
        flight_dest   = form_data.get("destination")
        if flight_origin or flight_dest:
            flight_key   = f"{prefix}:slots:flight"
            flight_slots = redis_client.hgetall(flight_key) or SLOTS_DEFAULT.copy()
            if flight_origin: flight_slots["origine"]     = flight_origin
            if flight_dest:   flight_slots["destination"] = flight_dest
            redis_client.hset(flight_key, mapping=flight_slots)
            redis_client.expire(flight_key, 3600)
            logger.info(f"[Classifier:form] ✈️ slots:flight: {flight_origin} → {flight_dest}")
    else:
        logger.info(f"[Classifier:form] ⏭ Aucun slot mis à jour depuis formulaire")


# ══════════════════════════════════════════════════════════════════════════════
# REJECTION
# ══════════════════════════════════════════════════════════════════════════════

def _rejection_response(language: str, segments: list, scores: list) -> str:
    messages = {
        "query_fr":      "⚠️ Votre demande semble hors du domaine voyage. Merci de préciser.",
        "query_ar":      "⚠️ طلبك خارج نطاق السفر. يرجى التوضيح.",
        "query_derja_l": "⚠️ طلبك خارج نطاق السفر. يرجى التوضيح.",
        "query_en":      "⚠️ Your request seems out of scope. Please clarify.",
        "query_derja_a": "⚠️ طلبك خارج نطاق السفر. يرجى التوضيح.",
    }
    return json.dumps({
        "message": messages.get(language, messages["query_fr"]),
        "_meta": {
            "status":   "rejected",
            "segments": segments,
            "scores":   [round(s, 3) for s in scores],
        },
    }, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════════════
# NŒUD PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

async def classifier_node(state: AgentState) -> AgentState:
    from models.xlmr_subcat import predict_subcat
    from models.ner_xlm import predict_ner
    await emit({
        "type":    "status",
        "step":    "classifier",
        "message": "Analyse de votre demande...",
    })

    language   = state.get("language", "query_fr")
    session_id = state.get("session_id", "")
    tenant_id  = state.get("tenant_id", "")
    user_id    = state.get("user_id", "")
    raw_text   = state.get("raw_text", "")
    redis      = await get_redis_client()

    # ── 0. Mode formulaire ────────────────────────────────────────────────
    if state.get("entities") and state.get("classification"):
        logger.info("[Classifier] ⏭️ SKIP — mode formulaire")
        form_data = state.get("form_data", {})
        if form_data and redis:
            await _store_form_data_to_slots(
                redis, session_id, tenant_id, user_id, form_data, language
            )
        if state.get("ambiguous_queue"):
            return await _continue_queue(state, language, redis, session_id, tenant_id, user_id)
        await emit({
            "type":           "status",
            "step":           "classifier_done",
            "classification": state.get("classification", []),
            "mode":           "form",
        })
        return state

    # ── 1. NER global ─────────────────────────────────────────────────────
    if raw_text and redis:
        global_ner = predict_ner(raw_text)
        global_ner.pop("PERSONS", None)
        global_entities = _flatten_entities(global_ner)
        if global_entities:
            store_entities_pipeline(
                redis, session_id, global_entities,
                tenant_id, user_id, raw_text,
                language=language,
                category="global",
            )
            logger.info(
                f"[Classifier] 🌍 NER global → {len(global_entities)} entités "
                f"→ slots:global"
            )

    if state.get("clarification_pending"):
        return await _resolve_clarification(
            state, language, redis, session_id, tenant_id, user_id
        )

    # ── 2. Mode normal ────────────────────────────────────────────────────
    segments  = state.get("segments", [])
    accepted  = []
    rejected  = []
    ambiguous = []

    for seg in segments:
        seg_for_model = seg
        if language.startswith("query_derja"):
            seg_for_model = await _translate_for_model(seg, redis)

        result = predict_subcat(seg_for_model, top_k=2)
        score  = result["confidence"]
        topk   = result["topk"]
        top1   = topk[0]["label"] if topk else ""
        top2   = topk[1]["label"] if len(topk) > 1 else ""

        logger.info(
            f"[Classifier] seg='{seg[:50]}' | score={score:.3f} | "
            f"top1={top1} | top2={top2}"
        )

        # Rejet immédiat si score très bas
        if score < SCORE_LOW:
            rejected.append({"segment": seg, "score": score})
            logger.info(f"[Classifier] 🔴 Rejeté score={score:.3f}")
            message = json.loads(_rejection_response(language, [seg], [score])).get("message", "")
            await emit({"type": "status", "step": "classifier_rejected",
                        "message": "Demande hors domaine voyage"})
            await emit({
                "type":            "stream_done",
                "status":          "rejected",
                "message":         message,
                "loyalty_summary": {},
                "follow_up":       "",
                "total_segments":  0,
            })
            return {
                **state,
                "classification": [],
                "entities":       {},
                "final_response": _rejection_response(language, [seg], [score]),
            }

        all_subcats = _all_subcats_list()
        gpt_correction = await asyncio.get_event_loop().run_in_executor(
            None,
            ask_gpt_correct_classification,
            seg, top1, top2, score, all_subcats, language
        )

        action = gpt_correction.get("action", "accept")
        gpt_s1 = gpt_correction.get("subcat1", {})
        gpt_s2  = gpt_correction.get("subcat2") or {}

        logger.info(f"[Classifier] 🤖 GPT correction → action={action} | s1={gpt_s1.get('label') if gpt_s1 else None} | s2={gpt_s2.get('label') if gpt_s2 else None}")

        if action == "reject":
            rejected.append({"segment": seg, "score": score})
            message = json.loads(_rejection_response(language, [seg], [score])).get("message", "")
            await emit({"type": "stream_done", "status": "rejected",
                        "message": message, "loyalty_summary": {},
                        "follow_up": "", "total_segments": 0})
            return {
                **state,
                "classification": [],
                "entities":       {},
                "final_response": _rejection_response(language, [seg], [score]),
            }

        elif action == "clarify":
            from models.xlmr_subcat import SUBCAT_TO_CAT
            corrected_topk = [
                {
                    "id":         gpt_s1.get("id", topk[0]["id"]),
                    "label":      gpt_s1.get("label", top1),
                    "category":   SUBCAT_TO_CAT.get(gpt_s1.get("label", top1), ""),
                    "confidence": score,
                },
                {
                    "id": (gpt_s2 or {}).get("id", topk[1]["id"] if len(topk) > 1 else 0),
                    "label": (gpt_s2 or {}).get("label", top2),
                    "category":   SUBCAT_TO_CAT.get(gpt_s2.get("label", top2), ""),
                    "confidence": score,
                },
            ]
            ambiguous.append({"segment": seg, "topk": corrected_topk, "score": score})
            logger.info(f"[Classifier] 🟡 GPT → clarification | {corrected_topk[0]['label']} vs {corrected_topk[1]['label']}")
            continue

        else:
            from models.xlmr_subcat import SUBCAT_TO_CAT
            final_label = gpt_s1.get("label", top1)
            final_id    = gpt_s1.get("id", result["pred_id"])
            final_cat   = SUBCAT_TO_CAT.get(final_label, result["category"])

            if action == "correct":
                logger.info(f"[Classifier] ✅ GPT corrigé : '{top1}' → '{final_label}'")
            else:
                logger.info(f"[Classifier] ✅ GPT confirme : '{top1}'")

            accepted.append({
                "segment":      seg,
                "pred_id":      final_id,
                "sub_category": final_label,
                "category":     final_cat,
                "confidence":   score,
            })

    # ── Tous rejetés ──────────────────────────────────────────────────────
    if not accepted and not ambiguous:
        rejection = _rejection_response(
            language,
            [r["segment"] for r in rejected],
            [r["score"]   for r in rejected],
        )
        message = json.loads(rejection).get("message", "")
        await emit({"type": "status", "step": "classifier_rejected",
                    "message": "Demande hors domaine voyage"})
        await emit({
            "type":            "stream_done",
            "status":          "rejected",
            "message":         message,
            "loyalty_summary": {},
            "follow_up":       "",
            "total_segments":  0,
        })
        return {
            **state,
            "classification": [],
            "entities":       {},
            "final_response": rejection,
        }

    # ── 0 acceptés, que des ambigus ───────────────────────────────────────
    if not accepted and ambiguous:
        logger.info(f"[Classifier] 0 acceptés | {len(ambiguous)} ambigus → queue")
        return await _ask_clarification_queued(
            state, ambiguous, language,
            accepted_before=[], entities_before={},
        )

    # ── Extraction entités (avec résolution DURATION) ─────────────────────
    all_entities_merged = {}
    for item in accepted:
        seg_entities = _extract_entities(
            [item["segment"]], session_id, tenant_id, user_id, redis, language=language
        )
        seg_entities = await _resolve_duration_if_needed(
            seg_entities, item, session_id, tenant_id, user_id, redis
        )
        item["entities"] = seg_entities
        for k, v in seg_entities.items():
            if k not in all_entities_merged:
                all_entities_merged[k] = v
            elif isinstance(all_entities_merged[k], list) and isinstance(v, list):
                for val in v:
                    if val not in all_entities_merged[k]:
                        all_entities_merged[k].append(val)
    entities = all_entities_merged

    # ── Acceptés + ambigus → agents + queue différée ─────────────────────
    if ambiguous:
        logger.info(
            f"[Classifier] ✅ {len(accepted)} acceptés → agents | "
            f"🟡 {len(ambiguous)} ambigus → queue différée"
        )
        if redis:
            for item in accepted:
                cat_key  = subcat_to_slot_key(item["sub_category"], item.get("entities_list", []))
                seg_ner  = predict_ner(item["segment"])
                seg_flat = _flatten_entities(seg_ner)
                if seg_flat:
                    store_entities_pipeline(
                        redis, session_id, seg_flat,
                        tenant_id, user_id, item["segment"],
                        language=language, category=cat_key,
                    )
                    logger.info(f"[Classifier] 📦 slots:{cat_key} ← '{item['sub_category']}' | {len(seg_flat)} entités")

        await emit({
            "type":           "status",
            "step":           "classifier_done",
            "classification": accepted,
            "segments_count": len(accepted),
        })
        return {
            **state,
            "classification":        accepted,
            "entities":              entities,
            "clarification_pending": False,
            "ambiguous_queue":       ambiguous,
        }

    # ── Tous acceptés ─────────────────────────────────────────────────────
    logger.info(f"[Classifier] ✅ {len(accepted)} acceptés | 0 ambigus")

    if redis:
        for item in accepted:
            cat_key  = subcat_to_slot_key(item["sub_category"], item.get("entities_list", []))
            seg_ner  = predict_ner(item["segment"])
            seg_flat = _flatten_entities(seg_ner)
            if seg_flat:
                store_entities_pipeline(
                    redis, session_id, seg_flat,
                    tenant_id, user_id, item["segment"],
                    language=language, category=cat_key,
                )
                logger.info(f"[Classifier] 📦 slots:{cat_key} ← '{item['sub_category']}' | {len(seg_flat)} entités")

    await emit({
        "type":           "status",
        "step":           "classifier_done",
        "classification": accepted,
        "segments_count": len(accepted),
    })
    return {
        **state,
        "classification":        accepted,
        "entities":              entities,
        "clarification_pending": False,
        "ambiguous_queue":       [],
    }


# ══════════════════════════════════════════════════════════════════════════════
# CLARIFICATION — queue
# ══════════════════════════════════════════════════════════════════════════════

async def _ask_clarification_queued(
    state: AgentState,
    ambiguous_queue: list,
    language: str,
    accepted_before: list,
    entities_before: dict,
) -> AgentState:
    """
    Pose la clarification pour le 1er ambigu de la queue.
    Stocke le reste dans ambiguous_queue pour traitement en chaîne.
    """
    if not ambiguous_queue:
        return state

    current   = ambiguous_queue[0]
    remaining = ambiguous_queue[1:]

    result   = ask_gpt_clarification(current["segment"], current["topk"], language)
    question = result.get("question", "")
    phrase_a = result.get("phrase_a", current["topk"][0].get("label", ""))
    phrase_b = result.get("phrase_b", current["topk"][1].get("label", "") if len(current["topk"]) > 1 else "")

    enriched_topk = []
    for i, sub in enumerate(current["topk"]):
        enriched_topk.append({
            **sub,
            "display_phrase": phrase_a if i == 0 else phrase_b,
        })
    await emit({
        "type":    "clarification",
        "message": question,
        "subcats": enriched_topk,
        "segment": current["segment"],
    })

    return {
        **state,
        "clarification_pending":  True,
        "clarification_subcats":  enriched_topk,
        "clarification_segment":  current["segment"],
        "clarification_accepted": accepted_before,
        "clarification_entities": entities_before,
        "ambiguous_queue":        remaining,
        "final_response": json.dumps({
            "message": question,
            "_meta": {
                "status":          "clarification_needed",
                "subcats":         enriched_topk,
                "segment":         current["segment"],
                "queue_remaining": len(remaining),
                "ambiguous_queue": remaining,
            },
        }, ensure_ascii=False),
    }


# ══════════════════════════════════════════════════════════════════════════════
# CLARIFICATION — résolution
# ══════════════════════════════════════════════════════════════════════════════

async def _resolve_clarification(
    state: AgentState,
    language: str,
    redis,
    session_id: str,
    tenant_id: str,
    user_id: str,
) -> AgentState:
    from models.xlmr_subcat import SUBCAT_TO_CAT
    from models.ner_xlm import predict_ner
    user_reply      = state.get("raw_text", "")
    accepted_before = state.get("clarification_accepted", [])
    entities_before = state.get("clarification_entities", {})
    button_choice   = state.get("clarification_choice")

    # ── CAS 1 : Bouton cliqué ─────────────────────────────────────────────
    if button_choice:
        sub_category   = button_choice.get("label", "")
        category       = SUBCAT_TO_CAT.get(sub_category, "")
        chosen_id      = button_choice.get("id")
        original_query = state.get("clarification_segment", "") or state.get("raw_text", "")
        state          = {**state, "raw_text": original_query}

        logger.info(f"[Classifier] ✅ Bouton → subcat={sub_category}")
        resolved = await _build_resolved_state(
            state, original_query, chosen_id, sub_category, category, "button_choice",
            accepted_before, entities_before, redis, session_id, tenant_id, user_id,
            language=language,
        )
        return await _continue_queue(resolved, language, redis, session_id, tenant_id, user_id)

    # ── CAS 2 : Nouvelle requête → GPT ───────────────────────────────────
    logger.info("[Classifier] 🔄 Nouvelle requête → GPT full subcats")
    result_gpt = ask_gpt_resolve(user_reply, _all_subcats_list())

    if result_gpt is not None:

        # ── CAS multi-intentions ──────────────────────────────────────
        if "segments" in result_gpt:
            logger.info(f"[Classifier] 🔀 Multi-intentions détectées → {len(result_gpt['segments'])} segments")

            accepted = []
            for item in result_gpt["segments"]:
                chosen = item["subcat"]
                accepted.append({
                    "segment":      item["segment"],
                    "pred_id":      chosen["id"],
                    "sub_category": chosen["label"],
                    "category":     SUBCAT_TO_CAT.get(chosen["label"], ""),
                    "confidence":   None,
                    "resolved_by":  "gpt_multi",
                })

            all_entities_merged = {}
            for item in accepted:
                seg_entities = _extract_entities(
                    [item["segment"]], session_id, tenant_id, user_id, redis, language=language
                )
                seg_entities = await _resolve_duration_if_needed(
                    seg_entities, item, session_id, tenant_id, user_id, redis
                )
                item["entities"] = seg_entities
                for k, v in seg_entities.items():
                    if k not in all_entities_merged:
                        all_entities_merged[k] = v
                    elif isinstance(all_entities_merged[k], list) and isinstance(v, list):
                        for val in v:
                            if val not in all_entities_merged[k]:
                                all_entities_merged[k].append(val)
            entities = all_entities_merged

            if redis:
                for item in accepted:
                    cat_key  = subcat_to_slot_key(item["sub_category"], [])
                    seg_ner  = predict_ner(item["segment"])
                    seg_flat = _flatten_entities(seg_ner)
                    if seg_flat:
                        store_entities_pipeline(
                            redis, session_id, seg_flat,
                            tenant_id, user_id, item["segment"],
                            language=language, category=cat_key,
                        )

            await emit({
                "type":           "status",
                "step":           "classifier_done",
                "classification": accepted,
                "segments_count": len(accepted),
            })
            return {
                **state,
                "classification":         accepted,
                "entities":               entities,
                "clarification_pending":  False,
                "clarification_subcats":  [],
                "clarification_segment":  "",
                "clarification_accepted": [],
                "clarification_entities": {},
                "clarification_choice":   None,
                "ambiguous_queue":        [],
                "final_response":         None,
            }

        # ── CAS simple ────────────────────────────────────────────────
        chosen = result_gpt["subcat"]
        logger.info(f"[Classifier] ✅ GPT match → subcat={chosen['label']}")
        resolved = await _build_resolved_state(
            state, user_reply,
            chosen["id"], chosen["label"], SUBCAT_TO_CAT.get(chosen["label"], ""),
            "gpt_full_match",
            accepted_before=[], entities_before={},
            redis=redis, session_id=session_id, tenant_id=tenant_id, user_id=user_id,
            language=language,
        )
        return await _continue_queue(resolved, language, redis, session_id, tenant_id, user_id)

    # ── CAS 3 : Hors domaine → reject ────────────────────────────────────
    logger.info("[Classifier] 🔴 Hors domaine → reject")

    rejection = _rejection_response(language, [user_reply], [0.0])
    message   = json.loads(rejection).get("message", "")

    await emit({
        "type":            "stream_done",
        "status":          "rejected",
        "message":         message,
        "loyalty_summary": {},
        "follow_up":       "",
        "total_segments":  0,
    })

    return {
        **state,
        "classification":         [],
        "entities":               {},
        "clarification_pending":  False,
        "clarification_subcats":  [],
        "clarification_segment":  "",
        "clarification_accepted": [],
        "clarification_entities": {},
        "clarification_choice":   None,
        "ambiguous_queue":        [],
        "final_response":         rejection,
    }


# ══════════════════════════════════════════════════════════════════════════════
# QUEUE — continuation
# ══════════════════════════════════════════════════════════════════════════════

async def _continue_queue(
    resolved_state: AgentState,
    language: str,
    redis,
    session_id: str,
    tenant_id: str,
    user_id: str,
) -> AgentState:
    """
    Après résolution d'un ambigu, vérifie s'il reste des ambigus dans la queue.
    Si oui → pose la clarification suivante.
    Si non → émet classifier_done et lance les agents.
    """
    ambiguous_queue   = resolved_state.get("ambiguous_queue", [])
    resolved_classif  = resolved_state.get("classification", [])
    resolved_entities = resolved_state.get("entities", {})

    if ambiguous_queue:
        logger.info(
            f"[Classifier] 🔄 Queue: {len(ambiguous_queue)} restants | "
            f"classification={len(resolved_classif)} résolus"
        )
        return await _ask_clarification_queued(
            resolved_state,
            ambiguous_queue,
            language,
            accepted_before=resolved_classif,
            entities_before=resolved_entities,
        )

    logger.info(
        f"[Classifier] ✅ Queue terminée | "
        f"{len(resolved_classif)} segments au total → agents"
    )
    await emit({
        "type":           "status",
        "step":           "classifier_done",
        "classification": resolved_classif,
        "segments_count": len(resolved_classif),
    })
    return {
        **resolved_state,
        "ambiguous_queue": [],
    }


# ══════════════════════════════════════════════════════════════════════════════
# BUILD RESOLVED STATE
# ══════════════════════════════════════════════════════════════════════════════

async def _build_resolved_state(
    state: AgentState,
    segment: str,
    chosen_id: int,
    sub_category: str,
    category: str,
    resolved_by: str,
    accepted_before: list,
    entities_before: dict,
    redis,
    session_id: str,
    tenant_id: str,
    user_id: str,
    language: str = "query_fr",
) -> AgentState:
    from models.xlmr_subcat import SUBCAT_TO_CAT
    from models.ner_xlm import predict_ner

    item_stub = {"sub_category": sub_category, "segment": segment}

    new_entities = _extract_entities([segment], session_id, tenant_id, user_id, redis, language=language)
    new_entities = await _resolve_duration_if_needed(
        new_entities, item_stub, session_id, tenant_id, user_id, redis
    )

    merged_entities = dict(entities_before)
    for key, values in new_entities.items():
        if key not in merged_entities:
            merged_entities[key] = values
        else:
            existing = merged_entities[key]
            if isinstance(existing, list) and isinstance(values, list):
                for v in values:
                    if v not in existing:
                        existing.append(v)
            elif existing is None:
                merged_entities[key] = values

    from models.ner_xlm import detect_loc_context

    locs  = merged_entities.get("LOC",  [])
    dates = merged_entities.get("DATE", [])

    if len(locs) == 0:
        merged_entities["origin"]      = None
        merged_entities["destination"] = None

    elif len(locs) == 1:
        context = detect_loc_context(segment, locs[0])
        if context == "origin":
            merged_entities["origin"]      = locs[0]
            merged_entities["destination"] = None
        else:
            merged_entities["origin"]      = None
            merged_entities["destination"] = locs[0]

    else:
        ctx0 = detect_loc_context(segment, locs[0])
        ctx1 = detect_loc_context(segment, locs[1])

        if ctx0 == "origin" and ctx1 == "destination":
            merged_entities["origin"]      = locs[0]
            merged_entities["destination"] = locs[1]
        elif ctx0 == "destination" and ctx1 == "origin":
            merged_entities["origin"]      = locs[1]
            merged_entities["destination"] = locs[0]
        else:
            merged_entities["origin"]      = locs[0]
            merged_entities["destination"] = locs[1]
        
    merged_entities["departure_date"] = dates[0] if len(dates) > 0 else None
    merged_entities["return_date"]    = dates[1] if len(dates) > 1 else None

    merged_classification = accepted_before + [{
        "segment":      segment,
        "pred_id":      chosen_id,
        "sub_category": sub_category,
        "category":     category,
        "confidence":   None,
        "resolved_by":  resolved_by,
    }]

    logger.info(
        f"[Classifier] 🔀 Fusion : {len(accepted_before)} avant "
        f"+ 1 résolu = {len(merged_classification)} total"
    )

    if redis:
        cat_key  = subcat_to_slot_key(sub_category, _flatten_entities(new_entities))
        seg_ner  = predict_ner(segment)
        seg_flat = _flatten_entities(seg_ner)
        if seg_flat:
            store_entities_pipeline(
                redis, session_id, seg_flat,
                tenant_id, user_id, segment,
                language=language, category=cat_key,
            )
            logger.info(
                f"[Classifier] 📦 slots:{cat_key} ← "
                f"'{sub_category}' | {len(seg_flat)} entités"
            )

    return {
        **state,
        "raw_text":               segment,
        "classification":         merged_classification,
        "entities":               merged_entities,
        "clarification_pending":  False,
        "clarification_subcats":  [],
        "clarification_segment":  "",
        "clarification_accepted": [],
        "clarification_entities": {},
        "clarification_choice":   None,
        "ambiguous_queue":        state.get("ambiguous_queue", []),
        "final_response":         None,
    }