# services/conversation_context.py
import logging
from typing import Optional

from core.firestore import firestore_service

logger = logging.getLogger("chat_logger")

HISTORY_LIMIT = 10


class ConversationContextService:
    """
    Service de chargement du contexte de conversation longue.

    Usage dans orchestrator_node.py :
        from services.conversation_context import conversation_context_service

        # Début de requête
        conv_context = await conversation_context_service.load_conversation_context(
            user_id=state["user_id"],
            tenant_id=state["tenant_id"],
            session_id=state["session_id"],
            is_anonymous=state.get("is_anonymous", False),
            loyalty_summary=state.get("loyalty_summary", {}),
        )
        state["context"]["conversation"] = conv_context
    """

    # =========================================================================
    # CHARGEMENT DU CONTEXTE
    # =========================================================================

    async def load_conversation_context(
        self,
        user_id:      str,
        tenant_id:    str,
        session_id:   str,
        is_anonymous: bool = False,
        loyalty_summary: dict = None,
    ) -> dict:
        """
        Point d'entrée principal.

        - Visiteur anonyme → contexte vide immédiatement
        - Authentifié      → charge historique Firestore + loyalty_summary
        """

        if is_anonymous or not user_id or user_id in ("anonymous", "visiteur", ""):
            logger.info("[ConvContext] 👤 Visiteur — contexte vide")
            return self._empty_context()

        logger.info(
            f"[ConvContext] 🔍 Chargement | user={user_id} | session={session_id}"
        )

        try:
            history = await self._load_history(session_id, tenant_id)
            loyalty = loyalty_summary or {}
            context = {
                "history":           history,
                "history_count":     len(history),
                "loyalty_summary":   loyalty,
                "has_history":       len(history) > 0,
                "_is_anonymous":     False,
            }

            logger.info(
                f"[ConvContext] ✅ Chargé | messages={len(history)} | "
                f"loyalty_tier={loyalty.get('tier', 'aucun')}"
            )
            return context

        except Exception as e:
            logger.error(f"[ConvContext] ❌ Erreur : {e}")
            return self._empty_context()


    def build_context_prompt(self, conv_context: dict) -> str:
        """
        Transforme le contexte de conversation en texte injecté
        dans le system prompt des agents (après le profile_prompt).

        Exemple de sortie :
            [Contexte conversation]
            Fidélité : Gold · remise 10% · 6486 pts
            Historique récent : 3 échanges
            Dernières demandes : vol Paris, hôtel Rome
        """
        if conv_context.get("_is_anonymous") or not conv_context.get("has_history"):
            return ""

        lines = ["[Contexte conversation]"]

        loyalty = conv_context.get("loyalty_summary", {})
        if loyalty:
            tier     = loyalty.get("tier", "").capitalize()
            discount = loyalty.get("discount", 0)
            points   = loyalty.get("points_after", loyalty.get("points_before", 0))
            if tier:
                lines.append(f"Fidélité : {tier} · remise {discount}% · {points} pts")
            benefits = loyalty.get("benefits", [])
            if benefits:
                lines.append(f"Avantages actifs : {', '.join(benefits)}")

        history = conv_context.get("history", [])
        if history:
            lines.append(f"Historique récent : {len(history)} échanges")

            recent_requests = []
            for msg in history[-6:]:  
                if msg.get("user_role") != "assistant":
                    user_msg = msg.get("user_message", "")
                    if user_msg and len(user_msg) > 5:
                        recent_requests.append(user_msg[:60])

            if recent_requests:
                lines.append(f"Dernières demandes : {' | '.join(recent_requests[-3:])}")

        return "\n".join(lines)

    # =========================================================================
    # MÉTHODES PRIVÉES
    # =========================================================================

    async def _load_history(
        self,
        session_id: str,
        tenant_id:  str,
    ) -> list:
        """
        Charge les derniers messages depuis Firestore.
        Retourne une liste de dicts avec user_message et assistant_response.
        """
        try:
            raw_history = await firestore_service.get_conversation_history(
                session_id=session_id,
                tenant_id=tenant_id,
                limit=HISTORY_LIMIT,
            )
            return raw_history or []
        except Exception as e:
            logger.error(f"[ConvContext] ❌ Firestore history error : {e}")
            return []

    def _empty_context(self) -> dict:
        return {
            "history":         [],
            "history_count":   0,
            "loyalty_summary": {},
            "has_history":     False,
            "_is_anonymous":   True,
        }


# ── Instance globale ─────────────────────────────────────────────
conversation_context_service = ConversationContextService()