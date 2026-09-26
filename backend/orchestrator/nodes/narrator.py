from services.narrateur import narrate
from services.redis import (
    get_redis_client, append_history,
    save_graph_state,
)
from orchestrator.state import AgentState
from services.profile_memory import profile_memory_service

import json
import logging

logger = logging.getLogger("chat_logger")

async def narrator_node(state: AgentState) -> AgentState:
    print(f"[DEBUG SALES] results type={type(state.get('results'))}")
    logger.info("[NARRATOR_NODE] 🎭 Début")

    try:
        response = await narrate(state)
        cross_sell_proposals = []
        if isinstance(response, dict):
            cross_sell_proposals = response.get("cross_sell_proposals", [])
            if not cross_sell_proposals:
                final_raw = response.get("final_response", "")
                try:
                    parsed = json.loads(final_raw) if isinstance(final_raw, str) else final_raw
                    if isinstance(parsed, dict):
                        cross_sell_proposals = parsed.get("cross_sell_proposals", [])
                except Exception:
                    pass

        if state.get("waiting_for_form") or (
            isinstance(response, dict) and
            response.get("waiting_for_form")
        ):
            current_node = "waiting_form"
        elif state.get("clarification_pending"):
            current_node = "clarification"
        elif response:
            current_node = "done"
        else:
            current_node = "rejected"

        classification = state.get("classification", [])
        if classification:
            last_intent = [
                c.get("sub_category")
                for c in classification
                if c.get("sub_category")
            ]
        else:
            last_intent = None

        from services.redis import subcat_to_slot_key
        classification = state.get("classification", [])
        if classification:
            first_sub      = classification[0].get("sub_category", "")
            slots_category = subcat_to_slot_key(first_sub) if first_sub else "global"
        else:
            slots_category = "global"
        if not slots_category and last_intent:
            from services.redis import subcat_to_slot_key
            first_intent   = last_intent[0] if isinstance(last_intent, list) else last_intent
            slots_category = subcat_to_slot_key(first_intent)

        redis  = await get_redis_client()
        prefix = f"{state['tenant_id']}:{state['user_id']}:{state['session_id']}"

        if redis:
            has_management_question = any(
                r.get("status") == "need_more_info" and r.get("form_type") == "natural"
                for r in state.get("results", {}).values()
                if isinstance(r, dict)
            )

            if current_node == "waiting_form":
                waiting_for = "clarification" if has_management_question else "form_data"
                if has_management_question:
                    current_node = "waiting_clarification"
            elif current_node == "clarification":
                waiting_for = "clarification_choice"
            else:
                waiting_for = None

            save_graph_state(
                redis, prefix,
                current_node    = current_node,
                last_intent     = last_intent,
                waiting_for     = waiting_for,
                slots_category  = slots_category,
                ambiguous_queue = state.get("ambiguous_queue", []),
            )
            logger.info(
                f"[NARRATOR_NODE] 💾 State sauvegardé | "
                f"node={current_node} | intent={last_intent} | "
                f"waiting_for={waiting_for} | "
                f"slots_category={slots_category}"
            )

        final_raw = response.get("final_response", "") if isinstance(response, dict) else ""
        message   = ""
        try:
            parsed  = json.loads(final_raw) if isinstance(final_raw, str) else final_raw
            message = parsed.get("message", "")
        except Exception:
            pass

        if redis and message:
            append_history(
                redis, prefix,
                role     = "assistant",
                content  = message,
                msg_type = None,
                intent   = last_intent[0] if isinstance(last_intent, list) and last_intent else None,
            )

        if not state.get("is_anonymous", False) and current_node == "done":
            try:
                await profile_memory_service.update_profile_after_conversation(
                    user_id=state["user_id"],
                    tenant_id=state["tenant_id"],
                    conversation_history=state.get("context", {}).get("history", []),
                )
                logger.info("[NARRATOR_NODE] 👤 Profil mis à jour")
            except Exception as e:
                logger.warning(f"[NARRATOR_NODE] ⚠️ Mise à jour profil échouée (non bloquant) : {e}")

        logger.info(f"[NARRATOR_NODE] ✅ Réponse obtenue | node={current_node}")
        return {
            **state, 
            "final_response": response,
            "cross_sell_proposals": cross_sell_proposals  # ← À AJOUTER
        }

    except Exception as e:
        logger.error(f"[NARRATOR_NODE] ❌ Erreur: {e}", exc_info=True)
        return {**state, "final_response": "Désolé, une erreur est survenue.",
                "cross_sell_proposals": []
                }