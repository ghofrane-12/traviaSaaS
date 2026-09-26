# orchestrator/nodes/agents/general_agent.py
import uuid
import json
import hashlib
import logging
import asyncio
import time as _time
from datetime import datetime
from typing import Any

from core.config import settings
from core.database import get_pool
from openai import AsyncOpenAI
from orchestrator.rag.rag_engine import RAGEngine

_llm_cache: dict[str, tuple] = {}
_CACHE_TTL = 600  

logger = logging.getLogger("chat_logger")

# =============================================================================
# DISPATCH
# =============================================================================

SUBCAT_DISPATCH = {
    "Agency Details & Contact": "_agency_details",
    "Chatbot Capabilities":     "_chatbot_capabilities",
    "Human Handoff":            "_human_handoff",
    "Feedback & Reviews":       "_feedback_reviews",
}

# =============================================================================
# AGENT
# =============================================================================

class GeneralAgent:
    def __init__(self, tenant_id: str = "", user_id: str = "", session_id: str = ""):
        self.tenant_id    = tenant_id
        self.user_id      = user_id
        self.session_id   = session_id
        self.tenant_config = {}
        self.sub_category  = ""
        self.response_type = "rag"
        self.subcat_config = {}
        self.raw_text      = ""
        self.language      = "query_fr"
        self.agent_type    = "general"
        self.rag_engine    = None

    # =========================================================================
    # POINT D'ENTRÉE
    # =========================================================================

    async def run(
        self,
        entities: dict,
        language: str,
        tenant_config: dict,
        sub_category: str = "",
        tenant_id: str = "",
        user_id: str = "",
        session_id: str = "",
        response_type: str = "rag",
        subcat_config: dict = None,
        raw_text: str = "",
        **kwargs,
    ) -> dict:
        self.tenant_id     = tenant_id or self.tenant_id
        self.user_id       = user_id   or self.user_id
        self.session_id    = session_id or self.session_id
        self.tenant_config = tenant_config
        self.sub_category  = sub_category or ""
        self.response_type = response_type
        self.subcat_config = subcat_config or {}
        self.raw_text      = raw_text
        self.language      = language
        self.rag_engine    = RAGEngine(self.tenant_id, self.agent_type, self.user_id, self.session_id)

        method_name = SUBCAT_DISPATCH.get(sub_category)
        if not method_name:
            logger.warning(f"[GeneralAgent] ⚠ Sous-catégorie inconnue: '{sub_category}'")
            return self._unknown_subcategory(sub_category)

        method = getattr(self, method_name)
        return await method(entities)

    # =========================================================================
    # AGENCY DETAILS & CONTACT
    # =========================================================================

    async def _agency_details(self, entities: dict) -> dict:
        query = self.raw_text or "informations agence contact horaires adresse"

        try:
            rag_result = await self.rag_engine.search_with_subcategory(
                query=query,
                sub_category=self.sub_category,
                k=8,
            )

            if rag_result and rag_result.get("context"):
                context = rag_result["context"]
                sections = await self._gpt_format_agency_info(context)
                return {
                    "status":   "rag_only",
                    "type":     "info_results",
                    "message":  "",
                    "sections": sections,
                    "actions":  [{"label": "Nous contacter", "action": "contact"}],
                }

        except Exception as e:
            logger.error(f"[GeneralAgent] ❌ RAG agency_details: {e}")

        return {
            "status":  "rag_only",
            "type":    "info_results",
            "message": "Nous n'avons pas trouvé d'informations sur l'agence.",
            "sections": [],
            "actions":  [{"label": "Nous contacter", "action": "contact"}],
        }


    async def _gpt_format_agency_info(self, context: str) -> list:
        lang_map = {
            "query_fr":      "français",
            "query_ar":      "arabe",
            "query_en":      "anglais",
            "query_derja_l": "arabe dialectal tunisien",
            "query_derja_a": "arabe dialectal tunisien",
        }
        target_lang = lang_map.get(self.language, "français")

        prompt = f"""Tu es un assistant d'agence de voyage. Voici les informations brutes de l'agence extraites d'une base de données :

    {context[:2000]}

    MISSION : Reformate ces informations en sections claires et lisibles pour le client.
    Langue de réponse OBLIGATOIRE : {target_lang}

    Retourne UNIQUEMENT ce JSON (sans backticks) :
    [
    {{
        "title": "titre de la section",
        "content": "phrase introductive courte",
        "items": ["item1", "item2", "item3"]
    }}
    ]

    RÈGLES :
    - Maximum 3 sections pertinentes
    - Sections possibles : Coordonnées, Horaires, Réseaux sociaux, Informations légales
    - Items courts et lisibles (max 60 chars)
    - N'invente rien — utilise uniquement les données présentes
    - Langue : {target_lang} UNIQUEMENT
    """

        try:
            raw = await self._call_openai(prompt)
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()
            sections = json.loads(raw)
            if isinstance(sections, list):
                return sections
        except Exception as e:
            logger.error(f"[GeneralAgent] ❌ GPT format agency: {e}")

        # Fallback — parse simple
        return self._parse_rag_to_sections(context, "agency")
    # =========================================================================
    # CHATBOT CAPABILITIES
    # =========================================================================

    async def _chatbot_capabilities(self, entities: dict) -> dict:
        query = self.raw_text or "capacités fonctionnalités assistant chatbot"

        try:
            rag_result = await self.rag_engine.search_with_subcategory(
                query=query,
                sub_category=self.sub_category,
                k=8,
            )

            if rag_result and rag_result.get("context"):
                context  = rag_result["context"]
                sections = await self._gpt_format_agency_info(context)
                return {
                    "status":   "rag_only",
                    "type":     "info_results",
                    "message":  "",
                    "sections": sections,
                    "actions":  [],
                }

        except Exception as e:
            logger.error(f"[GeneralAgent] ❌ RAG chatbot_capabilities: {e}")

        return {
            "status":  "rag_only",
            "type":    "info_results",
            "message": "Je peux vous aider à rechercher des vols, hôtels, restaurants, activités et bien plus encore.",
            "sections": [{
                "title":   "Ce que je peux faire",
                "content": "Je suis votre assistant voyage intelligent.",
                "items": [
                    "🔍 Recherche de vols, hôtels, restaurants, activités",
                    "📋 Gestion de vos réservations",
                    "ℹ️ Informations sur les destinations",
                    "🎭 Recommandations personnalisées",
                    "👤 Mise en relation avec un conseiller",
                ]
            }],
            "actions": [],
        }
    # =========================================================================
    # HUMAN HANDOFF
    # =========================================================================

    async def _human_handoff(self, entities: dict) -> dict:
        contact_raw = self.subcat_config.get("human_contact", "") or ""

        phone = ""
        email = ""
        parts = [p.strip() for p in contact_raw.split("/")]
        for part in parts:
            if "@" in part:
                email = part
            elif part:
                phone = part

        if not phone and not email:
            return {
                "status":  "human_required",
                "type":    "info_results",
                "message": "Le service de contact n'est pas disponible pour le moment.",
                "sections": [],
                "actions":  [],
                "show_ticket_form": False,
                "contact": {},
            }

        items = []
        if phone: items.append(f"📞 {phone}")
        if email: items.append(f"📧 {email}")

        return {
            "status":  "human_required",
            "type":    "info_results",
            "message": "Un conseiller est disponible pour vous aider.",
            "sections": [{
                "title":   "Contacter un conseiller",
                "content": "Notre équipe est à votre disposition.",
                "items":   items,
            }],
            "actions": [
                {"label": "Envoyer un message", "action": "open_ticket_form"},
                {"label": "Appeler", "action": "call", "contact": phone},
            ],
            "show_ticket_form": True,
            "contact": {"phone": phone, "email": email},
        }

    # =========================================================================
    # FEEDBACK & REVIEWS
    # =========================================================================

    async def _feedback_reviews(self, entities: dict) -> dict:
        """
        1. Lit tous les avis depuis DB
        2. GPT-4o-mini filtre/résume selon la requête user
        3. Retourne info_results avec avis pertinents
        Frontend affiche bouton 'Laisser un avis' → formulaire direct.
        """
        reviews = await self._get_reviews(limit=50)

        if not reviews:
            return {
                "status":  "rag_only",
                "type":    "info_results",
                "message": "Soyez le premier à laisser un avis !",
                "sections": [],
                "actions":  [{"label": "Laisser un avis", "action": "open_review_form"}],
                "show_review_form": True,
                "reviews":          [],
            }

        avg_rating  = sum(r["rating"] for r in reviews) / len(reviews)
        avg_stars   = "⭐" * round(avg_rating)
        user_query  = self.raw_text or "avis clients"
        language    = {
            "query_fr": "français", "query_en": "anglais",
            "query_ar": "arabe",    "query_derja_l": "darija tunisienne",
        }.get(self.language, "français")

        reviews_text = "\n".join([
            f"[{i+1}] Note: {r['rating']}/5 | Date: {r.get('created_at', '')} | Commentaire: {r.get('comment', '').strip()}"
            for i, r in enumerate(reviews)
            if r.get("comment", "").strip()
        ])

        if not reviews_text:
            return self._build_stats_only_result(reviews, avg_rating, avg_stars)

        prompt = f"""Tu es un assistant qui analyse des avis clients pour une agence de voyage.

REQUÊTE DE L'UTILISATEUR: "{user_query}"

AVIS DISPONIBLES:
{reviews_text}

MISSION:
1. Sélectionne les avis les plus pertinents par rapport à la requête
2. Si la requête est générale ("avis", "commentaires"), retourne les 5 meilleurs avis
3. Si la requête est spécifique ("avis hôtels", "avis vols"), filtre par thème
4. Résume brièvement chaque avis sélectionné en 1 phrase naturelle
5. Langue de réponse: {language}

Retourne UNIQUEMENT ce JSON (sans backticks):
{{
  "summary": "phrase de synthèse globale en 1-2 phrases",
  "selected_reviews": [
    {{
      "index": 1,
      "rating": 5,
      "summary": "résumé naturel de l'avis en 1 phrase",
      "original": "commentaire original court"
    }}
  ],
  "theme": "thème détecté (général / hôtels / vols / restaurants / activités)"
}}

JSON:"""

        parsed    = None
        cache_key = hashlib.sha256(
            f"{user_query}_{len(reviews)}_{reviews_text[:200]}".encode()
        ).hexdigest()[:20]

        cached = _llm_cache.get(cache_key)
        if cached:
            text, ts = cached
            if _time.time() - ts < _CACHE_TTL:
                logger.info("[GeneralAgent] ✅ Cache reviews hit")
                try:
                    parsed = json.loads(text)
                except Exception:
                    pass

        if not parsed:
            try:
                raw = await self._call_openai(prompt)
                if raw:
                    _llm_cache[cache_key] = (raw, _time.time())
                    parsed = json.loads(raw)
            except Exception as e:
                logger.error(f"[GeneralAgent] ❌ GPT reviews: {e}")

        if parsed:
            summary          = parsed.get("summary", "")
            selected_reviews = parsed.get("selected_reviews", [])

            items = [
                f"{'⭐' * r['rating']} — {r['summary']}"
                for r in selected_reviews
            ]

            sections = [{
                "title":   f"Avis clients — {avg_stars} ({avg_rating:.1f}/5 — {len(reviews)} avis)",
                "content": summary,
                "items":   items,
            }]
        else:
            sections = self._build_fallback_sections(reviews, avg_rating, avg_stars)

        return {
            "status":       "rag_only",
            "type":         "info_results",
            "message":      "",
            "sections":     sections,
            "actions":      [{"label": "Laisser un avis", "action": "open_review_form"}],
            "show_review_form": True,
            "reviews":      reviews,
            "avg_rating":   round(avg_rating, 1),
        }

    def _build_stats_only_result(
        self, reviews: list, avg_rating: float, avg_stars: str
    ) -> dict:
        """Retourne stats sans commentaires."""
        return {
            "status":  "rag_only",
            "type":    "info_results",
            "message": "",
            "sections": [{
                "title":   f"Avis clients — {avg_stars} ({avg_rating:.1f}/5)",
                "content": f"{len(reviews)} clients ont noté notre agence.",
                "items":   [],
            }],
            "actions":          [{"label": "Laisser un avis", "action": "open_review_form"}],
            "show_review_form": True,
            "reviews":          reviews,
            "avg_rating":       round(avg_rating, 1),
        }

    def _build_fallback_sections(
        self, reviews: list, avg_rating: float, avg_stars: str
    ) -> list:
        """Fallback sans LLM : affiche les 5 derniers avis bruts."""
        items = []
        for r in reviews[:5]:
            rating_stars = "⭐" * r["rating"]
            date_str     = r.get("created_at", "")
            if hasattr(date_str, "strftime"):
                date_str = date_str.strftime("%d/%m/%Y")
            comment = r.get("comment", "").strip()
            if comment:
                items.append(f"{rating_stars} — {comment} ({date_str})")

        return [{
            "title":   f"Avis clients — {avg_stars} ({avg_rating:.1f}/5 — {len(reviews)} avis)",
            "content": f"{len(reviews)} avis clients",
            "items":   items,
        }]

    # =========================================================================
    # DB HELPERS
    # =========================================================================

    async def _get_reviews(self, limit: int = 10) -> list:
        """Récupère les avis depuis tenant_reviews."""
        try:
            pool = await get_pool()
            rows = await pool.fetch(
                """
                SELECT review_id, user_id, rating, comment, created_at
                FROM tenant_reviews
                WHERE tenant_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                uuid.UUID(self.tenant_id),
                limit,
            )
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[GeneralAgent] ❌ get_reviews: {e}")
            return []

    # =========================================================================
    # OPENAI HELPER
    # =========================================================================

    async def _call_openai(self, prompt: str, timeout: float = 60.0) -> str:
        """Appel GPT-4o-mini async."""
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=1000,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=timeout,
        )
        raw = response.choices[0].message.content or ""
        for fence in ["```json", "```"]:
            raw = raw.replace(fence, "")
        return raw.strip()

    # =========================================================================
    # RAG PARSER
    # =========================================================================

    def _parse_rag_to_sections(self, context: str, context_type: str) -> list:
        """
        Transforme le texte RAG brut en sections structurées.
        Découpe par blocs de titre si présents, sinon un seul bloc.
        """
        if not context:
            return []

        lines   = context.strip().split("\n")
        sections = []
        current_title   = "Informations"
        current_items   = []
        current_content = ""

        for line in lines:
            line = line.strip()
            if not line:
                continue
            if len(line) < 60 and line.endswith(":"):
                if current_items or current_content:
                    sections.append({
                        "title":   current_title,
                        "content": current_content,
                        "items":   current_items,
                    })
                current_title   = line.rstrip(":")
                current_items   = []
                current_content = ""
            elif line.startswith(("-", "•", "*", "–")):
                current_items.append(line.lstrip("-•*– ").strip())
            else:
                if current_content:
                    current_content += " " + line
                else:
                    current_content = line

        if current_items or current_content:
            sections.append({
                "title":   current_title,
                "content": current_content,
                "items":   current_items,
            })

        return sections if sections else [{
            "title":   "Informations",
            "content": context[:500],
            "items":   [],
        }]

    def _unknown_subcategory(self, sub_category: str) -> dict:
        return {
            "status":  "api_error",
            "type":    "info_results",
            "message": f"La fonctionnalité '{sub_category}' n'est pas disponible.",
            "sections": [],
            "actions":  [],
        }


# =============================================================================
# POINT D'ENTRÉE MODULE
# =============================================================================

async def general_agent(
    entities: dict,
    language: str,
    tenant_config: dict,
    sub_category: str = "",
    tenant_id: str = "",
    user_id: str = "",
    session_id: str = "",
    response_type: str = "rag",
    subcat_config: dict = None,
    raw_text: str = "",
    **kwargs,
) -> dict:
    agent = GeneralAgent(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
    return await agent.run(
        entities=entities,
        language=language,
        tenant_config=tenant_config,
        sub_category=sub_category,
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        response_type=response_type,
        subcat_config=subcat_config,
        raw_text=raw_text,
        **kwargs,
    )