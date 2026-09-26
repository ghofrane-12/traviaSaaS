# api/chat.py
import logging
import sys
import json
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from core.schemas import AuthContext, ChatRequest
from methods.auth import get_current_user
from core.firestore import firestore_service
from orchestrator.graph import travel_graph, cross_sell_graph
from orchestrator.state import AgentState
from services.conversation_service import conversation_service
from datetime import datetime
from core.streaming import set_queue, emit
import asyncio


logger = logging.getLogger("chat_logger")
logger.setLevel(logging.DEBUG)

if logger.hasHandlers():
    logger.handlers.clear()

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S"))
logger.addHandler(console_handler)

file_handler = logging.FileHandler("chat_flow.log", mode="a", encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logger.addHandler(file_handler)

sys.stdout.reconfigure(line_buffering=True)

router = APIRouter(prefix="/chat", tags=["Chat"])




def _default_state_fields() -> dict:
    return {
        "segments":               [],
        "classification":         [],
        "entities":               {},
        "tenant_config":          {"tone": "casual", "currency": "EUR", "margin_percentage": 10},
        "context":                {},
        "clarification_pending":  False,
        "clarification_subcats":  [],
        "clarification_segment":  "",
        "clarification_choice":   None,
        "clarification_accepted": [],
        "clarification_entities": {},
        "ambiguous_queue":        [],
        "results":                {},
        "suggestions":            [],
        "loyalty_summary":        {},
        "final_response":         None,
        "error":                  None,
        "waiting_for_form":       False,
        "is_form_response":       False,
        "form_data":              {},
        "pending_segments":       {},
        "cross_sell_proposals":   [],  
        "is_cross_sell":          False,  
        "cross_sell_id":          None,   
        "active_subcategories": [],
    }


def format_entities(entities_dict: dict) -> list:
    result = []
    for k, vals in entities_dict.items():
        if vals is None:
            continue
        if isinstance(vals, list):
            for v in vals:
                if v is not None:
                    result.append({"type": k, "value": v})
        else:
            result.append({"type": k, "value": vals})
    return result


def parse_final_response(final_response_json) -> tuple:
    if not final_response_json:
        return "Je n'ai pas pu traiter votre demande.", [], [], None

    try:
        parsed = json.loads(final_response_json) if isinstance(final_response_json, str) else final_response_json

        if isinstance(parsed, dict) and "final_response" in parsed:
            try:
                parsed = json.loads(parsed["final_response"])
            except Exception:
                pass

        result_types = [
            "flight_search_results", "hotel_search_results", "specialty_search_results",
            "restaurant_search_results", "activity_search_results", "transport_search_results",
        ]

        if parsed.get("type") in result_types:
            message = parsed.get("message", "")
            if isinstance(message, str) and message.strip().startswith("{"):
                try:
                    inner = json.loads(message)
                    if isinstance(inner, dict):
                        message = inner.get("message", inner.get("reply", message))
                except Exception:
                    pass
            if not message:
                message = f"J'ai trouvé {len(parsed.get('results', []))} résultat(s)."
            return message, parsed.get("results", []), parsed.get("suggestions", []), parsed.get("follow_up")

        if parsed.get("type") == "info_results":
            return parsed.get("message", ""), parsed, parsed.get("suggestions", []), parsed.get("follow_up")

        return (
            parsed.get("message", str(final_response_json)),
            parsed.get("results_summary", []),
            parsed.get("suggestions", []),
            parsed.get("follow_up"),
        )
    except json.JSONDecodeError as e:
        logger.error(f"[PARSE] JSON decode error: {e}")
        return final_response_json, [], [], None


def extract_results_list(first_result: dict) -> list:
    if not first_result or first_result.get("status") not in ("success", "gemini_fallback"):
        return []
    for key in ("activities", "specialties", "restaurants"):
        if key in first_result:
            val = first_result[key]
            if isinstance(val, dict) and "results" in val:
                return val["results"][:10]
            if isinstance(val, list):
                return val[:10]
    offers = first_result.get("offers") or first_result.get("results")
    if isinstance(offers, list):
        return offers[:10]
    if isinstance(offers, dict):
        for sub in ("flightOffers", "results", "hotels"):
            if sub in offers:
                return offers[sub][:10]
    return []


def _is_clarification(final_state: dict) -> bool:
    return bool(final_state.get("clarification_pending"))


def _build_clarification_response(final_state: dict, session_id: str,
                                   tenant_id: str, user_id: str) -> dict:
    final_response_raw = final_state.get("final_response", "{}")
    try:
        parsed = json.loads(final_response_raw) if isinstance(final_response_raw, str) else final_response_raw
    except Exception:
        parsed = {}

    return {
        "reply":                   parsed.get("message", "Pouvez-vous préciser votre demande ?"),
        "status":                  "clarification_needed",
        "subcats":                 parsed.get("_meta", {}).get("subcats", []),
        "segment":                 parsed.get("_meta", {}).get("segment", ""),
        "_meta": {
            "status":          "clarification_needed",
            "subcats":         parsed.get("_meta", {}).get("subcats", []),
            "segment":         parsed.get("_meta", {}).get("segment", ""),
            "ambiguous_queue": parsed.get("_meta", {}).get("ambiguous_queue", []),  
        },
        "session_id":              session_id,
        "tenant_id":               tenant_id,
        "user_id":                 user_id,
        "language":                final_state.get("language", "query_fr"),
        "category":                None,
        "sub_category":            None,
        "results":                 [],
        "offers":                  [],
        "suggestions":             [],
        "follow_up":               None,
        "clarification_accepted":  final_state.get("clarification_accepted", []),
        "clarification_entities":  final_state.get("clarification_entities", {}),
    }


def _build_session_ids(current_user, request):
    if current_user.role == "visitor" or current_user.is_anonymous:
        
        tenant_id = current_user.tenant_id or request.tenant_id
        
        if not tenant_id:
            raise HTTPException(
                status_code=400, 
                detail="tenant_id requis. Accédez via le lien de votre agence."
            )
        
        user_id = current_user.user_id
        session_id = (
            request.session_id.replace(":", "_") 
            if request.session_id
            else f"visitor_{user_id}_{datetime.utcnow().strftime('%Y%m%d%H%M')}"
        )
    else:
        tenant_id = current_user.tenant_id
        if not tenant_id:
            raise HTTPException(status_code=400, detail="tenant_id invalide")
        
        user_id = current_user.user_id
        if request.session_id:
            session_id = request.session_id.replace(":", "_")
        else:
            session_id = f"{user_id}__{tenant_id}__{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"

    return tenant_id, user_id, session_id


def _build_initial_state(request, session_id: str, tenant_id: str, user_id: str, is_anonymous: bool = False) -> AgentState:
    base = _default_state_fields()

    is_form_response = getattr(request, "is_form_response", False) or False
    if is_form_response:
        pending_segments = getattr(request, "pending_segments", {}) or {}
        form_data        = getattr(request, "form_data", {})        or {}
        language         = _normalize_language(request.language or "query_fr")

        results = {}
        for entry in pending_segments.get("success", []):
            results[entry["agent_name"]] = entry["result"]
        for entry in pending_segments.get("error", []):
            results[entry["agent_name"]] = entry["result"]
        for entry in pending_segments.get("human", []):
            results[entry["agent_name"]] = entry["result"]

        first_more_info = pending_segments.get("more_info_submitted") or {}
        form_sub_category = first_more_info.get("sub_category", "")

        merged_agents = pending_segments.get("_merged_agents", [])

        if not merged_agents:
            if not form_sub_category:
                if any(k in form_data for k in ("origin", "destination", "departure_date", "cabin_class", "adults")):
                    form_sub_category = "Flight Booking"
                elif any(k in form_data for k in ("city", "check_in", "check_out", "rooms")):
                    form_sub_category = "Hotel Booking"
                elif any(k in form_data for k in ("city", "departure_date")) and "check_in" not in form_data:
                    form_sub_category = "Tour Booking"
                else:
                    form_sub_category = "Hotel Booking"
            merged_agents = [form_sub_category]
        else:
            merged_agents = [
                a.split(":")[-1].strip() if ":" in a else a
                for a in merged_agents
        ]
        classification = [
            {"sub_category": sub, "category": "Reservation", "confidence": 1.0}
            for sub in merged_agents
        ]

        form_entities_dict: dict = {}
 
        if isinstance(form_data, dict):
 
            dates = []
            if form_data.get("check_in"):       dates.append(form_data["check_in"])
            if form_data.get("check_out"):      dates.append(form_data["check_out"])
            if form_data.get("departure_date"): dates.append(form_data["departure_date"])
            if form_data.get("return_date"):    dates.append(form_data["return_date"])
            if dates:
                uniq_dates = list(dict.fromkeys(dates))
                form_entities_dict["DATE"]           = uniq_dates
                form_entities_dict["departure_date"] = uniq_dates[0]
                form_entities_dict["return_date"]    = uniq_dates[1] if len(uniq_dates) > 1 else None
                form_entities_dict["check_in"]  = uniq_dates[0]
                form_entities_dict["check_out"] = uniq_dates[1] if len(uniq_dates) > 1 else None
 
            guests = form_data.get("guests") or form_data.get("adults")
            if guests:
                form_entities_dict["guests"]  = [str(guests)]
                form_entities_dict["adults"]  = [str(guests)]
                form_entities_dict["PERSONS"] = [str(guests)]
            if form_data.get("rooms"):
                form_entities_dict["rooms"] = [str(form_data["rooms"])]
 
            hotel_city = form_data.get("city")
            if hotel_city:
                form_entities_dict["city"] = [hotel_city]
                form_entities_dict["hotel_city"] = hotel_city 
 
            flight_origin = form_data.get("origin")
            flight_dest   = form_data.get("destination")

            if flight_origin or flight_dest:
                locs = [x for x in [flight_origin, flight_dest] if x]
                form_entities_dict["LOC"]         = locs
                form_entities_dict["origin"]      = flight_origin or ""
                form_entities_dict["destination"] = flight_dest   or ""
            tour_city = form_data.get("tour_city")
            tour_date = form_data.get("tour_departure_date") or form_data.get("departure_date")

            if hotel_city and not flight_origin and not flight_dest:
                effective_tour_city = tour_city or hotel_city
                form_entities_dict["LOC"]         = list(dict.fromkeys(
                    [hotel_city] + ([tour_city] if tour_city and tour_city != hotel_city else [])
                ))
                form_entities_dict["destination"] = effective_tour_city
                form_entities_dict["origin"]      = effective_tour_city
                if tour_date and "DATE" not in form_entities_dict:
                    form_entities_dict["DATE"]           = [tour_date]
                    form_entities_dict["departure_date"] = tour_date
            elif tour_city and not hotel_city and not flight_origin:
                form_entities_dict["LOC"]         = [tour_city]
                form_entities_dict["destination"] = tour_city
                form_entities_dict["origin"]      = tour_city
                if tour_date and "DATE" not in form_entities_dict:
                    form_entities_dict["DATE"]           = [tour_date]
                    form_entities_dict["departure_date"] = tour_date

 
            if form_data.get("cabin_class"):
                form_entities_dict["cabin_class"] = [form_data["cabin_class"]]
                form_entities_dict["MISC"]        = [form_data["cabin_class"]]
 
            skip = {
                "city", "guests", "adults", "rooms",
                "check_in", "check_out", "departure_date", "return_date",
                "origin", "destination", "cabin_class",
            }
            for k, v in form_data.items():
                if v is not None and v != "" and k not in skip and k not in form_entities_dict:
                    form_entities_dict[k] = [str(v)] if not isinstance(v, list) else v

        return {**base,
                "raw_text":          getattr(request, "text", "") or "",
                "session_id":        session_id,
                "tenant_id":         tenant_id,
                "user_id":           user_id,
                "language":          language,
                "form_language":     language,
                "is_anonymous":      is_anonymous,
                "is_form_response":  False,
                "waiting_for_form":  False,
                "form_data":         form_data,
                "pending_segments":  pending_segments,
                "results":           results,
                "suggestions":       pending_segments.get("original_suggestions", []),
                "loyalty_summary":   pending_segments.get("original_loyalty", {}),
                "classification": classification,
                "entities":          form_entities_dict}

    if request.entities and request.classification:
        sub_category = request.classification[0].get("sub_category", "")
        language     = _normalize_language(request.language or "query_fr")
        raw_text     = request.text or ""

        if not raw_text:
            if "Hotel" in sub_category:
                city     = next((e.get("value", "") for e in request.entities if e.get("type") == "LOC"), "")
                raw_text = f"recherche hôtel à {city}"
            elif "Transport" in sub_category:
                locs     = [e.get("value", "") for e in request.entities if e.get("type") == "LOC"]
                raw_text = f"recherche vol de {locs[0] if locs else ''} à {locs[1] if len(locs) > 1 else ''}"

        entities_dict: dict = {}
        for e in request.entities:
            k, v = e.get("type"), e.get("value")
            if k and v is not None:
                entities_dict.setdefault(k, []).append(v)

        return {**base,
                "raw_text":       raw_text,
                "session_id":     session_id,
                "tenant_id":      tenant_id,
                "user_id":        user_id,
                "language":       language,
                "classification": request.classification,
                "is_anonymous":      is_anonymous,
                "entities":       entities_dict}

    clarification_pending  = getattr(request, "clarification_pending",  False) or False
    clarification_subcats  = getattr(request, "clarification_subcats",  [])    or []
    clarification_segment  = getattr(request, "clarification_segment",  "")    or ""
    clarification_accepted = getattr(request, "clarification_accepted", [])    or []
    clarification_entities = getattr(request, "clarification_entities", {})    or {}
    clarification_choice   = getattr(request, "clarification_choice",   None)  or None
    ambiguous_queue        = getattr(request, "ambiguous_queue",        [])    or []  


    return {**base,
            "raw_text":               request.text,
            "session_id":             session_id,
            "tenant_id":              tenant_id,
            "user_id":                user_id,
            "language":               _normalize_language(request.language),
            "clarification_pending":  clarification_pending,
            "clarification_subcats":  clarification_subcats,
            "clarification_segment":  clarification_segment,
            "clarification_accepted": clarification_accepted,
            "clarification_entities": clarification_entities,
            "clarification_choice":   clarification_choice,
            "is_anonymous":      is_anonymous,
            "ambiguous_queue":        ambiguous_queue}

def _normalize_language(lang: str) -> str:
    mapping = {
        "fr":            "query_fr",
        "query_fr":      "query_fr",
        "ar":            "query_ar",
        "query_ar":      "query_ar",
        "en":            "query_en",
        "query_en":      "query_en",
        "derja":         "query_derja_a",   
        "query_derja_a": "query_derja_a",  
        "query_derja_l": "query_derja_l",   
        "derja_latin":   "query_derja_l",
        "derja_arabe":   "query_derja_a",
        "derja_l":       "query_derja_l",
        "derja_a":       "query_derja_a",
    }
    return mapping.get(lang, lang) if lang else "query_fr"

def _build_response_data(final_state: dict, session_id: str,
                          tenant_id: str, user_id: str) -> dict:

    results             = final_state.get("results", {})
    final_response_json = final_state.get("final_response")

    if isinstance(final_response_json, dict) and "final_response" in final_response_json:
        final_response_json = final_response_json["final_response"]

    parsed_final = None
    if final_response_json:
        try:
            parsed_final = (json.loads(final_response_json)
                            if isinstance(final_response_json, str)
                            else final_response_json)
            if isinstance(parsed_final, dict) and "final_response" in parsed_final:
                parsed_final = json.loads(parsed_final["final_response"])
        except Exception:
            pass

    classification_list = final_state.get("classification") or [{}]
    classification      = classification_list[0] if classification_list else {}
    sub_category        = classification.get("sub_category", "General Information")
    category            = classification.get("category", "Info")
    language            = final_state.get("language", "query_fr")
    entities            = final_state.get("entities", {})
    suggestions         = final_state.get("suggestions", [])
    loyalty_summary     = final_state.get("loyalty_summary", {})

    first_result = next((r for r in results.values() if isinstance(r, dict)), None)

    if first_result and first_result.get("status") == "need_more_info":
        return {
            "reply":        first_result.get("message", ""),
            "category":     category,
            "sub_category": sub_category,
            "language":     language,
            "entities":     format_entities(entities),
            "session_id":   session_id,
            "tenant_id":    tenant_id,
            "user_id":      user_id,
            "status":       "need_more_info",
            "form":         first_result.get("form"),
            "steps":        first_result.get("steps", []),
        }

    if not parsed_final:
        return {
            "reply":      "Je n'ai pas pu traiter votre demande.",
            "status":     "error",
            "session_id": session_id,
            "tenant_id":  tenant_id,
            "user_id":    user_id,
        }

    resp_type  = parsed_final.get("type", "")
    message    = parsed_final.get("message", "")
    follow_up  = parsed_final.get("follow_up")
    pending_form          = parsed_final.get("pending_form")
    pending_clarification = parsed_final.get("pending_clarification")

    if resp_type == "multi_search_results":
        return {
            "reply":           message,
            "category":        category,
            "sub_category":    sub_category,
            "language":        language,
            "entities":        format_entities(entities),
            "session_id":      session_id,
            "tenant_id":       tenant_id,
            "user_id":         user_id,
            "status":          "success",
            "type":            "multi_search_results",
            "query":           {},
            "info": {
                "type":            "multi_search_results",
                "segments":        parsed_final.get("segments", []),
                "message":         message,
                "follow_up":       follow_up,
                "suggestions":     parsed_final.get("suggestions", suggestions),
                "loyalty_summary": parsed_final.get("loyalty_summary", loyalty_summary),
            },
            "results":         [],
            "offers":          [],
            "suggestions":     parsed_final.get("suggestions", suggestions),
            "loyalty_summary": parsed_final.get("loyalty_summary", loyalty_summary),
            "follow_up":       follow_up,
            "pending_form":    pending_form,
            "pending_clarification": pending_clarification,
        }

    if resp_type == "info_results":
        info_payload = {
            "type":            "info_results",
            "message":         message,
            "follow_up":       follow_up,
            "sections":        parsed_final.get("sections", []),
            "actions":         parsed_final.get("actions", []),
            "suggestions":     parsed_final.get("suggestions", suggestions),
            "loyalty_summary": parsed_final.get("loyalty_summary", loyalty_summary),
        }
        return {
            "reply":           message,
            "category":        category,
            "sub_category":    sub_category,
            "language":        language,
            "entities":        format_entities(entities),
            "session_id":      session_id,
            "tenant_id":       tenant_id,
            "user_id":         user_id,
            "status":          "success",
            "type":            "info_results",
            "query":           {},
            "results":         [],
            "offers":          [],
            "info":            info_payload,
            "suggestions":     parsed_final.get("suggestions", suggestions),
            "loyalty_summary": parsed_final.get("loyalty_summary", loyalty_summary),
            "follow_up":       follow_up,
            "pending_form":    pending_form,
            "pending_clarification": pending_clarification,
        }

    result_type_keys = {
        "flight_search_results":     ("results",),
        "hotel_search_results":      ("results",),
        "transport_search_results":  ("results",),
        "restaurant_search_results": ("results",),
        "activity_search_results":   ("results",),
        "specialty_search_results":  ("results",),
    }

    raw_results = []
    if resp_type in result_type_keys:
        for key in result_type_keys[resp_type]:
            val = parsed_final.get(key, [])
            if isinstance(val, list) and val:
                raw_results = val
                break

    return {
        "reply":           message,
        "category":        category,
        "sub_category":    sub_category,
        "language":        language,
        "entities":        format_entities(entities),
        "session_id":      session_id,
        "tenant_id":       tenant_id,
        "user_id":         user_id,
        "status":          "success",
        "type":            resp_type,
        "query":           parsed_final.get("query", {}),
        "results":         raw_results,
        "offers":          raw_results,
        "info":            None,
        "source":          first_result.get("source") if first_result else None,
        "suggestions":     parsed_final.get("suggestions", suggestions),
        "loyalty_summary": parsed_final.get("loyalty_summary", loyalty_summary),
        "steps":           first_result.get("steps", []) if first_result else [],
        "follow_up":       follow_up,
        "pending_form":    pending_form,
        "pending_clarification": pending_clarification,
    }



@router.post("/", response_model=list)
async def chat(request: ChatRequest, current_user: AuthContext = Depends(get_current_user)):
    tenant_id, user_id, session_id = _build_session_ids(current_user, request)
    logger.info(f"📩 CHAT | user={user_id} | role={current_user.role} | tenant={tenant_id} | text={request.text}")

    initial_state = _build_initial_state(request, session_id, tenant_id, user_id, is_anonymous=current_user.is_anonymous)

    try:
        final_state = await travel_graph.ainvoke(initial_state)

        if _is_clarification(final_state):
            logger.info("[CHAT] 🟡 Clarification needed → réponse spéciale")
            response = _build_clarification_response(final_state, session_id, tenant_id, user_id)
            return [response]

        response = _build_response_data(final_state, session_id, tenant_id, user_id)

        await firestore_service.save_message(
            session_id=session_id, tenant_id=tenant_id,
            user_uid=user_id, user_role=current_user.role,
            user_message=request.text,
            assistant_response={"reply": response["reply"], "category": response["category"],
                                 "sub_category": response["sub_category"], "status": response["status"]},
            is_anonymous=current_user.is_anonymous
        )
        return [response]

    except Exception as e:
        logger.error(f"❌ CHAT ERROR | {e}")
        return [{"reply": "Désolé, une erreur technique est survenue.",
                 "status": "error", "session_id": session_id,
                 "tenant_id": tenant_id, "user_id": user_id}]



@router.post("/stream")
async def chat_stream(
    raw_request: Request,
    current_user: AuthContext = Depends(get_current_user)
):
    from types import SimpleNamespace
    from core.schemas import ChatRequest as _ChatRequest

    body_bytes = await raw_request.body()
    body_json  = json.loads(body_bytes) if body_bytes else {}

    try:
        chat_req = _ChatRequest.model_validate(body_json)
    except Exception:
        chat_req = _ChatRequest(**{k: v for k, v in body_json.items()
                                   if k in _ChatRequest.model_fields})

    request_dict = chat_req.model_dump() if hasattr(chat_req, 'model_dump') else chat_req.dict()
    request_dict.update({k: body_json[k] for k in
                         ("is_form_response", "form_data", "pending_segments", "ambiguous_queue", "language")
                         if k in body_json})
    request = SimpleNamespace(**request_dict)

    tenant_id, user_id, session_id = _build_session_ids(current_user, request)

    logger.info(
        f"📡 STREAM | user={user_id} | tenant={tenant_id} | "
        f"text={request.text} | form_response={body_json.get('is_form_response', False)} | language={request.language}"
    )

    initial_state = _build_initial_state(request, session_id, tenant_id, user_id, is_anonymous=current_user.is_anonymous)

    queue: asyncio.Queue = asyncio.Queue()
    set_queue(queue)

    if body_json.get("is_cross_sell"):
        entities_dict: dict = {}
        for e in body_json.get("entities", []):
            k, v = e.get("type"), e.get("value")
            if k and v is not None:
                entities_dict.setdefault(k, []).append(v)

        initial_state = {
            **_default_state_fields(),
            "raw_text":       body_json.get("label", ""),
            "session_id":     session_id,
            "tenant_id":      tenant_id,
            "user_id":        user_id,
            "language":       _normalize_language(body_json.get("language", "query_fr")),
            "is_anonymous":   current_user.is_anonymous,
            "is_cross_sell":  True,
            "cross_sell_id":  body_json.get("cross_sell_id", ""),
            "classification": body_json.get("classification", []),
            "entities":       entities_dict,
        }
        logger.info(
            f"📡 CROSS-SELL | user={user_id} | tenant={tenant_id} | "
            f"cross_sell_id={body_json.get('cross_sell_id')} | "
            f"sub_category={body_json.get('classification', [{}])[0].get('sub_category', '')}"
        )
        graph_task = asyncio.create_task(_run_graph_cross_sell(initial_state, queue))
    else:
        graph_task = asyncio.create_task(_run_graph(initial_state, queue))

    async def event_stream():
        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=300.0)
                if event is None:
                    final_state = await graph_task

                    if _is_clarification(final_state):
                        clarif = _build_clarification_response(
                            final_state, session_id, tenant_id, user_id)
                        try:
                            await firestore_service.save_message(
                                session_id=session_id, tenant_id=tenant_id,
                                user_uid=user_id, user_role=current_user.role,
                                user_message=request.text,
                                assistant_response={"reply": clarif["reply"], "status": "clarification_needed"},
                                is_anonymous=current_user.is_anonymous
                            )
                        except Exception as e:
                            logger.error(f"[STREAM] Firestore error (clarification/sentinel): {e}")
                        yield f"data: {json.dumps({'type': 'json', 'data': clarif}, ensure_ascii=False)}\n\n"
                        for word in clarif["reply"].split():
                            yield f"data: {json.dumps({'type': 'text', 'word': word})}\n\n"
                            await asyncio.sleep(0.04)
                        yield "data: [DONE]\n\n"
                        return

                    final_response_raw = final_state.get("final_response")
                    if final_response_raw:
                        try:
                            fr     = json.loads(final_response_raw) if isinstance(final_response_raw, str) else final_response_raw
                            status = fr.get("_meta", {}).get("status", "")
                            if status in ("blocked", "rejected"):
                                message = fr.get("message", "⚠️ Cette demande ne peut pas être traitée.")
                                payload = {
                                    "reply": message, "status": status,
                                    "session_id": session_id, "tenant_id": tenant_id, "user_id": user_id,
                                }
                                yield f"data: {json.dumps({'type': 'json', 'data': payload}, ensure_ascii=False)}\n\n"
                                for word in message.split():
                                    yield f"data: {json.dumps({'type': 'text', 'word': word})}\n\n"
                                    await asyncio.sleep(0.03)
                                yield "data: [DONE]\n\n"
                                return
                        except Exception:
                            pass

                    fallback = {
                        "reply": "❌ Une erreur est survenue.", "status": "error",
                        "session_id": session_id, "tenant_id": tenant_id, "user_id": user_id,
                    }
                    yield f"data: {json.dumps({'type': 'json', 'data': fallback}, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                etype = event.get("type")

                if etype in ("status", "agent_start"):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    continue

                if etype == "segment_done":
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    logger.info(
                        f"[STREAM] 📤 segment_done | seg_id={event.get('seg_id')} | "
                        f"result_type={event.get('result_type')}"
                    )
                    continue

                if etype == "segment_form":
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    logger.info(
                        f"[STREAM] 📋 segment_form | seg_id={event.get('seg_id')} | "
                        f"sub_category={event.get('sub_category')}"
                    )
                    continue

                if etype == "stream_done":
                    final_state = await graph_task

                    blocked_status = event.get("status")
                    if blocked_status in ("blocked", "rejected"):
                        message = event.get("message", "⚠️ Cette demande ne peut pas être traitée.")
                        payload = {
                            "reply":      message,
                            "status":     blocked_status,
                            "session_id": session_id,
                            "tenant_id":  tenant_id,
                            "user_id":    user_id,
                        }
                        yield f"data: {json.dumps({'type': 'json', 'data': payload}, ensure_ascii=False)}\n\n"
                        for word in message.split():
                            yield f"data: {json.dumps({'type': 'text', 'word': word})}\n\n"
                            await asyncio.sleep(0.03)
                        yield "data: [DONE]\n\n"
                        return

                    if final_state.get("waiting_for_form"):
                        pending_segments = final_state.get("pending_segments", {})
                        fr_raw = final_state.get("final_response", "{}")
                        try:
                            fr = json.loads(fr_raw) if isinstance(fr_raw, str) else fr_raw
                        except Exception:
                            fr = {}
                        waiting_payload = {
                            "status":           "waiting_for_form",
                            "pending_segments": pending_segments,
                            "message":          fr.get("message", ""),
                            "form":             fr.get("form"),
                            "session_id":       session_id,
                            "tenant_id":        tenant_id,
                            "user_id":          user_id,
                        }
                        yield f"data: {json.dumps({'type': 'json', 'data': waiting_payload}, ensure_ascii=False)}\n\n"
                        yield "data: [DONE]\n\n"
                        try:
                            await firestore_service.save_message(
                                session_id=session_id, tenant_id=tenant_id,
                                user_uid=user_id, user_role=current_user.role,
                                user_message=request.text,
                                assistant_response={
                                    "reply": waiting_payload.get("message", ""),
                                    "category": "", "sub_category": "",
                                    "status": "waiting_for_form",
                                    "pending_segments": True,
                                },
                                is_anonymous=current_user.is_anonymous
                            )
                            logger.info(f"[STREAM] ✅ Firestore saved (waiting_for_form) | session={session_id}")
                        except Exception as e:
                            logger.error(f"[STREAM] ❌ Firestore error (waiting_for_form): {e}")
                        return

                    if _is_clarification(final_state):
                        clarif = _build_clarification_response(
                            final_state, session_id, tenant_id, user_id)
                        try:
                            await firestore_service.save_message(
                                session_id=session_id, tenant_id=tenant_id,
                                user_uid=user_id, user_role=current_user.role,
                                user_message=request.text,
                                assistant_response={
                                    "reply": clarif["reply"],
                                    "category": "", "sub_category": "",
                                    "status": "clarification_needed",
                                    "clarification_subcats": clarif.get("subcats", []),
                                },
                                is_anonymous=current_user.is_anonymous
                            )
                            logger.info(f"[STREAM] ✅ Firestore saved (clarification) | session={session_id}")
                        except Exception as e:
                            logger.error(f"[STREAM] ❌ Firestore error (clarification): {e}")
                        yield f"data: {json.dumps({'type': 'json', 'data': clarif}, ensure_ascii=False)}\n\n"
                        for word in clarif["reply"].split():
                            yield f"data: {json.dumps({'type': 'text', 'word': word})}\n\n"
                            await asyncio.sleep(0.04)
                        yield "data: [DONE]\n\n"
                        return

                    response = _build_response_data(
                        final_state, session_id, tenant_id, user_id)

                    if response.get("status") == "need_more_info":
                        try:
                            await firestore_service.save_message(
                                session_id=session_id, tenant_id=tenant_id,
                                user_uid=user_id, user_role=current_user.role,
                                user_message=request.text,
                                assistant_response={
                                    "reply": response.get("reply", ""),
                                    "category": response.get("category", ""),
                                    "sub_category": response.get("sub_category", ""),
                                    "status": "need_more_info",
                                },
                                is_anonymous=current_user.is_anonymous
                            )
                            logger.info(f"[STREAM] ✅ Firestore saved (need_more_info) | session={session_id}")
                        except Exception as e:
                            logger.error(f"[STREAM] ❌ Firestore error (need_more_info): {e}")
                        yield f"data: {json.dumps({'type': 'json', 'data': response}, ensure_ascii=False)}\n\n"
                        yield "data: [DONE]\n\n"
                        return

                    firestore_saved = False
                    try:
                        logger.info(f"[STREAM] 💾 Saving to Firestore | session={session_id} | user={user_id}")
                        assistant_response_data = {
                            "reply":          response.get("reply", ""),
                            "category":       response.get("category", ""),
                            "sub_category":   response.get("sub_category", ""),
                            "status":         response.get("status", "success"),
                            "segments_count": event.get("total_segments", 1),
                            "type":           response.get("type", ""),
                        }
                        if response.get("loyalty_summary"):
                            assistant_response_data["loyalty_summary"] = response.get("loyalty_summary")
                        if response.get("suggestions"):
                            assistant_response_data["suggestions"] = response.get("suggestions")

                        doc_id = await firestore_service.save_message(
                            session_id=session_id, tenant_id=tenant_id,
                            user_uid=user_id, user_role=current_user.role,
                            user_message=request.text,
                            assistant_response=assistant_response_data,
                            is_anonymous=current_user.is_anonymous
                        )
                        if doc_id:
                            firestore_saved = True
                            logger.info(f"[STREAM] ✅ Firestore saved successfully | doc_id={doc_id} | session={session_id}")
                        else:
                            logger.error(f"[STREAM] ❌ Firestore returned empty doc_id | session={session_id}")
                    except Exception as e:
                        logger.error(f"[STREAM] ❌ CRITICAL Firestore error: {type(e).__name__}: {e}")
                        import traceback
                        traceback.print_exc()
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


                    yield f"data: {json.dumps({'type': 'json', 'data': response}, ensure_ascii=False)}\n\n"

                    reply_to_stream = response.get("reply", "")
                    if reply_to_stream:
                        await asyncio.sleep(0.05)
                        for word in reply_to_stream.split():
                            yield f"data: {json.dumps({'type': 'text', 'word': word})}\n\n"
                            await asyncio.sleep(0.04)

                    yield "data: [DONE]\n\n"

                    if not firestore_saved:
                        logger.error(f"[STREAM] ⚠️ Conversation NOT saved to Firestore | session={session_id} | user={user_id}")

                    return                
                if etype == "narrator_done":
                    logger.warning("[STREAM] ⚠ narrator_done reçu — narrate.py pas encore mis à jour ?")
                    final_state = await graph_task

                    if _is_clarification(final_state):
                        clarif = _build_clarification_response(
                            final_state, session_id, tenant_id, user_id)
                        yield f"data: {json.dumps({'type': 'json', 'data': clarif}, ensure_ascii=False)}\n\n"
                        for word in clarif["reply"].split():
                            yield f"data: {json.dumps({'type': 'text', 'word': word})}\n\n"
                            await asyncio.sleep(0.04)
                        yield "data: [DONE]\n\n"
                        return

                    response = _build_response_data(
                        final_state, session_id, tenant_id, user_id)
                    yield f"data: {json.dumps({'type': 'json', 'data': response}, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0.05)
                    for word in response["reply"].split():
                        yield f"data: {json.dumps({'type': 'text', 'word': word})}\n\n"
                        await asyncio.sleep(0.04)
                    yield "data: [DONE]\n\n"
                    return

        except asyncio.TimeoutError:
            logger.error("[STREAM] Timeout 300s dépassé")
            yield f"data: {json.dumps({'type': 'json', 'data': {'reply': 'Délai dépassé.', 'status': 'error', 'session_id': session_id}})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.error(f"[STREAM] Generator error: {e}")
            yield f"data: {json.dumps({'type': 'json', 'data': {'reply': 'Erreur technique.', 'status': 'error', 'session_id': session_id}})}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            if not graph_task.done():
                graph_task.cancel()

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache",
                                       "X-Accel-Buffering": "no"})


async def _run_graph(initial_state: dict, queue: asyncio.Queue):
    """Lance le graph et envoie le sentinel None quand c'est fini."""
    try:
        result = await travel_graph.ainvoke(initial_state)
        return result
    except Exception as e:
        logger.error(f"[STREAM] Graph error: {e}", exc_info=True)
        await queue.put({
            "type":            "stream_done",
            "loyalty_summary": {},
            "follow_up":       "",
            "total_segments":  0,
            "error":           str(e),
        })
        return {}
    finally:
        await queue.put(None)

async def _run_graph_cross_sell(initial_state: dict, queue: asyncio.Queue):
    """
    Lance cross_sell_graph — démarre directement au router.
    Bypass : orchestrator, adapter, NLP, classifier.
    """
    try:
        result = await cross_sell_graph.ainvoke(initial_state)
        return result
    except Exception as e:
        logger.error(f"[STREAM] Cross-sell graph error: {e}", exc_info=True)
        await queue.put({
            "type":            "stream_done",
            "loyalty_summary": {},
            "follow_up":       "",
            "total_segments":  0,
            "error":           str(e),
        })
        return {}
    finally:
        await queue.put(None)




@router.get("/history/{session_id}")
async def get_chat_history(session_id: str, tenant_id: str,
                            current_user: AuthContext = Depends(get_current_user)):
    history = await firestore_service.get_conversation_history(session_id, tenant_id)
    if current_user.is_anonymous:
        history = [h for h in history if h.get("user_uid") == current_user.user_id]
        return {"tenant_id": tenant_id, "session_id": session_id, "history": history,
                "user": {"uid": current_user.user_id, "role": current_user.role,
                         "is_anonymous": True,
                         "note": "L'historique des visiteurs est supprimé après 24h d'inactivité"}}
    if current_user.role not in ("superadmin", "admin"):
        history = [h for h in history if h.get("user_uid") == current_user.user_id]
    return {"tenant_id": tenant_id, "session_id": session_id, "history": history,
            "user": {"uid": current_user.user_id, "role": current_user.role}}


@router.get("/activity/{event_id}")
async def get_activity_details(
    event_id: str,
    request: Request,
    current_user: AuthContext = Depends(get_current_user),
):
    from orchestrator.nodes.agents.activity import ActivityAgent
    from orchestrator.nodes.sales_optimizer import (
        _get_exchange_rates,
        _convert_amount,
        _needs_conversion,
    )
    from services.loyalty_service import LoyaltyService

    sub_category = request.query_params.get("sub_category", "")

    agent = ActivityAgent(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
    )
    agent.api_keys = await agent._load_api_keys(current_user.tenant_id)
    result = await agent.get_details(event_id, sub_category)

    if not result or not isinstance(result, dict):
        return result


    tenant_config = getattr(current_user, "tenant_config", {}) or {}
    target_currency = (tenant_config.get("currency") or "EUR").upper()
    margin          = float(tenant_config.get("margin_percentage") or 0)

    try:
        loyalty_summary  = getattr(current_user, "loyalty_summary", {}) or {}
        tier             = loyalty_summary.get("tier", "bronze")
        tier_config      = LoyaltyService.TIERS_CONFIG.get(tier, LoyaltyService.TIERS_CONFIG["bronze"])
        loyalty_discount = tier_config["discount"]
    except Exception:
        loyalty_discount = 0

    effective_margin = max(0.0, margin - loyalty_discount)

    try:
        rates = await _get_exchange_rates()
    except Exception as e:
        logger.warning(f"[ActivityDetails] Taux indisponibles: {e} → pas de conversion")
        return result

    price_min: float = 0.0
    price_max: float = 0.0
    source_currency: str = target_currency

    price_ranges = result.get("priceRanges", [])
    if price_ranges and isinstance(price_ranges, list):
        r               = price_ranges[0]
        price_min       = float(r.get("min") or 0)
        price_max       = float(r.get("max") or price_min)
        source_currency = (r.get("currency") or target_currency).upper()
    elif result.get("price_min") or result.get("price"):
        price_min       = float(result.get("price_min") or result.get("price") or 0)
        price_max       = float(result.get("price_max") or price_min)
        source_currency = (result.get("currency") or target_currency).upper()

    if price_min > 0:
        needs_conv = _needs_conversion(source_currency, target_currency)

        def _apply(amount: float) -> float:
            converted = (
                _convert_amount(amount, source_currency, target_currency, rates)
                if needs_conv else amount
            )
            return round(converted * (1 + effective_margin / 100), 2)

        result["price_min"]        = _apply(price_min)
        result["price_max"]        = _apply(price_max)
        result["price"]            = result["price_min"]
        result["currency"]         = target_currency
        result["display_currency"] = target_currency
        result["margin_applied"]   = effective_margin

        if needs_conv:
            result["original_currency"]  = source_currency
            result["price_min_original"] = price_min
            result["price_max_original"] = price_max

        logger.info(
            f"[ActivityDetails] {event_id} | "
            f"{price_min}-{price_max} {source_currency} → "
            f"{result['price_min']}-{result['price_max']} {target_currency} "
            f"(marge {effective_margin}%)"
        )

    return result


@router.get("/sessions")
async def get_user_sessions(tenant_id: str, current_user: AuthContext = Depends(get_current_user)):
    if current_user.is_anonymous:
        return {"sessions": []}
    sessions = await conversation_service.get_user_sessions(
        user_uid=current_user.user_id, tenant_id=tenant_id, user_role=current_user.role)
    return {"sessions": sessions}


@router.delete("/conversation/{session_id}")
async def delete_conversation(session_id: str, tenant_id: str,
                               current_user: AuthContext = Depends(get_current_user)):
    success = await conversation_service.delete_conversation(
        session_id=session_id, tenant_id=tenant_id,
        user_uid=current_user.user_id, user_role=current_user.role)
    if not success:
        raise HTTPException(status_code=403, detail="Non autorisé ou conversation introuvable")
    return {"message": "Conversation supprimée avec succès"}


@router.put("/conversation/{session_id}/rename")
async def rename_conversation(session_id: str, tenant_id: str, request: dict,
                               current_user: AuthContext = Depends(get_current_user)):
    new_title = request.get("title", "")
    if not new_title:
        raise HTTPException(status_code=400, detail="Le titre est requis")
    success = await conversation_service.rename_conversation(
        session_id=session_id, tenant_id=tenant_id, new_title=new_title,
        user_uid=current_user.user_id, user_role=current_user.role)
    if not success:
        raise HTTPException(status_code=403, detail="Non autorisé ou conversation introuvable")
    return {"message": "Conversation renommée avec succès"}