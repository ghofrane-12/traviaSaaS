# info.py
import asyncio
import json
from core.config import settings
from orchestrator.rag.rag_engine import RAGEngine
import re
import httpx

SUBCAT_DISPATCH = {
    "Weather & Best Seasons": "_weather_best_seasons",
    "Internet & Connectivity": "_internet_connectivity",
    "Cultural Norms": "_cultural_norms",
}

class InfoAgent:

    def __init__(self, tenant_id: str = "", user_id: str = "", session_id: str = ""):
        self.tenant_id = tenant_id
        self.tenant_config = {}
        self.user_id = user_id
        self.session_id = session_id
        self.entities = {}
        self.language = ""
        self.sub_category = ""
        self.agent_type = "info"
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

    async def _resolve_rag_only(self, rag_query: str, steps: list, result_key: str = "info") -> dict:
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

            print(f"[InfoAgent] ✅ RAG hit → info_results")
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

        print("[InfoAgent] ⚠ RAG vide → fallback LLM")
        return await self._resolve_llm_only(rag_query, steps, result_key, "")

    async def _resolve_llm_only(self, rag_query: str, steps: list, result_key: str = "info", prompt_override: str = "") -> dict:
        """Résolution uniquement par LLM (GPT)"""
        
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
            
            print(f"[InfoAgent] 🎯 Prompt personnalisé utilisé")
            
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
                print(f"[InfoAgent] ❌ Prompt personnalisé échoué: {e} → fallback système")
        
        print("[InfoAgent] 🔧 Prompt système utilisé")
        gpt_result = await self._gpt_fallback(rag_query)
        return {
            "status": "llm_only",
            "source": "GPT-4o-mini",
            **gpt_result,
            "user_id":    self.user_id,
            "session_id": self.session_id,
            "steps":      steps,
        }

    async def _resolve_human_only(self, steps: list, result_key: str = "info") -> dict:
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
    #   GPT FALLBACK
    # =========================================================================
    async def _gpt_fallback(self, query: str) -> dict:
        lang = self.language if hasattr(self, 'language') and self.language else "fr"
        prompt = f"""Tu es un assistant voyage expert. Réponds en JSON valide UNIQUEMENT.
    Langue de réponse : {lang}
    Format OBLIGATOIRE:
    {{
        "type": "info_results",
        "message": "Titre court et accrocheur",
        "sections": [
            {{
                "title": "Titre de section",
                "content": "Explication 2-3 phrases",
                "items": ["point 1", "point 2", "point 3"]
            }}
        ],
        "actions": [{{"label": "En savoir plus", "action": "explore"}}]
    }}
    Génère 3-4 sections pertinentes. Requête: {query}"""

        try:
            raw = await self._call_openai(prompt, timeout=60.0)
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = json.loads(raw.strip())
            result["type"] = "info_results"
            result.setdefault("sections", [])
            result.setdefault("actions", [])
            return result
        except Exception as e:
            print(f"[InfoAgent] ❌ GPT: {e}")
            return {
                "type": "info_results",
                "message": "Information non disponible pour le moment.",
                "sections": [{"title": "Erreur", "content": "Impossible de récupérer les informations.", "items": []}],
                "actions": []
            }
    # =========================================================================
    # MÉTHODES MÉTIERS
    # =========================================================================

    async def _weather_best_seasons(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        rag_query = (
            f"meilleures saisons voyage climat affluence {' '.join(locs)} "
            f"climat idéal meilleures périodes météo tourisme"
        )
        steps = [
            "Consultez les saisons recommandées pour chaque destination",
            "Comparez climat, températures et précipitations",
            "Évitez les périodes de forte affluence touristique",
            "Planifiez votre voyage en fonction de la météo et des festivals locaux",
        ]
        
        if self.response_type == "rag":
            return await self._resolve_rag_only(rag_query, steps, "weather_best_seasons")
        elif self.response_type == "llm":
            prompt_override = self.subcat_config.get("llm_prompt_override", "")
            return await self._resolve_llm_only(rag_query, steps, "weather_best_seasons", prompt_override)
        elif self.response_type == "human":
            return await self._resolve_human_only(steps, "weather_best_seasons")
        
        return await self._resolve_rag_only(rag_query, steps, "weather_best_seasons")

    async def _internet_connectivity(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        rag_query = (
            f"internet connectivité wifi roaming réseaux mobiles {' '.join(locs)} "
            f"e-sim carte sim prépayée connexion voyage"
        )
        steps = [
            "Vérifiez la couverture réseau mobile de votre opérateur",
            "Envisagez une e-sim ou carte SIM locale pour des tarifs avantageux",
            "Téléchargez les cartes hors-ligne avant le départ",
            "Identifiez les zones avec wifi gratuit (aéroports, cafés, hôtels)",
        ]
        
        if self.response_type == "rag":
            return await self._resolve_rag_only(rag_query, steps, "internet_connectivity")
        elif self.response_type == "llm":
            prompt_override = self.subcat_config.get("llm_prompt_override", "")
            return await self._resolve_llm_only(rag_query, steps, "internet_connectivity", prompt_override)
        elif self.response_type == "human":
            return await self._resolve_human_only(steps, "internet_connectivity")
        
        return await self._resolve_rag_only(rag_query, steps, "internet_connectivity")

    async def _cultural_norms(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        rag_query = (
            f"normes sociales politesse étiquette comportements {' '.join(locs)} "
            f"coutumes locales bonnes manières traditions comportement respect"
        )
        steps = [
            "Respectez les règles de politesse locales",
            "Adaptez votre comportement en public et en privé",
            "Informez-vous sur les gestes ou comportements interdits",
            "Soyez attentif aux différences culturelles et religieuses",
        ]
        
        if self.response_type == "rag":
            return await self._resolve_rag_only(rag_query, steps, "cultural_norms")
        elif self.response_type == "llm":
            prompt_override = self.subcat_config.get("llm_prompt_override", "")
            return await self._resolve_llm_only(rag_query, steps, "cultural_norms", prompt_override)
        elif self.response_type == "human":
            return await self._resolve_human_only(steps, "cultural_norms")
        
        return await self._resolve_rag_only(rag_query, steps, "cultural_norms")



# ─── Wrapper ─────────────────────────────────────────────────────────────────
async def info_agent(entities, language, tenant_config, sub_category=None,
                     tenant_id="", user_id="", session_id="",
                     response_type="rag", subcat_config=None,
                     raw_text="", **kwargs) -> dict:
    agent = InfoAgent(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
    return await agent.run(entities, language, tenant_config, sub_category=sub_category,
                           tenant_id=tenant_id, user_id=user_id, session_id=session_id,
                           response_type=response_type, subcat_config=subcat_config,
                           raw_text=raw_text, **kwargs)