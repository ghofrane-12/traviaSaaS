# security.py - Version corrigée

import asyncio
import json
import re
from typing import Optional
from core.config import settings
from orchestrator.rag.rag_engine import RAGEngine
import httpx


SUBCAT_DISPATCH = {
    "Emergency Contacts":           "_emergency_contacts",
    "Fraud Prevention":             "_fraud_prevention",
    "Lost Or Stolen Items":         "_lost_stolen_items",
    "Insurance & Refund Policies":  "_insurance_refund_policies",
}


class SecurityAgent:

    def __init__(self, tenant_id: str = "", user_id: str = "", session_id: str = ""):
        self.tenant_id = tenant_id
        self.tenant_config = {}
        self.user_id = user_id
        self.session_id = session_id
        self.entities = {}
        self.language = ""
        self.sub_category = ""
        self.agent_type = "security"
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

    async def _resolve_rag_only(self, rag_query: str, steps: list, result_key: str = "security") -> dict:
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

            print(f"[SecurityAgent] ✅ RAG hit → info_results")
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

        print("[SecurityAgent] ⚠ RAG vide → fallback LLM")
        return await self._resolve_llm_only(rag_query, steps, result_key, "")

    async def _resolve_llm_only(self, rag_query: str, steps: list, result_key: str = "security", prompt_override: str = "") -> dict:
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
            
            print(f"[SecurityAgent] 🎯 Prompt personnalisé utilisé")
            
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
                print(f"[SecurityAgent] ❌ Prompt personnalisé échoué: {e} → fallback système")
        
        print("[SecurityAgent] 🔧 Prompt système utilisé")
        gpt_result = await self._gpt_fallback(rag_query)
        return {
            "status": "llm_only",
            "source": "GPT-4o-mini",
            **gpt_result,
            "user_id":    self.user_id,
            "session_id": self.session_id,
            "steps":      steps,
        }
    async def _resolve_human_only(self, steps: list, result_key: str = "security") -> dict:
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
        prompt = f"""Tu es un assistant voyage expert en sécurité et gestion des urgences en voyage. Réponds en JSON valide UNIQUEMENT.
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
            print(f"[SecurityAgent] ❌ GPT: {e}")
            return {
                "type": "info_results",
                "message": "Information non disponible pour le moment.",
                "sections": [{"title": "Erreur temporaire", "content": "Veuillez réessayer.", "items": []}],
                "actions": []
            }

    # =========================================================================
    # MÉTHODES MÉTIERS
    # =========================================================================

    async def _emergency_contacts(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        rag_query = (
            f"urgence contacts secours voyage {' '.join(locs)} "
            f"ambassade police pompiers samu numéro urgence assistance médicale "
            f"emergency contacts embassy police ambulance fire medical assistance"
        )
        steps = [
            "Notez les numéros d'urgence locaux (police, pompiers, ambulance)",
            "Enregistrez l'adresse et le téléphone de votre ambassade ou consulat",
            "Souscrivez une assistance rapatriement avant le départ",
            "Informez vos proches de votre itinéraire et de vos contacts",
            "Conservez une copie de vos documents d'identité en cas de perte",
        ]
        
        if self.response_type == "rag":
            return await self._resolve_rag_only(rag_query, steps, "emergency_contacts")
        elif self.response_type == "llm":
            prompt_override = self.subcat_config.get("llm_prompt_override", "")
            return await self._resolve_llm_only(rag_query, steps, "emergency_contacts", prompt_override)
        elif self.response_type == "human":
            return await self._resolve_human_only(steps, "emergency_contacts")
        
        return await self._resolve_rag_only(rag_query, steps, "emergency_contacts")

    async def _fraud_prevention(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        rag_query = (
            f"fraude arnaque voyage sécurité {' '.join(locs)} "
            f"phishing faux site réservation taxi non officiel carte bancaire usurpation "
            f"fraud scam phishing fake booking taxi scam credit card theft"
        )
        steps = [
            "Utilisez des sites de réservation officiels et vérifiez les URL",
            "Méfiez-vous des offres trop alléchantes ou des prix anormalement bas",
            "Ne communiquez jamais vos informations bancaires par téléphone ou email",
            "Préférez les taxis officiels aux stations ou via des applications reconnues",
            "Activez les alertes SMS pour vos transactions bancaires",
        ]
        
        if self.response_type == "rag":
            return await self._resolve_rag_only(rag_query, steps, "fraud_prevention")
        elif self.response_type == "llm":
            prompt_override = self.subcat_config.get("llm_prompt_override", "")
            return await self._resolve_llm_only(rag_query, steps, "fraud_prevention", prompt_override)
        elif self.response_type == "human":
            return await self._resolve_human_only(steps, "fraud_prevention")
        
        return await self._resolve_rag_only(rag_query, steps, "fraud_prevention")

    async def _lost_stolen_items(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        rag_query = (
            f"perte vol objets voyage {' '.join(locs)} "
            f"passeport perdu portefeuille volé carte bancaire bloquée valise disparue "
            f"lost stolen passport wallet credit card blocked luggage missing"
        )
        steps = [
            "Déposez immédiatement une plainte auprès de la police locale",
            "Contactez votre ambassade pour un passeport ou titre de voyage d'urgence",
            "Bloquez vos cartes bancaires via l'application de votre banque",
            "Conservez des copies numériques de tous vos documents dans le cloud",
            "Prévenez votre assurance voyage et conservez tous les justificatifs",
        ]
        
        if self.response_type == "rag":
            return await self._resolve_rag_only(rag_query, steps, "lost_stolen_items")
        elif self.response_type == "llm":
            prompt_override = self.subcat_config.get("llm_prompt_override", "")
            return await self._resolve_llm_only(rag_query, steps, "lost_stolen_items", prompt_override)
        elif self.response_type == "human":
            return await self._resolve_human_only(steps, "lost_stolen_items")
        
        return await self._resolve_rag_only(rag_query, steps, "lost_stolen_items")

    async def _insurance_refund_policies(self, entities: dict) -> dict:
        locs = entities.get("LOC", [])
        rag_query = (
            f"assurance voyage remboursement annulation rapatriement {' '.join(locs)} "
            f"franchise déclaration sinistre documents justificatifs conditions "
            f"travel insurance cancellation refund repatriation claim documents"
        )
        steps = [
            "Souscrivez une assurance voyage avant toute réservation",
            "Conservez tous les justificatifs (billets, reçus, rapports médicaux)",
            "Contactez votre assurance dans les 48h suivant l'incident",
            "Vérifiez les conditions d'annulation et de remboursement",
            "Conservez les originaux des documents pour votre dossier",
        ]
        
        if self.response_type == "rag":
            return await self._resolve_rag_only(rag_query, steps, "insurance_refund_policies")
        elif self.response_type == "llm":
            prompt_override = self.subcat_config.get("llm_prompt_override", "")
            return await self._resolve_llm_only(rag_query, steps, "insurance_refund_policies", prompt_override)
        elif self.response_type == "human":
            return await self._resolve_human_only(steps, "insurance_refund_policies")
        
        return await self._resolve_rag_only(rag_query, steps, "insurance_refund_policies")


# ─── Wrapper ─────────────────────────────────────────────────────────────────
async def security_agent(entities, language, tenant_config, sub_category=None,
                         tenant_id="", user_id="", session_id="",
                         response_type="rag", subcat_config=None,
                         raw_text="", **kwargs) -> dict:
    agent = SecurityAgent(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
    return await agent.run(entities, language, tenant_config, sub_category=sub_category,
                           tenant_id=tenant_id, user_id=user_id, session_id=session_id,
                           response_type=response_type, subcat_config=subcat_config,
                           raw_text=raw_text, **kwargs)