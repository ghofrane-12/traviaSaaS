# services/profile_memory.py

import logging
from typing import Optional
from uuid import UUID

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI

from core.firebase import db as firestore_db
from core.database import SessionLocal
from services.loyalty_service import LoyaltyService
from core.config import settings

logger = logging.getLogger("chat_logger")

PROFILES_COLLECTION = "user_profiles"

PROFILE_DEFAULT = {
    "preferred_cabin_class":  "",
    "preferred_seat":         "",
    "preferred_hotel_stars":  0,
    "preferred_room_type":    "",
    "favorite_destinations":  [],
    "usual_budget":           "",
    "travel_style":           "",
    "loyalty_tier":           "bronze",
    "loyalty_points":         0,
    "loyalty_total_spent":    0.0,
    "loyalty_benefits":       [],
    "loyalty_discount":       0,
    "conversation_summary":   "",
    "language":               "fr",
    "response_style":         "formal",
    "preferred_season":       "",   
    "travel_frequency":       "",  
}


class ProfileMemoryService:

    def __init__(self):
        self._loyalty_service = LoyaltyService()
        self._llm = ChatOpenAI(
            model="gpt-4o-mini",
            openai_api_key=settings.OPENAI_API_KEY,
            max_tokens=512,
        )

    # =========================================================================
    # CHARGEMENT DU PROFIL
    # =========================================================================

    async def load_profile(
        self,
        user_id:    str,
        tenant_id:  str,
        is_anonymous: bool = False,
        conversation_history: list = None,
    ) -> dict:
        if is_anonymous or not user_id or user_id in ("anonymous", "visiteur", ""):
            logger.info("[Profile] 👤 Visiteur anonyme — profil vide")
            return {**PROFILE_DEFAULT, "_is_anonymous": True}

        logger.info(f"[Profile] 🔍 Chargement profil | user={user_id} | tenant={tenant_id}")

        try:
            profile = await self._load_from_firestore(user_id, tenant_id)
            loyalty = await self._load_loyalty(user_id, tenant_id)
            if loyalty:
                profile.update(loyalty)

            if conversation_history:
                summary = await self._build_summary(
                    conversation_history, profile.get("conversation_summary", "")
                )
                if summary:
                    profile["conversation_summary"] = summary

            profile["_is_anonymous"] = False
            logger.info(
                f"[Profile] ✅ Profil chargé | tier={profile.get('loyalty_tier')} | "
                f"summary={'oui' if profile.get('conversation_summary') else 'non'}"
            )
            return profile

        except Exception as e:
            logger.error(f"[Profile] ❌ Erreur chargement profil : {e}")
            return {**PROFILE_DEFAULT, "_is_anonymous": False, "_error": str(e)}

    # =========================================================================
    # MISE À JOUR DU PROFIL
    # =========================================================================

    async def update_profile_after_conversation(
        self,
        user_id:   str,
        tenant_id: str,
        conversation_history: list,
        new_preferences: dict = None,
    ) -> None:
        if not user_id or user_id in ("anonymous", "visiteur", ""):
            return

        try:
            existing = await self._load_from_firestore(user_id, tenant_id)

            if conversation_history:
                summary = await self._build_summary(
                    conversation_history,
                    existing.get("conversation_summary", ""),
                )
                if summary:
                    existing["conversation_summary"] = summary

            if new_preferences:
                for key, value in new_preferences.items():
                    if key in PROFILE_DEFAULT and value:
                        existing[key] = value

            await self._save_to_firestore(user_id, tenant_id, existing)
            logger.info(f"[Profile] 💾 Profil mis à jour | user={user_id}")

        except Exception as e:
            logger.error(f"[Profile] ❌ Erreur mise à jour profil : {e}")

    # =========================================================================
    # GÉNÉRATION DU PROMPT PROFIL
    # =========================================================================

    def build_context_prompt(self, profile: dict) -> str:
        if profile.get("_is_anonymous"):
            return ""

        lines = []
        tier = profile.get("loyalty_tier", "bronze").capitalize()
        lines.append(f"[Profil client — {tier}]")

        points   = profile.get("loyalty_points", 0)
        discount = profile.get("loyalty_discount", 0)
        lines.append(f"Fidélité : {tier} · {points} pts · remise {discount}%")

        benefits = profile.get("loyalty_benefits", [])
        if benefits:
            lines.append(f"Avantages : {', '.join(benefits)}")

        prefs = []
        if profile.get("preferred_cabin_class"):
            prefs.append(f"classe {profile['preferred_cabin_class']}")
        if profile.get("preferred_seat"):
            prefs.append(f"siège {profile['preferred_seat']}")
        if profile.get("usual_budget"):
            prefs.append(f"budget ~{profile['usual_budget']}")
        if profile.get("travel_style"):
            prefs.append(f"voyage {profile['travel_style']}")
        if prefs:
            lines.append(f"Préférences : {', '.join(prefs)}")

        favs = profile.get("favorite_destinations", [])
        if favs:
            lines.append(f"Destinations favorites : {', '.join(favs[:5])}")

        if profile.get("conversation_summary"):
            lines.append(f"Historique : {profile['conversation_summary']}")

        if profile.get("preferred_season"):
            prefs.append(f"saison {profile['preferred_season']}")
        if profile.get("travel_frequency"):
            prefs.append(f"voyage {profile['travel_frequency']}")

        return "\n".join(lines)

    # =========================================================================
    # MÉTHODES PRIVÉES
    # =========================================================================

    async def _load_from_firestore(self, user_id: str, tenant_id: str) -> dict:
        try:
            doc_ref = (
                firestore_db
                .collection(PROFILES_COLLECTION)
                .document(tenant_id)
                .collection("users")
                .document(user_id)
            )
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                return {**PROFILE_DEFAULT, **data}
            else:
                logger.info(f"[Profile] 🆕 Nouveau profil pour user={user_id}")
                return PROFILE_DEFAULT.copy()
        except Exception as e:
            logger.error(f"[Profile] ❌ Firestore read error : {e}")
            return PROFILE_DEFAULT.copy()

    async def _save_to_firestore(self, user_id: str, tenant_id: str, profile: dict) -> None:
        try:
            clean = {k: v for k, v in profile.items() if not k.startswith("_")}
            doc_ref = (
                firestore_db
                .collection(PROFILES_COLLECTION)
                .document(tenant_id)
                .collection("users")
                .document(user_id)
            )
            doc_ref.set(clean, merge=True)
        except Exception as e:
            logger.error(f"[Profile] ❌ Firestore write error : {e}")

    
    async def _load_loyalty(self, user_id: str, tenant_id: str) -> Optional[dict]:
        try:
            from core.database import get_pool as _get_pool
            pool = await _get_pool()
            row = await pool.fetchrow(
                """
                SELECT points, tier, total_spent
                FROM tenant_loyalty
                WHERE tenant_id = $1 AND user_id = $2
                """,
                UUID(tenant_id), UUID(user_id)
            )
            if not row:
                return None

            tier_config = self._loyalty_service.get_tier_config(row["tier"])
            return {
                "loyalty_tier":        row["tier"],
                "loyalty_points":      row["points"],
                "loyalty_total_spent": float(row["total_spent"]),
                "loyalty_benefits":    tier_config.get("benefits", []),
                "loyalty_discount":    tier_config.get("discount", 0),
            }
        except Exception as e:
            logger.error(f"[Profile] ❌ Loyalty load error : {e}")
            return None
    async def _build_summary(
        self,
        conversation_history: list,
        existing_summary: str = "",
    ) -> str:
        """
        Résume l'historique via appel direct au LLM Anthropic.
        Sans dépendance à ConversationSummaryMemory (introuvable dans langchain_community).
        """
        if not conversation_history:
            return existing_summary

        try:
            recent = conversation_history[-20:]
            conv_text = ""
            for msg in recent:
                role    = msg.get("role", "")
                content = msg.get("content", "")
                if not content:
                    continue
                if role == "user":
                    conv_text += f"Client: {content}\n"
                elif role == "assistant":
                    conv_text += f"Assistant: {content}\n"

            if not conv_text.strip():
                return existing_summary

            prompt_parts = []
            if existing_summary:
                prompt_parts.append(f"Résumé précédent : {existing_summary}\n")
            prompt_parts.append(f"Nouvelle conversation :\n{conv_text}")
            prompt_parts.append(
                "\nRésume en 2-3 phrases les préférences et intentions de voyage "
                "du client (destinations, budget, style de voyage, demandes récurrentes, "
                "saison préférée si mentionnée, fréquence de voyage estimée). "
                "Sois concis et factuel."
            )

            messages  = [HumanMessage(content="\n".join(prompt_parts))]
            response  = await self._llm.ainvoke(messages)
            summary   = response.content.strip()

            logger.info(f"[Profile] 📝 Résumé généré : {summary[:80]}…")
            return summary

        except Exception as e:
            logger.warning(
                f"[Profile] ⚠️ Résumé échoué : {e} — résumé précédent conservé"
            )
            return existing_summary


# ── Instance globale  ─────────────────────────────────────────────
profile_memory_service = ProfileMemoryService()
