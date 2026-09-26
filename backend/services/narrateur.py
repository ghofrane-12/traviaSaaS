# orchestrator/narrate.py
import json
import logging
import asyncio
import re

import httpx
from core.config import settings
from core.streaming import emit
import hashlib
import time as _time
logger = logging.getLogger("chat_logger")


_narrate_cache: dict[str, tuple] = {}
_NARRATE_CACHE_TTL = 600  


TONE_PROMPTS = {
    "formal": "Répondez de manière professionnelle et formelle.",
    "casual": "Répondez de manière décontractée et amicale.",
    "luxury": "Répondez avec élégance, comme un concierge 5 étoiles.",
}

LANGUAGE_LABELS = {
    "query_fr":      "français",
    "query_ar":      "arabe",
    "query_derja_a": "arabe",
    "query_en":      "anglais",
    "query_derja_l": "arabe",
}

TYPE_LABELS = {
    "flight_search_results":     "vols",
    "hotel_search_results":      "hôtels",
    "transport_search_results":  "transports",
    "restaurant_search_results": "restaurants",
    "activity_search_results":   "activités",
    "specialty_search_results":  "spécialités",
}




def _make_seg_id(agent_name: str, index: int) -> str:
    """
    Produit un seg_id lisible depuis le nom de l'agent.
    Ex: "Destination Info:Cultural Norms" → "Cultural_Norms"
        "Claims:Baggage Damage"           → "Baggage_Damage"
    Si le nom est vide ou générique, fallback sur l'index.
    """
    if not agent_name:
        return str(index)
    part = agent_name.split(":")[-1].strip()
    clean = re.sub(r"[^a-zA-Z0-9]", "_", part).strip("_")
    return clean if clean else str(index)




async def narrate(state: dict) -> dict:
    tone            = state.get("tenant_config", {}).get("tone", "casual")
    language        = state.get("form_language") or state.get("language", "query_fr")
    results         = state.get("results", {})
    suggestions     = state.get("suggestions", [])
    loyalty_summary = state.get("loyalty_summary", {})
    raw_text        = state.get("raw_text", "")

    logger.info(f"[NARRATE] → Entrée narrate | language={language} | is_form={state.get('is_form_response')} | waiting={state.get('waiting_for_form')}")

    tone_instruction = TONE_PROMPTS.get(tone, TONE_PROMPTS["casual"])
    target_language  = LANGUAGE_LABELS.get(language, "français")
    tone_instruction += f"\n\nLangue de réponse OBLIGATOIRE : {target_language}. Ne réponds jamais dans une autre langue.\nIdentifiant langue: {language}"
    profile_prompt = state.get("context", {}).get("profile_prompt", "")
    if profile_prompt:
        tone_instruction = f"{tone_instruction}\n\n{profile_prompt}"
    conv_context_prompt = state.get("context", {}).get("conv_context_prompt", "")
    if conv_context_prompt:
        tone_instruction = f"{tone_instruction}\n\n{conv_context_prompt}"
    if isinstance(results, list):
        logger.warning("[NARRATE] ⚠ state['results'] est une liste")
        results = {f"agent_{i}": r for i, r in enumerate(results) if isinstance(r, dict)}
    is_form_response = state.get("is_form_response", False)
    form_data = state.get("form_data", {})
    
    if is_form_response and state.get("waiting_for_form"):
        return await _process_form_response(state, form_data)


    pending_segments = state.get("pending_segments", {})
    if pending_segments and not is_form_response:
        for entry in pending_segments.get("success", []):
            results.setdefault(entry["agent_name"], entry["result"])
        for entry in pending_segments.get("error", []):
            results.setdefault(entry["agent_name"], entry["result"])
        for entry in pending_segments.get("human", []):
            results.setdefault(entry["agent_name"], entry["result"])
        if not suggestions:
            suggestions = pending_segments.get("original_suggestions", [])
        if not loyalty_summary:
            loyalty_summary = pending_segments.get("original_loyalty", {})
        raw_text = raw_text or pending_segments.get("original_raw_text", "")
        state["pending_segments"] = {}

    logger.info(f"[NARRATE] Tone: {tone} | Language: {target_language} | Segments: {list(results.keys())}")

    # ── 1. Triage ────────────────────────────────────────────────────────────
    buckets = _triage_results(results)

    logger.info(
        f"[NARRATE] Triage → succès={len(buckets['success'])} | "
        f"erreurs={len(buckets['error'])} | "
        f"more_info={len(buckets['more_info'])} | "
        f"human={len(buckets['human'])}"
    )

    if buckets["more_info"]:
        first_form = buckets["more_info"][0]
        seg_id = _make_seg_id(first_form["agent_name"], 0)

        state["pending_segments"] = {
            "success": [],
            "error":   buckets["error"],
            "human":   buckets["human"],
            "more_info": buckets["more_info"][1:],
            "original_suggestions": suggestions,
            "original_loyalty":     loyalty_summary,
            "original_raw_text":    raw_text,
            "_merged_agents": first_form["result"].get("_merged_agents", []),  
        }
        state["waiting_for_form"] = True

        seg_counter = [0]
        total_emitted_ref = [0]

        async def _emit_form_now():
            await _emit_segment_form(first_form, seg_id, state=state, pending_segments=state.get("pending_segments", {}))
            total_emitted_ref[0] += 1

        async def _emit_success_now():
            if not buckets["success"]:
                return
            types = [_detect_result_type(e["result"])[0] for e in buckets["success"]]
            all_info = all(t in ("info_results", "unknown") for t in types)
            if all_info and len(buckets["success"]) > 1:
                await _narrate_multi_info_segmented(
                    buckets["success"], raw_text, suggestions, loyalty_summary,
                    tone_instruction, target_language, seg_counter=seg_counter,
                )
            else:
                for entry in buckets["success"]:
                    s_id = _make_seg_id(entry["agent_name"], seg_counter[0])
                    await _narrate_single(
                        entry, error_entries=[],
                        raw_text=raw_text, suggestions=suggestions,
                        loyalty_summary=loyalty_summary,
                        tone_instruction=tone_instruction,
                        target_language=target_language,
                        seg_id=s_id,
                    )
                    seg_counter[0] += 1
                    total_emitted_ref[0] += 1
            for entry in buckets["error"]:
                e_id = _make_seg_id(entry["agent_name"], seg_counter[0])
                await _emit_segment_error(entry, e_id, target_language)
                seg_counter[0] += 1
                total_emitted_ref[0] += 1

        await asyncio.gather(_emit_form_now(), _emit_success_now())

        await _emit_stream_done(
        loyalty_summary,
        {"final_response": "{}"},
        total=total_emitted_ref[0],
        suggestions=suggestions,
        cross_sell_proposals=[] if state.get("is_cross_sell") else state.get("cross_sell_proposals", []),
        language=language,
    )

        return {
            "final_response": json.dumps({
                "status": "waiting_for_form",
                "message": first_form["result"].get("message", ""),
                "form": first_form["result"].get("form"),
                "pending_segments_count": len(buckets["more_info"][1:])
            }, ensure_ascii=False),
            "waiting_for_form": True,
            "pending_segments": state["pending_segments"],
        }

    # ── 2. Aucun succès ──────────────────────────────────────────────────────
    if not buckets["success"]:

        if buckets["human"]:
            result = await _narrate_human(
                buckets["human"][0], suggestions, loyalty_summary,
                tone_instruction, target_language, seg_id="human_required"
            )
            if state.get("ambiguous_queue"):
                result = await _inject_ambiguous_clarification(result, state)
            await _emit_stream_done(loyalty_summary, result, total=1, suggestions=suggestions, cross_sell_proposals=[] if state.get("is_cross_sell") else state.get("cross_sell_proposals", []), language=language)
            return result

        if buckets["more_info"]:
            for idx, entry in enumerate(buckets["more_info"]):
                seg_id = _make_seg_id(entry["agent_name"], idx)
                await _emit_segment_form(entry, seg_id)

            empty_final = _build_empty_final(suggestions, loyalty_summary)
            await _emit_stream_done(loyalty_summary, empty_final, total=len(buckets["more_info"]),cross_sell_proposals=[] if state.get("is_cross_sell") else state.get("cross_sell_proposals", []), language=language)
            return empty_final

        if buckets["error"]:
            for idx, entry in enumerate(buckets["error"]):
                seg_id = _make_seg_id(entry["agent_name"], idx)
                await _emit_segment_error(entry, seg_id, target_language)

            error_final = _build_all_error_final(
                buckets["error"], language, suggestions, loyalty_summary
            )
            await _emit_stream_done(loyalty_summary, error_final, total=len(buckets["error"]), cross_sell_proposals=[] if state.get("is_cross_sell") else state.get("cross_sell_proposals", []), language=language)
            return error_final

    # ── 3. Au moins 1 succès ─────────────────────────────────────────────────
    seg_counter   = [0] 
    all_finals    = []   
    total_emitted = 0

    for entry in buckets["error"]:
        seg_id = _make_seg_id(entry["agent_name"], seg_counter[0])
        await _emit_segment_error(entry, seg_id, target_language)
        seg_counter[0] += 1
        total_emitted  += 1

    async def _emit_forms_and_human() -> list:
        """Émet les formulaires et human_required, retourne leurs finals."""
        finals = []
        for entry in buckets["more_info"]:
            seg_id = _make_seg_id(entry["agent_name"], seg_counter[0])
            await _emit_segment_form(entry, seg_id, state=state, pending_segments=state.get("pending_segments", {}))
            seg_counter[0] += 1
            total_emitted_ref[0] += 1

        for entry in buckets["human"]:
            seg_id = _make_seg_id(entry["agent_name"], seg_counter[0])
            result = await _narrate_human(
                entry, suggestions, loyalty_summary,
                tone_instruction, target_language, seg_id=seg_id
            )
            finals.append(result)
            seg_counter[0] += 1
            total_emitted_ref[0] += 1
        return finals

    async def _emit_success_segments() -> list:
        """Traite et émet les segments succès via Gemini."""
        finals = []
        types    = [_detect_result_type(e["result"])[0] for e in buckets["success"]]
        all_info = all(t in ("info_results", "unknown") for t in types)

        if all_info and len(buckets["success"]) > 1:
            results_list = await _narrate_multi_info_segmented(
                buckets["success"],
                raw_text, suggestions, loyalty_summary,
                tone_instruction, target_language,
                seg_counter=seg_counter,
            )
            finals.extend(results_list)
            total_emitted_ref[0] += len(buckets["success"])
        else:
            for entry in buckets["success"]:
                seg_id = _make_seg_id(entry["agent_name"], seg_counter[0])
                result = await _narrate_single(
                    entry,
                    error_entries=[],
                    raw_text=raw_text,
                    suggestions=suggestions,
                    loyalty_summary=loyalty_summary,
                    tone_instruction=tone_instruction,
                    target_language=target_language,
                    seg_id=seg_id,
                )
                finals.append(result)
                seg_counter[0] += 1
                total_emitted_ref[0] += 1
        return finals

    total_emitted_ref = [total_emitted]

    forms_human_finals, success_finals = await asyncio.gather(
        _emit_forms_and_human(),
        _emit_success_segments(),
    )
    all_finals.extend(forms_human_finals)
    all_finals.extend(success_finals)
    total_emitted = total_emitted_ref[0]

    # ── 4. Construire le final_response consolidé ────────────────────────────
    consolidated = _consolidate_finals(all_finals, suggestions, loyalty_summary)

    # ── 5. Injecter clarification si ambiguous_queue présent ─────────────────
    if state.get("ambiguous_queue"):
        consolidated = await _inject_ambiguous_clarification(consolidated, state)

    # ── 6. stream_done global ────────────────────────────────────────────────
    await _emit_stream_done(
    loyalty_summary, consolidated,
    total=total_emitted,
    suggestions=suggestions,
    cross_sell_proposals=[] if state.get("is_cross_sell") else state.get("cross_sell_proposals", []),
    language=language,
)


    return consolidated




async def _emit_stream_done(
    loyalty_summary: dict,
    final_result: dict,
    total: int,
    suggestions: list = [],
    cross_sell_proposals: list = [],
    language: str = "query_fr",   
) -> None:
    final_str = final_result.get("final_response", "{}")
    parsed = {}
    try:
        parsed = json.loads(final_str) if isinstance(final_str, str) else final_str
    except Exception:
        pass

    await emit({
        "type":                 "stream_done",
        "loyalty_summary":      loyalty_summary,
        "follow_up":            parsed.get("follow_up", ""),
        "total_segments":       total,
        "suggestions":          suggestions,
        "cross_sell_proposals": cross_sell_proposals,
        "language":             language,
    })
    logger.info(
        f"[NARRATE] 🏁 stream_done | total_segments={total} | "
        f"cross_sell={len(cross_sell_proposals)} | language={language}"
    )


async def _emit_segment_error(entry: dict, seg_id: str, target_language: str) -> None:
    label   = _resolve_error_label(entry)
    result  = entry["result"]
    
    phrases = {
        "français":                           f"Le service {label} est temporairement indisponible.",
        "anglais":                            f"The {label} service is temporarily unavailable.",
        "arabe":                              f"خدمة {label} غير متاحة مؤقتاً.",
        "darija tunisienne (alphabet latin)": f"Service {label} moch mawjoud daba.",
        "darija tunisienne (alphabet arabe)": f"خدمة {label} ماهيش موجودة دابا.",
    }
    message = phrases.get(target_language, phrases["français"])
    
    custom_message = result.get("message") or result.get("error_message")
    if custom_message and len(custom_message) < 300:
        message = custom_message

    await emit({
        "type":        "segment_done",
        "seg_id":      seg_id,
        "result_type": "error",
        "label":       label,
        "message":     message,
        "error_code":  result.get("error_code"),
        "retryable":   result.get("retryable", True),   
        "sub_category": entry.get("agent_name", ""),
    })
    logger.info(f"[NARRATE] 🔴 segment_done(error) | seg_id={seg_id} | label={label}")


async def _emit_segment_form(
    entry: dict, seg_id: str,
    state: dict = None, pending_segments: dict = None
) -> None:
    result     = entry["result"]
    agent_name = entry["agent_name"].lower()

    if result.get("form_type") == "natural":
        await _emit_management_question(entry, seg_id, state)
        return
    
    language = state.get("language", "query_fr") if state else "query_fr"
    target_language = LANGUAGE_LABELS.get(language, "français")
    
    form_messages = {
        "français": "Veuillez compléter les informations ci-dessous",
        "arabe":    "يرجى إكمال المعلومات أدناه",
        "anglais":  "Please complete the information below",
    }
    message = form_messages.get(target_language, form_messages["français"])

    sub_category = result.get("sub_category", "")
    if not sub_category:
        if any(k in agent_name for k in ("hotel", "stay")):
            sub_category = "Hotel Booking"
        elif any(k in agent_name for k in ("flight", "vol", "transport")):
            sub_category = "Flight Booking"
        elif any(k in agent_name for k in ("tour", "excursion", "activity")):
            sub_category = "Tour/Excursion Booking"

    merged_agents = result.get("_merged_agents", [sub_category])
    is_merged     = len(merged_agents) > 1

    entities_raw = []
    if state:
        for k, vals in state.get("entities", {}).items():
            if isinstance(vals, list):
                for v in vals:
                    if v: entities_raw.append({"type": k, "value": v})
            elif vals:
                entities_raw.append({"type": k, "value": vals})

    await emit({
        "type":             "segment_form",
        "seg_id":           seg_id,
        "language":          language,
        "sub_category":     sub_category,
        "sub_categories":   merged_agents,
        "is_merged":        is_merged,
        "message":          message,
        "form":             result.get("form"),
        "steps":            result.get("steps", []),
        "entities":         entities_raw,
        "pending_segments": pending_segments or {},
    })
    logger.info(
        f"[NARRATE] 📋 segment_form | seg_id={seg_id} | "
        f"sub_category={sub_category} | is_merged={is_merged}"
    )


async def _emit_management_question(
    entry: dict, seg_id: str, state: dict = None
) -> None:
    """
    Génère une question en langage naturel pour les cas management
    où le contexte est manquant. Pas de formulaire frontend.
    """
    result       = entry["result"]
    agent_name   = entry["agent_name"]
    missing      = result.get("missing_fields", [])
    language     = state.get("language", "query_fr") if state else "query_fr"
    tone         = state.get("tenant_config", {}).get("tone", "formal") if state else "formal"

    LANGUAGE_LABELS = {
        "query_fr":      "français",
        "query_ar":      "arabe",
        "query_en":      "anglais",
        "query_derja_l": "darija tunisienne (alphabet latin)",
        "query_derja_a": "darija tunisienne (alphabet arabe)",
    }
    target_language  = LANGUAGE_LABELS.get(language, "français")
    tone_instruction = {
        "formal": "Répondez de manière professionnelle et formelle.",
        "casual": "Répondez de manière décontractée et amicale.",
        "luxury": "Répondez avec élégance, comme un concierge 5 étoiles.",
    }.get(tone, "Répondez de manière professionnelle.")

    FIELD_LABELS = {
        "city":           "la destination",
        "origin":         "la ville de départ",
        "destination":    "la ville d'arrivée",
        "check_in":       "la date d'arrivée",
        "check_out":      "la date de départ",
        "departure_date": "la date de départ",
        "return_date":    "la date de retour",
    }
    missing_str = " et ".join(FIELD_LABELS.get(f, f) for f in missing)

    prompt = f"""Tu es un assistant voyage. L'utilisateur souhaite effectuer une action 
sur une réservation ({agent_name}) mais il manque des informations.

Il manque : {missing_str}

Génère UNE question courte et naturelle en {target_language} pour demander 
ces informations. Maximum 2 phrases. Pas de liste, pas de formulaire.

Retourne UNIQUEMENT ce JSON (sans backticks) :
{{"message": "ta question ici", "follow_up": ""}}

JSON:"""

    message = ""
    try:
        response = await _call_openai(prompt, tone_instruction)
        if response:
            import json as _json
            parsed  = _json.loads(_clean_json(response))
            message = parsed.get("message", "")
    except Exception as e:
        logger.error(f"[NARRATE] ❌ management question Gemini: {e}")

    if not message:
        fallback = {
            "français": f"Pourriez-vous me préciser {missing_str} pour votre réservation ?",
            "anglais":  f"Could you please specify {missing_str} for your booking?",
            "arabe":    f"هل يمكنك تحديد {missing_str} لحجزك؟",
        }
        message = fallback.get(target_language, fallback["français"])

    await emit({
        "type":        "segment_done",
        "seg_id":      seg_id,
        "result_type": "management_question",
        "message":     message,
        "follow_up":   "",
        "sections":    [],
        "suggestions": [],
    })
    logger.info(
        f"[NARRATE] 💬 management_question | seg_id={seg_id} | "
        f"missing={missing} | '{message[:60]}'"
    )


async def _narrate_single(
    success_entry: dict,
    error_entries: list,
    raw_text: str,
    suggestions: list,
    loyalty_summary: dict,
    tone_instruction: str,
    target_language: str,
    seg_id: str,
    pending_form=None,
) -> dict:
    result      = success_entry["result"]
    result_type, raw_results = _detect_result_type(result)
    result_count = len(raw_results)
    error_phrase = _build_error_phrase(error_entries, target_language)

    message   = ""
    follow_up = ""

    if result_type == "info_results":
        sections    = result.get("sections", [])
        raw_content = _extract_raw_content(result)
        result_dest = result.get("destination") or result.get("city") or ""
        effective_raw_text = raw_text
        if result_dest and result_dest.lower() not in raw_text.lower():
            effective_raw_text = f"{raw_text} (destination: {result_dest})"

        info_prompt = f"""Tu es un assistant voyage expert. L'utilisateur a posé une question précise.
Réponds DIRECTEMENT à cette question en utilisant UNIQUEMENT le contenu fourni.
⚠️ RÈGLE ABSOLUE : Réponds UNIQUEMENT en {target_language}.

QUESTION DE L'UTILISATEUR:
"{effective_raw_text}"

CONTENU DOCUMENTAIRE DISPONIBLE:
{raw_content[:3000]}

{f'NOTE: {error_phrase}' if error_phrase else ''}

RÈGLES STRICTES:
1. Réponds DIRECTEMENT à la question posée, pas de généralités
2. Utilise UNIQUEMENT les informations du contenu documentaire ci-dessus
3. Si l'information spécifique n'est pas dans le contenu, dis-le clairement
4. Langue: {target_language} uniquement
5. Message principal: 2-4 phrases ciblées sur la question
6. Si une NOTE est présente, intègre-la naturellement à la fin du message
7. NE génère PAS de contenu inventé ou générique
8. Retourne UNIQUEMENT ce JSON (sans backticks):
{{
  "message": "réponse directe à la question en 2-4 phrases",
  "follow_up": "question courte pour approfondir",
  "sections": [
    {{"title": "titre pertinent", "content": "contenu extrait du document", "items": []}}
  ]
}}

JSON:"""

        reformulated_sections = sections
        try:
            response = await _call_openai(info_prompt, tone_instruction,temperature=0.5)
            if response:
                cleaned = _clean_json(response)
                parsed  = json.loads(cleaned)
                message   = parsed.get("message", "")
                follow_up = parsed.get("follow_up", "")
                reformulated_sections = parsed.get("sections", sections)
        except Exception as e:
            logger.error(f"[NARRATE] ❌ Reformulation info: {e}")
            message = result.get("message", "")

        message   = message   or result.get("message", "Voici les informations disponibles.")
        follow_up = follow_up or "Avez-vous d'autres questions sur ce sujet ?"
        reformulated_sections = _normalize_items(reformulated_sections)

        final_payload = {
            "type":            "info_results",
            "message":         message,
            "follow_up":       follow_up,
            "sections":        reformulated_sections,
            "actions":         result.get("actions", [{"label": "En savoir plus", "action": "explore"}]),
            "suggestions":     suggestions,
            "loyalty_summary": loyalty_summary,
        }
        if pending_form:
            final_payload["pending_form"] = pending_form

        await emit({
            "type":        "segment_done",
            "seg_id":      seg_id,
            "result_type": "info_results",
            "message":     message,
            "follow_up":   follow_up,
            "sections":    reformulated_sections,
            "actions":     final_payload["actions"],
            "suggestions": [],
        })
        logger.info(f"[NARRATE] ✅ segment_done(info) | seg_id={seg_id} | {message[:80]}")
        return {"final_response": json.dumps(final_payload, ensure_ascii=False)}

    sugg_text    = "\n".join([f"- {s.get('label','')}" for s in suggestions[:3]]) if suggestions else ""
    loyalty_text = (f"+{loyalty_summary['points_earned']} points fidélité"
                    if loyalty_summary and loyalty_summary.get("points_earned", 0) > 0 else "")

    prompt = f"""Tu es un assistant voyage chaleureux et créatif. Génère un message d'introduction naturel en {target_language} et varié.

⚠️ RÈGLE ABSOLUE : Réponds UNIQUEMENT en {target_language}.
Même si le contenu ci-dessous est en français, ta réponse doit être en {target_language}.
CONTEXTE:
- Recherche: "{raw_text}"
- Type: {result_type} ({result_count} résultats trouvés)
- Destination: {_extract_query_from_result(result).get("city") or _extract_query_from_result(result).get("destination", "")}
- Fidélité: {loyalty_text if loyalty_text else 'aucun point'}
{f'- Problème partiel: {error_phrase}' if error_phrase else ''}

RÈGLES:
1. Message en {target_language} UNIQUEMENT — aucun mot dans une autre langue
2. 2-3 phrases VARIÉES et naturelles — évite les formules répétitives comme "J'ai trouvé X résultats"
3. Sois créatif : utilise des tournures différentes à chaque fois
4. Mentionne le nombre de résultats
5. Si "Problème partiel" mentionné, intègre-le naturellement
6. Termine par une question d'orientation engageante
7. Retourne UNIQUEMENT ce JSON (sans backticks):
{{"message": "ton message ici", "follow_up": "question courte"}}

JSON:"""

    try:
        response = await _call_openai(prompt, tone_instruction)
        if response:
            parsed    = _parse_gpt_response(response)
            message   = parsed.get("message", "")
            follow_up = parsed.get("follow_up", "")
    except Exception as e:
        logger.error(f"[NARRATE] ❌ Gemini single search: {e}")

    if not message:
        message   = f"J'ai trouvé {result_count} résultat(s) pour votre recherche."
        if error_phrase:
            message += f" {error_phrase}"
        follow_up = "Souhaitez-vous voir les détails ?"

    if raw_results:
        converted_offers = result.get("offers", [])
        final_payload = {
            "type":            result_type,
            "query":           _extract_query_from_result(result),
            "results":         raw_results,
            "offers":    converted_offers,
            "message":         message,
            "follow_up":       follow_up,
            "suggestions":     suggestions,
            "loyalty_summary": loyalty_summary,
        }
        await emit({
            "type":        "segment_done",
            "seg_id":      seg_id,
            "result_type": result_type,
            "query":       _extract_query_from_result(result),
            "results":     raw_results,
            "offers":    converted_offers,
            "message":     message,
            "follow_up":   follow_up,
            "suggestions": [],
        })
    else:
        final_payload = {
            "type":            "info_results",
            "message":         message,
            "follow_up":       follow_up,
            "suggestions":     suggestions,
            "loyalty_summary": loyalty_summary,
        }
        await emit({
            "type":        "segment_done",
            "seg_id":      seg_id,
            "result_type": "info_results",
            "message":     message,
            "follow_up":   follow_up,
            "suggestions": [],
        })

    if pending_form:
        final_payload["pending_form"] = pending_form

    logger.info(f"[NARRATE] ✅ segment_done(search) | seg_id={seg_id} | type={result_type} | {result_count} résultats")
    return {"final_response": json.dumps(final_payload, ensure_ascii=False)}




async def _narrate_multi_info_segmented(
    success_entries: list,
    raw_text: str,
    suggestions: list,
    loyalty_summary: dict,
    tone_instruction: str,
    target_language: str,
    seg_counter: list,
) -> list:
    """
    1 appel Gemini groupé pour toutes les questions info.
    Découpe les sections par question et émet 1 segment_done par entrée.
    Retourne la liste des final_response pour consolidation.
    """

    docs_per_question = []
    for entry in success_entries:
        raw_content = _extract_raw_content(entry["result"])
        docs_per_question.append(raw_content.strip())

    numbered_docs = "\n\n".join(
        f"--- CONTENU QUESTION {i+1} ---\n{doc}"
        for i, doc in enumerate(docs_per_question)
        if doc
    )

    prompt = f"""Tu es un assistant voyage expert. L'utilisateur a posé {len(success_entries)} questions distinctes.
⚠️ RÈGLE ABSOLUE : Réponds UNIQUEMENT en {target_language}.
Même si le contenu ci-dessous est en français, ta réponse doit être en {target_language}.
Réponds DIRECTEMENT à CHACUNE en utilisant UNIQUEMENT le contenu documentaire fourni.

QUESTIONS DE L'UTILISATEUR:
"{raw_text}"

CONTENU DOCUMENTAIRE DISPONIBLE (1 bloc par question):
{numbered_docs[:4000]}

RÈGLES STRICTES:
1. Réponds DIRECTEMENT à chaque question, pas de généralités
2. Utilise UNIQUEMENT les informations des blocs documentaires ci-dessus
3. Si une information spécifique est absente du contenu, dis-le clairement
4. Langue: {target_language} UNIQUEMENT — aucun mot dans une autre langue
5. 1 objet par question dans le tableau "questions"
6. "message" par question : 2-3 phrases directes
7. "items" : liste de points courts extraits du document (sans puces ni astérisques)
8. NE génère PAS de contenu inventé ou générique
9. Retourne UNIQUEMENT ce JSON (sans backticks) :
{{
  "questions": [
    {{
      "message": "réponse directe question 1",
      "follow_up": "question courte pour approfondir",
      "sections": [
        {{
          "title": "titre pertinent",
          "content": "synthèse courte",
          "items": ["Point A", "Point B"]
        }}
      ]
    }},
    {{
      "message": "réponse directe question 2",
      "follow_up": "question courte pour approfondir",
      "sections": [
        {{
          "title": "titre pertinent",
          "content": "synthèse courte",
          "items": ["Point A", "Point B"]
        }}
      ]
    }}
  ]
}}

JSON:"""

    fallback_per_entry = [
        {
            "message":  e["result"].get("message", ""),
            "follow_up": "Avez-vous d'autres questions ?",
            "sections": _normalize_items(e["result"].get("sections", [])),
        }
        for e in success_entries
    ]

    parsed_questions = fallback_per_entry
    try:
        response = await _call_openai(prompt, tone_instruction)
        if response:
            cleaned  = _clean_json(response)
            parsed   = json.loads(cleaned)
            gpt_q = parsed.get("questions", [])
            if len(gpt_q) == len(success_entries):
                parsed_questions = gpt_q
            else:
                logger.warning(
                    f"[NARRATE] ⚠ Gemini multi_info: {len(gpt_q)} questions "
                    f"pour {len(success_entries)} attendues → fallback partiel"
                )
                for i in range(len(success_entries)):
                    if i < len(gpt_q):
                        parsed_questions[i] = gpt_q[i]
    except Exception as e:
        logger.error(f"[NARRATE] ❌ Gemini multi_info: {e}")

    finals = []
    for i, entry in enumerate(success_entries):
        seg_id  = _make_seg_id(entry["agent_name"], seg_counter[0])
        q_data  = parsed_questions[i] if i < len(parsed_questions) else fallback_per_entry[i]

        message   = q_data.get("message", "") or entry["result"].get("message", "Voici les informations.")
        follow_up = q_data.get("follow_up", "Avez-vous d'autres questions ?")
        sections  = _normalize_items(q_data.get("sections", entry["result"].get("sections", [])))

        await emit({
            "type":        "segment_done",
            "seg_id":      seg_id,
            "result_type": "info_results",
            "message":     message,
            "follow_up":   follow_up,
            "sections":    sections,
            "actions":     entry["result"].get("actions", []),
            "suggestions": [],  
        })
        logger.info(f"[NARRATE] ✅ segment_done(info) | seg_id={seg_id} | {message[:80]}")

        final_payload = {
            "type":            "info_results",
            "message":         message,
            "follow_up":       follow_up,
            "sections":        sections,
            "actions":         entry["result"].get("actions", []),
            "suggestions":     suggestions,
            "loyalty_summary": loyalty_summary,
        }
        finals.append({"final_response": json.dumps(final_payload, ensure_ascii=False)})
        seg_counter[0] += 1

    logger.info(f"[NARRATE] ✅ multi_info terminé | {len(success_entries)} segments émis")
    return finals



async def _narrate_human(
    entry: dict,
    suggestions: list,
    loyalty_summary: dict,
    tone_instruction: str,
    target_language: str,
    seg_id: str,
) -> dict:
    result           = entry["result"]
    original_message = result.get("message", "")
    sections         = result.get("sections", [])
    actions          = result.get("actions", [])

    contact_info  = ""
    section_title = sections[0].get("title", "") if sections else ""

    for section in sections:
        for item in section.get("items", []):
            if any(c in item for c in ("📞", "📧", "@", "+")):
                contact_info = item
                break
        if contact_info:
            break

    if not contact_info and actions:
        for action in actions:
            if action.get("contact"):
                contact_info = action.get("contact")
                break

    human_prompt = f"""Tu es un assistant voyage. Reformule ce message de manière naturelle et empathique en {target_language}.
⚠️ RÈGLE ABSOLUE : Réponds UNIQUEMENT en {target_language}. Même si le message original est en français, ta réponse doit être en {target_language}.

MESSAGE ORIGINAL:
"{original_message}"

INFORMATIONS DISPONIBLES:
- Titre: {section_title}
- Contact: {contact_info if contact_info else 'non spécifié'}

RÈGLES:
1. Langue: {target_language} UNIQUEMENT — aucun mot dans une autre langue
2. Sois empathique et compréhensif
3. Indique clairement qu'un conseiller doit être contacté
4. Mentionne le moyen de contact si disponible
5. Termine par une question ouverte
6. Retourne UNIQUEMENT ce JSON (sans backticks):
{{
  "message": "message reformulé en 2-3 phrases",
  "follow_up": "question courte pour continuer l'assistance"
}}

JSON:"""

    reformulated_message = original_message
    follow_up_fallback = {
    "français": "Souhaitez-vous d'autres informations ?",
    "arabe":    "هل لديك أي استفسارات أخرى؟",
    "anglais":  "Do you have any other questions?",
    }
    follow_up = follow_up_fallback.get(target_language, follow_up_fallback["français"])

    try:
        response = await _call_openai(human_prompt, tone_instruction)
        if response:
            parsed               = json.loads(_clean_json(response))
            reformulated_message = parsed.get("message", original_message)
            follow_up            = parsed.get("follow_up", follow_up)
    except Exception as e:
        logger.error(f"[NARRATE] ❌ Reformulation human_required: {e}")

    await emit({
        "type":        "segment_done",
        "seg_id":      seg_id,
        "result_type": "human_required",
        "message":     reformulated_message,
        "follow_up":   follow_up,
        "sections":    sections,
        "actions":     actions,
        "suggestions": [],
    })
    logger.info(f"[NARRATE] ✅ segment_done(human) | seg_id={seg_id} | {reformulated_message[:80]}")

    final_payload = {
        "type":            "info_results",
        "message":         reformulated_message,
        "follow_up":       follow_up,
        "sections":        sections,
        "actions":         actions,
        "suggestions":     suggestions,
        "loyalty_summary": loyalty_summary,
        "status":          "human_required",
    }
    return {"final_response": json.dumps(final_payload, ensure_ascii=False)}





def _consolidate_finals(finals: list, suggestions: list, loyalty_summary: dict) -> dict:
    """
    Consolide N final_response en 1 seul pour chat.py.
    Utilisé uniquement pour la sauvegarde Firestore et le json final.
    """
    if not finals:
        return _build_empty_final(suggestions, loyalty_summary)

    if len(finals) == 1:
        return finals[0]


    segments_data = []
    messages      = []
    follow_ups    = []

    for f in finals:
        try:
            parsed = json.loads(f.get("final_response", "{}"))
            segments_data.append(parsed)
            if parsed.get("message"):
                messages.append(parsed["message"])
            if parsed.get("follow_up"):
                follow_ups.append(parsed["follow_up"])
        except Exception:
            pass

    consolidated = {
        "type":            "multi_search_results",
        "message":         " ".join(messages[:2]),  
        "follow_up":       follow_ups[0] if follow_ups else "Avez-vous d'autres questions ?",
        "segments":        segments_data,
        "suggestions":     suggestions,
        "loyalty_summary": loyalty_summary,
    }
    return {"final_response": json.dumps(consolidated, ensure_ascii=False)}


def _build_empty_final(suggestions: list, loyalty_summary: dict) -> dict:
    payload = {
        "type":            "info_results",
        "message":         "",
        "follow_up":       "",
        "sections":        [],
        "actions":         [],
        "suggestions":     suggestions,
        "loyalty_summary": loyalty_summary,
    }
    return {"final_response": json.dumps(payload, ensure_ascii=False)}


def _build_all_error_final(
    error_entries: list, language: str,
    suggestions: list, loyalty_summary: dict
) -> dict:
    error_messages = {
        "query_fr":      "Le service est temporairement indisponible. Veuillez réessayer dans quelques instants.",
        "query_ar":      "الخدمة غير متاحة مؤقتاً. يرجى المحاولة مرة أخرى.",
        "query_en":      "The service is temporarily unavailable. Please try again shortly.",
        "query_derja_l": "الخدمة غير متاحة مؤقتاً. يرجى المحاولة مرة أخرى.",
        "query_derja_a": "الخدمة غير متاحة مؤقتاً. يرجى المحاولة مرة أخرى.",
    }
    msg = error_messages.get(language, error_messages["query_fr"])
    payload = {
        "type":            "info_results",
        "message":         msg,
        "follow_up":       "Souhaitez-vous réessayer ou tenter une autre recherche ?",
        "sections":        [],
        "actions":         [],
        "suggestions":     suggestions,
        "loyalty_summary": loyalty_summary,
        "status":          "api_error",
    }
    logger.info(f"[NARRATE] ⚠ all_errors | {len(error_entries)} segments en erreur")
    return {"final_response": json.dumps(payload, ensure_ascii=False)}




async def _inject_ambiguous_clarification(
    narrate_result: dict,
    state: dict,
) -> dict:
    from services.gpt import ask_gpt_clarification

    ambiguous_queue = state.get("ambiguous_queue", [])
    if not ambiguous_queue:
        return narrate_result

    language = state.get("language", "query_fr")
    current  = ambiguous_queue[0]

    try:
        clarif_result = ask_gpt_clarification(
            current["segment"], current["topk"], language
        )
        question = clarif_result.get("question", "")
        phrase_a = clarif_result.get("phrase_a", current["topk"][0].get("label", ""))
        phrase_b = clarif_result.get("phrase_b", current["topk"][1].get("label", "") if len(current["topk"]) > 1 else "")


        enriched_topk = []
        for i, sub in enumerate(current["topk"]):
            enriched_topk.append({
                **sub,
                "display_phrase": phrase_a if i == 0 else phrase_b,
            })
    except Exception as e:
        logger.error(f"[NARRATE] ❌ ask_gpt_clarification: {e}")
        return narrate_result

    final_str = narrate_result.get("final_response")
    if not final_str:
        return narrate_result

    try:
        final_obj = json.loads(final_str)
        final_obj["pending_clarification"] = {
            "question":        question,
            "subcats":         enriched_topk,
            "segment":         current["segment"],
            "queue_remaining": len(ambiguous_queue) - 1,
        }
        narrate_result["final_response"] = json.dumps(final_obj, ensure_ascii=False)
        logger.info(
            f"[NARRATE] 💬 Clarification injectée | "
            f"seg='{current['segment'][:50]}' | "
            f"queue_remaining={len(ambiguous_queue) - 1}"
        )
    except Exception as e:
        logger.error(f"[NARRATE] ❌ Inject clarification: {e}")

    return narrate_result




def _triage_results(results: dict) -> dict:
    buckets = {"success": [], "error": [], "more_info": [], "human": []}
    if isinstance(results, list):
        logger.warning("[NARRATE] ⚠ results est une liste, conversion en dict")
        results = {f"agent_{i}": r for i, r in enumerate(results) if isinstance(r, dict)}
    for agent_name, result in results.items():
        if not isinstance(result, dict):
            continue

        status = result.get("status", "unknown")
        entry  = {"agent_name": agent_name, "result": result,
                  "result_type": _detect_result_type(result)}

        if status in ("success", "gpt_fallback", "rag_only", "llm_only"):
            buckets["success"].append(entry)
        elif status == "api_error":
            buckets["error"].append(entry)
        elif status == "need_more_info":
            buckets["more_info"].append(entry)
        elif status == "human_required":
            buckets["human"].append(entry)

    if len(buckets["more_info"]) > 1:
        buckets["more_info"] = _merge_forms(buckets["more_info"])
    return buckets

def _merge_forms(more_info_entries: list) -> list:
    """
    Fusionne N formulaires need_more_info en 1 seul.
    Gère les cas : hotel+vol, hotel+tour, vol+tour, hotel+vol+tour.
    """
    if not more_info_entries:
        return more_info_entries

    all_fields_by_agent: dict[str, list] = {}
    agent_names: list[str] = []

    for entry in more_info_entries:
        agent_name = entry["agent_name"]
        agent_names.append(agent_name)
        form   = entry["result"].get("form", {})
        fields = form.get("fields", [])
        all_fields_by_agent[agent_name] = fields

    seen_fields: dict[str, dict] = {}
    for fields in all_fields_by_agent.values():
        for field in fields:
            fname = field.get("name")
            if fname and fname not in seen_fields:
                seen_fields[fname] = field

    if not seen_fields:
        return [more_info_entries[0]]

    has_flight = any(
        any(k in a.lower() for k in ("flight", "vol", "transport"))
        for a in agent_names
    )
    has_hotel = any(
        any(k in a.lower() for k in ("hotel", "stay"))
        for a in agent_names
    )
    has_tour = any(
        any(k in a.lower() for k in ("tour", "excursion", "activity"))
        for a in agent_names
    )

    sections = []
    for agent_name, fields in all_fields_by_agent.items():
        if not fields:
            continue
        is_flight = any(k in agent_name.lower() for k in ("flight", "vol", "transport"))
        is_tour   = any(k in agent_name.lower() for k in ("tour", "excursion", "activity"))

        if is_flight:
            label, icon = "Vol", "✈️"
        elif is_tour:
            label, icon = "Tour", "🗺️"
        else:
            label, icon = "Hôtel", "🏨"

        sections.append({
            "agent":  agent_name,
            "label":  label,
            "icon":   icon,
            "fields": [f["name"] for f in fields],
        })

    if has_flight and has_hotel and has_tour:
        sub_category = "Mixed Booking"
        title        = "Votre voyage complet"
        description  = "Complétez les informations pour votre vol, hôtel et activités"
    elif has_hotel and has_tour:
        sub_category = "Mixed Booking"
        title        = "Hôtel & Activités"
        description  = "Complétez les informations pour votre hôtel et vos activités"
    elif has_flight and has_tour:
        sub_category = "Mixed Booking"
        title        = "Vol & Activités"
        description  = "Complétez les informations pour votre vol et vos activités"
    else:
        sub_category = "Mixed Booking"
        title        = "Informations requises"
        description  = "Complétez les informations pour votre réservation"

    merged_form = {
        "type":        "form",
        "title":       title,
        "description": description,
        "fields":      list(seen_fields.values()),
        "sections":    sections,
        "agent_types": {
            "has_flight": has_flight,
            "has_hotel":  has_hotel,
            "has_tour":   has_tour,
        },
    }

    first = more_info_entries[0].copy()
    first["result"] = {
        **first["result"],
        "form":           merged_form,
        "message":        "Veuillez compléter les informations ci-dessous",
        "_merged_agents": agent_names,
        "sub_category":   sub_category,
    }
    return [first]

def _detect_result_type(result: dict) -> tuple[str, list]:
    result_type_raw = result.get("type", "")

    explicit_map = {
        "hotel_search_results":      ("hotels",      "results", "offers"),
        "flight_search_results":     ("offers",      "results"),
        "transport_search_results":  ("transports",  "results"),
        "restaurant_search_results": ("restaurants", "results"),
        "specialty_search_results":  ("specialties", "results"),
        "activity_search_results":   ("activities",  "results"),
    }
    if result_type_raw in explicit_map:
        keys = explicit_map[result_type_raw]
        for k in keys:
            val = result.get(k)
            if isinstance(val, list) and val:
                return result_type_raw, val
            if isinstance(val, dict) and "results" in val:
                return result_type_raw, val["results"]
        return "info_results", []

    candidate = result.get("results") or result.get("hotels") or result.get("offers") or []
    if isinstance(candidate, list) and candidate:
        first = candidate[0] if isinstance(candidate[0], dict) else {}
        item_type = first.get("type", "")
        if item_type == "hotel" or first.get("stars") is not None:
            return "hotel_search_results", candidate
        if item_type == "restaurant":
            return "restaurant_search_results", candidate
        if item_type == "specialty":
            return "specialty_search_results", candidate
        if item_type == "flight":
            return "flight_search_results", candidate

    for key, rtype in [
        ("hotels",      "hotel_search_results"),
        ("transports",  "transport_search_results"),
        ("offers",      "flight_search_results"),
    ]:
        if result.get(key) and isinstance(result[key], list):
            return rtype, result[key]

    for key in ("restaurants", "activities"):
        val = result.get(key)
        if val:
            lst = val.get("results", []) if isinstance(val, dict) else val if isinstance(val, list) else []
            rtype = "restaurant_search_results" if key == "restaurants" else "activity_search_results"
            return rtype, lst

    if (result_type_raw == "info_results"
            or result.get("status") == "rag_only"
            or (result.get("sections") and not result.get("offers"))):
        return "info_results", []

    return "unknown", []




def _extract_raw_content(result: dict) -> str:
    """Extrait le contenu textuel brut d'un résultat info pour le prompt Gemini."""
    sections    = result.get("sections", [])
    raw_content = ""
    top_message = result.get("message", "")

    if top_message:
        raw_content += f"{top_message}\n\n"

    for section in sections:
        if not isinstance(section, dict):
            continue
        title   = section.get("title", "")
        content = section.get("content", "")
        if not content:
            continue
        if top_message and content.strip() in top_message.strip():
            continue
        raw_content += f"{title}\n{content}\n"
        items = section.get("items", [])
        real_items = [
            i for i in items
            if isinstance(i, str)
            and not any(kw in i.lower() for kw in (
                "analyse", "recherche en cours", "traitement",
                "chargement", "vérification", "récupération"
            ))
        ]
        if real_items:
            raw_content += "\n".join(f"- {i}" for i in real_items) + "\n"

    return raw_content.strip()


def _resolve_error_label(entry: dict) -> str:
    result     = entry["result"]
    agent_name = entry["agent_name"]

    explicit_type = result.get("type", "")
    if explicit_type in TYPE_LABELS:
        return TYPE_LABELS[explicit_type]

    AGENT_NAME_MAP = {
        "flight": "vols", "flight_agent": "vols", "flights": "vols", "transport_air": "vols",
        "hotel": "hôtels", "hotel_agent": "hôtels", "hotels": "hôtels", "stay": "hôtels", "stay_agent": "hôtels",
        "transport": "transports", "transport_agent": "transports", "transports": "transports",
        "car_rental": "transports", "bus": "transports", "train": "transports",
        "restaurant": "restaurants", "restaurant_agent": "restaurants", "restaurants": "restaurants",
        "dining": "restaurants", "gastronomy": "spécialités",
        "activity": "activités", "activity_agent": "activités", "activities": "activités",
        "event": "activités", "tour": "activités", "excursion": "activités","gastronomy_agent":  "spécialités",
        "specialty": "spécialités", "specialty_agent": "spécialités", "specialties": "spécialités",
        "food": "spécialités",
    }
    agent_key = agent_name.lower()
    for key, label in AGENT_NAME_MAP.items():
        if key in agent_key:
            return label

    rtype, _ = _detect_result_type(result)
    if rtype in TYPE_LABELS:
        return TYPE_LABELS[rtype]

    return agent_name.replace("_", " ").replace("agent", "").strip()


def _build_error_phrase(error_entries: list, target_language: str) -> str:
    if not error_entries:
        return ""
    labels = []
    seen   = set()
    for entry in error_entries:
        label = _resolve_error_label(entry)
        if label not in seen:
            seen.add(label)
            labels.append(f"les {label}")

    if not labels:
        return ""

    if len(labels) == 1:
        joined = labels[0]
    elif len(labels) == 2:
        joined = f"{labels[0]} et {labels[1]}"
    else:
        joined = ", ".join(labels[:-1]) + f" et {labels[-1]}"

    phrases = {
        "français":                           f"{joined.capitalize()} sont temporairement indisponibles.",
        "anglais":                            f"{joined.capitalize()} are temporarily unavailable.",
        "arabe":                              f"{joined} غير متاحة مؤقتاً.",
        "darija tunisienne (alphabet latin)": f"{joined} moch mawjoudin daba.",
        "darija tunisienne (alphabet arabe)": f"{joined} ماهيش موجودين دابا.",
    }
    logger.info(f"[NARRATE] 🔴 Segments en erreur → {joined}")
    return phrases.get(target_language, phrases["français"])


def _normalize_items(sections: list) -> list:
    normalized = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        items       = section.get("items", [])
        clean_items = []
        for item in items:
            if isinstance(item, str):
                clean_items.append(item)
            elif isinstance(item, dict):
                parts = [f"{k} : {v}" for k, v in item.items()]
                clean_items.append(" | ".join(parts))
            elif item is not None:
                clean_items.append(str(item))
        normalized.append({**section, "items": clean_items})
    return normalized


def _clean_json(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _parse_gpt_response(raw: str) -> dict:
    try:
        return json.loads(_clean_json(raw))
    except Exception as e:
        logger.error(f"[NARRATE] Parse error: {e}")
        return {"message": "", "follow_up": ""}


async def _call_openai(prompt: str, tone_instruction: str, temperature: float = 0) -> str:
    cache_key = hashlib.sha256((tone_instruction + prompt).encode()).hexdigest()[:20]
    cached = _narrate_cache.get(cache_key)
    if cached:
        text, ts = cached
        if _time.time() - ts < _NARRATE_CACHE_TTL:
            logger.info("[GPT] ✅ Cache narration hit")
            return text

    async with httpx.AsyncClient(
        verify=False,
        timeout=httpx.Timeout(60.0, connect=10.0)
    ) as h:
        response = await h.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "max_tokens": 1000,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": tone_instruction},
                    {"role": "user", "content": prompt},
                ],
            }
        )
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"] or ""
        logger.info(f"[GPT] ✅ Réponse reçue, longueur: {len(text)}")
        _narrate_cache[cache_key] = (text, _time.time())
        if len(_narrate_cache) > 300:
            now = _time.time()
            expired = [k for k, (_, ts) in _narrate_cache.items() if now - ts > _NARRATE_CACHE_TTL]
            for k in expired:
                del _narrate_cache[k]
        return text


def _extract_query_from_result(result: dict) -> dict:
    if result.get("query"):
        return result["query"]
    offers = result.get("offers") or result.get("results") or result.get("restaurants")
    if offers and isinstance(offers, list) and offers:
        first = offers[0]
        return {
            "city":        first.get("location", {}).get("city", first.get("city", "")),
            "origin":      first.get("departure", {}).get("city", ""),
            "destination": first.get("arrival",   {}).get("city", ""),
        }
    return {}

async def _process_form_response(state: dict, form_data: dict) -> dict:
    """
    Traite la réponse d'un formulaire.
    - Si d'autres formulaires sont en attente → émettre le suivant.
    - Sinon → reconstruire le state complet et rappeler narrate() normalement.
    """
    pending_segments = state.get("pending_segments", {})
    remaining_forms  = pending_segments.get("more_info", [])

    if remaining_forms:
        next_form = remaining_forms[0]
        seg_id    = _make_seg_id(next_form["agent_name"], 0)
        await _emit_segment_form(next_form, seg_id)

        pending_segments["more_info"] = remaining_forms[1:]
        state["pending_segments"]     = pending_segments

        return {
            "final_response": json.dumps({
                "status":                  "waiting_for_form",
                "message":                 next_form["result"].get("message", ""),
                "form":                    next_form["result"].get("form"),
                "pending_segments_count":  len(pending_segments.get("success", [])) + len(remaining_forms[1:])
            }, ensure_ascii=False),
            "waiting_for_form": True,
            "pending_segments": pending_segments,
        }
    
    current_language = state.get("form_language") or state.get("language", "query_fr")

    state["waiting_for_form"] = False
    state["is_form_response"] = False

    state["suggestions"] = pending_segments.get("original_suggestions", [])
    if not state.get("loyalty_summary"):
        state["loyalty_summary"] = pending_segments.get("original_loyalty", {})
    if not state.get("raw_text"):
        state["raw_text"] = pending_segments.get("original_raw_text", "")

    state["language"] = current_language
    state["form_language"] = current_language

    logger.info(
        f"[NARRATE] 📋 form_response → reprise narrate | "
        f"résultats={list(state.get('results', {}).keys())}"
    )
    return await narrate(state)