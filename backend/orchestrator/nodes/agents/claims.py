# claims.py
import asyncio
import json
import httpx
import re
from core.config import settings
from orchestrator.rag.rag_engine import RAGEngine


SUBCAT_DISPATCH = {
    "Baggage Damage":                 "_baggage_damage",
    "Flight Disruption":                 "_flight_disruption",
    "Overbooking":                       "_overbooking",
    "Complaint Submission":              "_complaint_submission",
}



class ClaimsAgent:

    def __init__(self, tenant_id: str = "", user_id: str = "", session_id: str = ""):
        self.tenant_id = tenant_id
        self.tenant_config = {}
        self.user_id = user_id
        self.session_id = session_id
        self.entities = {}
        self.language = ""
        self.sub_category = ""
        self.agent_type = "claims"
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

    async def _resolve_rag_only(self, rag_query: str, steps: list, result_key: str = "claims") -> dict:
        """Résolution uniquement par RAG avec la nouvelle architecture"""
        
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
            content = clean_lines[:16] if clean_lines else [rag_text[:300]]

            print(f"[ClaimsAgent] ✅ RAG hit → info_results")
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

        print("[ClaimsAgent] ⚠ RAG vide → GPT")
        return await self._resolve_llm_only(rag_query, steps, result_key)

    async def _resolve_llm_only(self, rag_query: str, steps: list, result_key: str = "claims") -> dict:
        """Résolution uniquement par LLM (GPT)"""
        
        user_query = self.raw_text if hasattr(self, 'raw_text') else rag_query
        locs = self.entities.get("LOC", [])
        city = locs[0] if isinstance(locs, list) and locs else (locs if isinstance(locs, str) else "")
        tenant_currency = self.tenant_config.get("currency", "EUR")
        prompt_override = self.subcat_config.get("llm_prompt_override", "")

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
            
            print(f"[ClaimsAgent] 🎯 Prompt personnalisé utilisé")
            
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
                print(f"[ClaimsAgent] ❌ Prompt personnalisé échoué: {e} → fallback système")
        
        
        print("[ClaimsAgent] 🔧 Prompt système utilisé")
        gemini_result = await self._gpt_fallback(rag_query)
        return {
            "status": "llm_only",
            "source": "GPT-4o-mini",
            **gemini_result,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "steps": steps,
        }

    async def _resolve_human_only(self, steps: list, result_key: str = "claims") -> dict:
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
        prompt = f"""Tu es un assistant voyage expert en réclamations et remboursements. Réponds en JSON valide UNIQUEMENT.
    Format OBLIGATOIRE :
    {{
        "type": "info_results",
        "message": "Titre court et clair",
        "sections": [
            {{
                "title": "Titre de section",
                "content": "Explication en un paragraphe pas trop long",
                "items": ["étape 1", "étape 2", "étape 3"]
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
            print(f"[ClaimsAgent] ❌ GPT: {e}")
            return {
                "type": "info_results",
                "message": "Information non disponible pour le moment.",
                "sections": [{"title": "Erreur temporaire", "content": "Veuillez réessayer.", "items": []}],
                "actions": []
            }
    # =========================================================================
    # MÉTHODES MÉTIERS
    # =========================================================================

    async def _baggage_damage(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        rag_query = (
            f"bagage endommagé perdu volé réclamation compagnie aérienne {' '.join(locs)} "
            f"PIR property irregularity report dommage valise indemnisation "
            f"damaged lost baggage claim airline compensation reimbursement"
        )
        steps = [
            "Signalez immédiatement le dommage au comptoir bagage avant de quitter l'aéroport",
            "Remplissez un PIR (Property Irregularity Report) auprès de la compagnie aérienne",
            "Photographiez les dommages visibles sur le bagage et son contenu",
            "Conservez tous les reçus d'achats de première nécessité pour remboursement",
            "Déposez votre réclamation formelle dans les 7 jours (dommage) ou 21 jours (retard)",
        ]
        
        if self.response_type == "rag":
            return await self._resolve_rag_only(rag_query, steps, "baggage_damage")
        elif self.response_type == "llm":
            return await self._resolve_llm_only(rag_query, steps, "baggage_damage")
        elif self.response_type == "human":
            return await self._resolve_human_only(steps, "baggage_damage")
        
        return await self._resolve_rag_only(rag_query, steps, "baggage_damage")

    async def _flight_disruption(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        rag_query = (
            f"vol annulé retard surbooking perturbation compagnie aérienne {' '.join(locs)} "
            f"règlement UE 261/2004 compensation droits passagers "
            f"cancelled flight delay overbooking passenger rights compensation"
        )
        steps = [
            "Vérifiez vos droits selon le règlement UE 261/2004 (vols au départ/arrivée UE)",
            "Demandez immédiatement la prise en charge (repas, hébergement, transport)",
            "Conservez tous les justificatifs (carte d'embarquement, reçus, emails)",
            "Réclamez la compensation forfaitaire (250€ à 600€ selon distance)",
            "Si refus, saisissez le médiateur ou l'autorité de l'aviation civile",
        ]
        
        if self.response_type == "rag":
            return await self._resolve_rag_only(rag_query, steps, "flight_disruption")
        elif self.response_type == "llm":
            return await self._resolve_llm_only(rag_query, steps, "flight_disruption")
        elif self.response_type == "human":
            return await self._resolve_human_only(steps, "flight_disruption")
        
        return await self._resolve_rag_only(rag_query, steps, "flight_disruption")

    async def _overbooking(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        rag_query = (
            f"surbooking surréservation refus embarquement compagnie aérienne {' '.join(locs)} "
            f"règlement UE 261/2004 compensation volontaire involontaire "
            f"overbooking denied boarding voluntary involuntary compensation"
        )
        steps = [
            "Ne quittez pas la zone d'embarquement tant que le problème n'est pas résolu",
            "Demandez une compensation volontaire (en espèces) avant de libérer votre siège",
            "Si refus involontaire, exigez la compensation légale (250€ à 600€)",
            "Exigez un réacheminement ou remboursement + indemnisation",
            "Conservez une preuve écrite du refus d'embarquement",
        ]
        
        if self.response_type == "rag":
            return await self._resolve_rag_only(rag_query, steps, "overbooking")
        elif self.response_type == "llm":
            return await self._resolve_llm_only(rag_query, steps, "overbooking")
        elif self.response_type == "human":
            return await self._resolve_human_only(steps, "overbooking")
        
        return await self._resolve_rag_only(rag_query, steps, "overbooking")

    async def _complaint_submission(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        rag_query = (
            f"dépôt réclamation plainte service client compagnie aérienne hôtel {' '.join(locs)} "
            f"modèle lettre réclamation médiateur tourisme DGAC "
            f"complaint filing customer service letter template mediator"
        )
        steps = [
            "Rassemblez tous les documents justificatifs (billets, reçus, photos)",
            "Rédigez une réclamation claire et précise (date, lieu, problème, solution attendue)",
            "Envoyez la réclamation par lettre recommandée avec accusé de réception",
            "Conservez une copie de tous les échanges et courriers",
            "Saisissez le médiateur du tourisme en cas de refus ou silence prolongé",
        ]
        
        if self.response_type == "rag":
            return await self._resolve_rag_only(rag_query, steps, "complaint_submission")
        elif self.response_type == "llm":
            return await self._resolve_llm_only(rag_query, steps, "complaint_submission")
        elif self.response_type == "human":
            return await self._resolve_human_only(steps, "complaint_submission")
        
        return await self._resolve_rag_only(rag_query, steps, "complaint_submission")


# ─── Wrapper ─────────────────────────────────────────────────────────────────
async def claims_agent(entities, language, tenant_config, sub_category=None,
                       tenant_id="", user_id="", session_id="",
                       response_type="rag", subcat_config=None,
                       raw_text="", **kwargs) -> dict:
    agent = ClaimsAgent(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
    return await agent.run(entities, language, tenant_config, sub_category=sub_category,
                           tenant_id=tenant_id, user_id=user_id, session_id=session_id,
                           response_type=response_type, subcat_config=subcat_config,
                           raw_text=raw_text, **kwargs)