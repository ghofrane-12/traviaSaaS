# logistics.py
import asyncio
import json
from core.config import settings
from orchestrator.rag.rag_engine import RAGEngine
import re
import httpx


SUBCAT_DISPATCH = {
    "Late Check-Out": "_late_checkout",
    "Airport Logistics": "_airport_logistics",
    "Check-In": "_checkin",
}



class LogisticsAgent:

    def __init__(self, tenant_id: str = "", user_id: str = "", session_id: str = ""):
        self.tenant_id = tenant_id
        self.tenant_config = {}
        self.user_id = user_id
        self.session_id = session_id
        self.entities = {}
        self.language = ""
        self.sub_category = ""
        self.agent_type = "logistics"
        self.response_type = "rag" 
        self.subcat_config = {}
        self.raw_text = ""
        self.rag = RAGEngine(tenant_id, self.agent_type, user_id, session_id)

    # =========================================================================
    # POINT D'ENTRÉE
    # =========================================================================

    async def run(self, entities, language, tenant_config, sub_category=None,
                  tenant_id="", user_id="", session_id="", response_type="rag",
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

        method_name = SUBCAT_DISPATCH.get(sub_category)
        if not method_name and sub_category:
            subcat_only = sub_category.split(":")[-1].strip()
            method_name = next(
                (v for k, v in SUBCAT_DISPATCH.items() if k.endswith(subcat_only))            )
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
                    "max_tokens": 1000,
                    "temperature": 0,
                    "messages": [{"role": "user", "content": prompt}],
                }
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"] or ""
    # =========================================================================
    # MÉTHODES DE RÉSOLUTION
    # =========================================================================

    async def _resolve_rag_only(self, rag_query: str, steps: list, result_key: str = "logistics") -> dict:
        """Résolution uniquement par RAG avec la nouvelle architecture"""
        
        # ✅ Utiliser search_with_subcategory
        rag_result = await self.rag.search_with_subcategory(
            query=rag_query,
            sub_category=self.sub_category,
            k=5
        )

        if rag_result and rag_result.get("context"):
            rag_text = rag_result["context"]
            lines = rag_text.split('\n')
            clean_lines = []
            for line in lines:
                line = line.strip()
                if line and len(line) > 10:
                    line = re.sub(r'^[-•*]\s*', '• ', line)
                    clean_lines.append(line)
            content = clean_lines[:16] if clean_lines else [rag_text[:600]]

            print(f"[LogisticsAgent] ✅ RAG hit → info_results")
            return {
                "status": "rag_only",
                "source": "RAGEngine",
                "type": "info_results",
                "message": f"Informations : {result_key.replace('_', ' ').title()}",
                "sections": [{
                    "title": "Informations disponibles",
                    "content": "\n".join(content),
                    "items": steps,
                }],
                "actions": [{"label": "En savoir plus", "action": "explore"}],
                "rag_context": rag_text,
                "confidence": rag_result.get("confidence", 0),
                "sources": rag_result.get("sources", []),
                "user_id": self.user_id,
                "session_id": self.session_id,
                "steps": steps,
            }

        print("[LogisticsAgent] ⚠ RAG vide → fallback LLM")
        return await self._resolve_llm_only(rag_query, steps, result_key, "")

    async def _resolve_llm_only(self, rag_query: str, steps: list, result_key: str = "logistics", prompt_override: str = "") -> dict:
        """Résolution uniquement par LLM (Gemini)"""
        
        user_query = self.raw_text if hasattr(self, 'raw_text') else rag_query
        locs = self.entities.get("LOC", [])
        city = locs[0] if isinstance(locs, list) and locs else (locs if isinstance(locs, str) else "")
        tenant_currency = self.tenant_config.get("currency", "EUR")

        # ✅ Utiliser prompt_override si disponible
        if prompt_override:
            query = prompt_override
            replacements = {
                "{user_query}": user_query,
                "{language}": self.language or "fr",
                "{steps}": "\n".join(steps),
                "{city}":city,     
                "{currency}":tenant_currency,
            }
            for key, value in replacements.items():
                query = query.replace(key, value)
            
            print(f"[LogisticsAgent] 🎯 Prompt personnalisé utilisé")
            
            try:
                raw = await self._call_openai(query, timeout=60.0)
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                result = json.loads(raw.strip())

                return {
                    "status":   "llm_only",
                    "source":   "GPT-4o-mini Custom",
                    "type":     result.get("type", "info_results"),
                    "message":  result.get("message", f"Informations sur {result_key.replace('_', ' ').title()}"),
                    "sections": result.get("sections", [{"title": "Informations", "content": result.get("message", ""), "items": steps}]),
                    "actions":  result.get("actions", [{"label": "En savoir plus", "action": "explore"}]),
                    "user_id":    self.user_id,
                    "session_id": self.session_id,
                    "steps":      steps,
                }
            except Exception as e:
                print(f"[LogisticsAgent] ❌ Prompt personnalisé échoué: {e} → fallback système")
        
        # Fallback système
        print("[LogisticsAgent] 🔧 Prompt système utilisé")
        gpt_result = await self._gpt_fallback(rag_query)
        return {
            "status": "llm_only",
            "source": "GPT-4o-mini",
            **gpt_result,
            "user_id":    self.user_id,
            "session_id": self.session_id,
            "steps":      steps,
        }

    async def _resolve_human_only(self, steps: list, result_key: str = "logistics") -> dict:
        contact = self.subcat_config.get("human_contact", "")
        lang    = self.language or "query_fr"

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
        contacts_fallback = {
            "query_fr":      "📧 Contactez votre agence",
            "query_ar":      "📧 تواصل مع الوكالة",
            "query_derja_a": "📧 تواصل مع الوكالة",
            "query_derja_l": "📧 Contactez l'agence",
            "query_en":      "📧 Contact your agency",
        }

        return {
            "status":   "human_required",
            "type":     "info_results",
            "message":  messages.get(lang, messages["query_fr"]),
            "sections": [{
                "title":   titles.get(lang, titles["query_fr"]),
                "content": f"Pour {result_key.replace('_', ' ').title()}, veuillez contacter notre équipe.",
                "items":   [f"📞 {contact}" if contact else contacts_fallback.get(lang, contacts_fallback["query_fr"])] + steps
            }],
            "actions":    [{"label": "Contacter", "action": "contact", "contact": contact}],
            "user_id":    self.user_id,
            "session_id": self.session_id,
            "steps":      steps,
        }
    # =========================================================================
    # GPT FALLBACK
    # =========================================================================

    async def _gpt_fallback(self, query: str) -> dict:
        lang = self.language if hasattr(self, 'language') and self.language else "fr"
        prompt = f"""Tu es un assistant voyage expert en logistique aéroportuaire et procédures d'embarquement. Réponds en JSON valide UNIQUEMENT.
    Langue de réponse : {lang}
    Format OBLIGATOIRE :
    {{
        "type": "info_results",
        "message": "Titre court et clair",
        "sections": [
            {{
                "title": "Titre de section",
                "content": "Explication en 2-3 phrases",
                "items": ["point 1", "point 2", "point 3"]
            }}
        ],
        "actions": [{{"label": "En savoir plus", "action": "explore"}}]
    }}
    Génère 3 à 4 sections pertinentes. Requête : {query}"""

        try:
            raw = await self._call_openai(prompt, timeout=60.0)
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = json.loads(raw.strip())
            result["type"] = "info_results"
            result.setdefault("sections", [])
            result.setdefault("actions", [{"label": "En savoir plus", "action": "explore"}])
            return result
        except Exception as e:
            print(f"[LogisticsAgent] ❌ GPT: {e}")
            return {
                "type": "info_results",
                "message": "Information non disponible pour le moment.",
                "sections": [{"title": "Erreur temporaire", "content": "Veuillez réessayer.", "items": []}],
                "actions": []
            }

    # =========================================================================
    # MÉTHODES MÉTIERS
    # =========================================================================

    async def _airport_logistics(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        rag_query = (
            f"logistique aéroport transfert navette transport {' '.join(locs)} "
            f"taxi bus train aéroport terminal correspondance transit "
            f"airport transfer shuttle taxi bus train terminal connection transit"
        )
        steps = [
            "Vérifiez les options de transport entre l'aéroport et votre destination",
            "Réservez votre transfert à l'avance pour éviter les files d'attente",
            "Prévoyez un temps de trajet suffisant selon le trafic local",
            "Conservez le numéro de votre chauffeur ou le contact de la navette",
            "En cas de correspondance, vérifiez le terminal de départ du vol suivant",
        ]
        
        if self.response_type == "rag":
            return await self._resolve_rag_only(rag_query, steps, "airport_logistics")
        elif self.response_type == "llm":
            prompt_override = self.subcat_config.get("llm_prompt_override", "")
            return await self._resolve_llm_only(rag_query, steps, "airport_logistics", prompt_override)
        elif self.response_type == "human":
            return await self._resolve_human_only(steps, "airport_logistics")
        
        return await self._resolve_rag_only(rag_query, steps, "airport_logistics")

    async def _checkin(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        rag_query = (
            f"enregistrement embarquement procédures aéroport {' '.join(locs)} "
            f"check-in online bornes bagage cabine carte embarquement porte gate "
            f"check-in boarding pass gate luggage drop online mobile boarding"
        )
        steps = [
            "Effectuez l'enregistrement en ligne 24-48h avant le départ",
            "Arrivez au comptoir bagage au moins 2h avant pour les vols internationaux",
            "Téléchargez votre carte d'embarquement sur mobile ou imprimez-la",
            "Vérifiez le numéro de porte et les éventuels changements sur les écrans",
            "Présentez-vous à la porte d'embarquement au moins 45 minutes avant le départ",
        ]
        
        if self.response_type == "rag":
            return await self._resolve_rag_only(rag_query, steps, "checkin")
        elif self.response_type == "llm":
            prompt_override = self.subcat_config.get("llm_prompt_override", "")
            return await self._resolve_llm_only(rag_query, steps, "checkin", prompt_override)
        elif self.response_type == "human":
            return await self._resolve_human_only(steps, "checkin")
        
        return await self._resolve_rag_only(rag_query, steps, "checkin")

    async def _late_checkout(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        rag_query = (
            f"late checkout départ tardif hôtel prolongement séjour {' '.join(locs)} "
            f"check-out tardif supplément bagages consigne frais supplémentaires "
            f"late checkout hotel late departure luggage storage fees"
        )
        steps = [
            "Demandez le late checkout à la réception au moins 24h à l'avance",
            "Vérifiez les frais supplémentaires (souvent 50% du prix pour 4h)",
            "Si refus, renseignez-vous sur la consigne à bagages de l'hôtel",
            "Proposez de libérer la chambre à l'heure normale avec consigne gratuite",
            "Négociez un départ tardif en basse saison ou en cas de statut fidélité",
        ]
        
        if self.response_type == "rag":
            return await self._resolve_rag_only(rag_query, steps, "late_checkout")
        elif self.response_type == "llm":
            prompt_override = self.subcat_config.get("llm_prompt_override", "")
            return await self._resolve_llm_only(rag_query, steps, "late_checkout", prompt_override)
        elif self.response_type == "human":
            return await self._resolve_human_only(steps, "late_checkout")
        
        return await self._resolve_rag_only(rag_query, steps, "late_checkout")


# ─── Wrapper ─────────────────────────────────────────────────────────────────
async def logistics_agent(entities, language, tenant_config, sub_category=None,
                          tenant_id="", user_id="", session_id="",
                          response_type="rag", subcat_config=None,
                          raw_text="", **kwargs) -> dict:
    agent = LogisticsAgent(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
    return await agent.run(entities, language, tenant_config, sub_category=sub_category,
                           tenant_id=tenant_id, user_id=user_id, session_id=session_id,
                           response_type=response_type, subcat_config=subcat_config,
                           raw_text=raw_text, **kwargs)