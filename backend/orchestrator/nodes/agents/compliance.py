# compliance.py
import asyncio
import json
from core.config import settings
from orchestrator.rag.rag_engine import RAGEngine
import re
import httpx
SUBCAT_DISPATCH = {
    "Luggage & Safety":           "_luggage_safety",
    "Special Accessibility Needs":     "_special_accessibility_needs",
    "Vaccination & Health Requirements": "_vaccination_health",
    "Visa & Passport":             "_visa_passport",
}



class ComplianceAgent:

    def __init__(self, tenant_id: str = "", user_id: str = "", session_id: str = ""):
        self.tenant_id = tenant_id
        self.tenant_config = {}
        self.user_id = user_id
        self.session_id = session_id
        self.entities = {}
        self.language = ""
        self.sub_category = ""
        self.agent_type = "compliance"
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

    async def _resolve_rag_only(self, rag_query: str, steps: list, result_key: str = "compliance") -> dict:
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
            content = clean_lines[:16] if clean_lines else [rag_text[:600]]

            print(f"[ComplianceAgent] ✅ RAG hit → info_results")
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

        print("[ComplianceAgent] ⚠ RAG vide → fallback LLM")
        return await self._resolve_llm_only(rag_query, steps, result_key, "")

    async def _resolve_llm_only(self, rag_query: str, steps: list, result_key: str = "compliance", prompt_override: str = "") -> dict:
        """Résolution uniquement par LLM (Gemini)"""
        
        user_query = self.raw_text if hasattr(self, 'raw_text') else rag_query
        locs = self.entities.get("LOC", [])
        city = locs[0] if isinstance(locs, list) and locs else (locs if isinstance(locs, str) else "")
        tenant_currency = self.tenant_config.get("currency", "EUR")

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
            
            print(f"[ComplianceAgent] 🎯 Prompt personnalisé utilisé")
            
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
                print(f"[ComplianceAgent] ❌ Prompt personnalisé échoué: {e} → fallback système")
        
        print("[ComplianceAgent] 🔧 Prompt système utilisé")
        gemini_result = await self._gpt_fallback(rag_query)
        return {
            "status": "llm_only",
            "source": "GPT-4o-mini",
            **gemini_result,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "steps": steps,
        }

    async def _resolve_human_only(self, steps: list, result_key: str = "compliance") -> dict:
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
    # GEMINI FALLBACK
    # =========================================================================

    async def _gpt_fallback(self, query: str) -> dict:
        prompt = f"""Tu es un assistant voyage expert en conformité, réglementations et politiques de voyage. Réponds en JSON valide UNIQUEMENT.
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
            print(f"[ComplianceAgent] ❌ GPT: {e}")
            return {
                "type": "info_results",
                "message": "Information non disponible pour le moment.",
                "sections": [{"title": "Erreur temporaire", "content": "Veuillez réessayer.", "items": []}],
                "actions": []
            }

    # =========================================================================
    # MÉTHODES MÉTIERS
    # =========================================================================

    async def _luggage_safety(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        rag_query = (
            f"politiques bagages sécurité franchise cabine soute poids dimensions {' '.join(locs)} "
            f"excédent bagage frais supplémentaire objets interdits liquides règles compagnie "
            f"luggage policy baggage allowance cabin hold weight dimensions excess fees prohibited"
        )
        steps = [
            "Vérifiez la franchise bagage de votre compagnie aérienne avant l'embarquement",
            "Respectez les dimensions maximales pour les bagages cabine",
            "Pesez vos bagages à l'avance pour éviter les frais d'excédent",
            "Consultez la liste des objets interdits en cabine et en soute",
            "Étiquetez clairement vos bagages avec vos coordonnées",
        ]
        
        if self.response_type == "rag":
            return await self._resolve_rag_only(rag_query, steps, "luggage_safety")
        elif self.response_type == "llm":
            prompt_override = self.subcat_config.get("llm_prompt_override", "")
            return await self._resolve_llm_only(rag_query, steps, "luggage_safety", prompt_override)
        elif self.response_type == "human":
            return await self._resolve_human_only(steps, "luggage_safety")
        
        return await self._resolve_rag_only(rag_query, steps, "luggage_safety")

    async def _special_accessibility_needs(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        rag_query = (
            f"accessibilité mobilité réduite handicap assistance aéroport {' '.join(locs)} "
            f"fauteuil roulant assistance spéciale PMR animaux assistance voyage adapté "
            f"accessibility wheelchair special assistance disabled passengers reduced mobility PRM"
        )
        steps = [
            "Prévenez la compagnie aérienne de vos besoins spéciaux au moins 48h avant",
            "Demandez l'assistance PMR (Personnes à Mobilité Réduite) à la réservation",
            "Vérifiez les politiques de transport pour animaux d'assistance",
            "Renseignez-vous sur les équipements accessibles à l'aéroport de destination",
            "Conservez tous les justificatifs médicaux nécessaires pour les contrôles",
        ]
        
        if self.response_type == "rag":
            return await self._resolve_rag_only(rag_query, steps, "special_accessibility_needs")
        elif self.response_type == "llm":
            prompt_override = self.subcat_config.get("llm_prompt_override", "")
            return await self._resolve_llm_only(rag_query, steps, "special_accessibility_needs", prompt_override)
        elif self.response_type == "human":
            return await self._resolve_human_only(steps, "special_accessibility_needs")
        
        return await self._resolve_rag_only(rag_query, steps, "special_accessibility_needs")

    async def _vaccination_health(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        rag_query = (
            f"santé alimentation régime vaccins restrictions médicales voyage {' '.join(locs)} "
            f"halal casher végétarien allergies vaccinations obligatoires certificat médical "
            f"health dietary rules travel vaccinations required halal kosher vegan allergies medical"
        )
        steps = [
            "Vérifiez les vaccinations obligatoires ou recommandées pour la destination",
            "Signalez vos restrictions alimentaires à la compagnie aérienne à l'avance",
            "Emportez une ordonnance médicale traduite pour vos médicaments",
            "Renseignez-vous sur les règles d'importation de médicaments à destination",
            "Vérifiez la disponibilité de vos régimes spéciaux (halal, casher, végétarien) en vol",
        ]
        
        if self.response_type == "rag":
            return await self._resolve_rag_only(rag_query, steps, "vaccination_health")
        elif self.response_type == "llm":
            prompt_override = self.subcat_config.get("llm_prompt_override", "")
            return await self._resolve_llm_only(rag_query, steps, "vaccination_health", prompt_override)
        elif self.response_type == "human":
            return await self._resolve_human_only(steps, "vaccination_health")
        
        return await self._resolve_rag_only(rag_query, steps, "vaccination_health")

    async def _visa_passport(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        rag_query = (
            f"documents voyage passeport visa entrée exigences {' '.join(locs)} "
            f"validité passeport visa touristique transit ESTA ETA autorisation électronique "
            f"travel documents passport visa entry requirements validity tourist transit electronic"
        )
        steps = [
            "Vérifiez la validité de votre passeport (min. 6 mois après le retour)",
            "Renseignez-vous sur les exigences de visa pour votre nationalité",
            "Faites une demande de visa ou d'autorisation électronique à l'avance",
            "Préparez les documents d'entrée requis (assurance, réservations, justificatifs)",
            "Conservez des copies numériques de tous vos documents dans le cloud",
        ]
        
        if self.response_type == "rag":
            return await self._resolve_rag_only(rag_query, steps, "visa_passport")
        elif self.response_type == "llm":
            prompt_override = self.subcat_config.get("llm_prompt_override", "")
            return await self._resolve_llm_only(rag_query, steps, "visa_passport", prompt_override)
        elif self.response_type == "human":
            return await self._resolve_human_only(steps, "visa_passport")
        
        return await self._resolve_rag_only(rag_query, steps, "visa_passport")


# ─── Wrapper ─────────────────────────────────────────────────────────────────
async def compliance_agent(entities, language, tenant_config, sub_category=None,
                           tenant_id="", user_id="", session_id="",
                           response_type="rag", subcat_config=None,
                           raw_text="", **kwargs) -> dict:
    agent = ComplianceAgent(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
    return await agent.run(entities, language, tenant_config, sub_category=sub_category,
                           tenant_id=tenant_id, user_id=user_id, session_id=session_id,
                           response_type=response_type, subcat_config=subcat_config,
                           raw_text=raw_text, **kwargs)