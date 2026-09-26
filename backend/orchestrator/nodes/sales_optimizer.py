# orchestrator/nodes/sales_optimizer_node.py
from orchestrator.state import AgentState
from typing import Any, Dict, List, Optional
import logging
import json
import hashlib
import httpx
from core.config import settings
import copy
import time as _time
from services.redis import _translate_to_french

logger = logging.getLogger("chat_logger")
import os

_TAXONOMY: dict = {}
_TAXONOMY_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "categories.json")

try:
    with open(_TAXONOMY_PATH, "r", encoding="utf-8") as f:
        _TAXONOMY = json.load(f)
    logger.info(f"[SalesOptimizer] ✅ Taxonomie chargée ({sum(len(v) for v in _TAXONOMY.values())} sous-catégories)")
except Exception as e:
    logger.warning(f"[SalesOptimizer] ⚠ Impossible de charger categories.json: {e}")
    _TAXONOMY = {}


def _infer_category(sub_category: str) -> str:
    """Déduit la category depuis sub_category en lisant categories.json."""
    for category, subcats in _TAXONOMY.items():
        if sub_category in subcats:
            return category
    return "Reservation"  
# =============================================================================
# TAUX DE CHANGE
# =============================================================================

_EXCHANGE_RATES_FALLBACK: Dict[str, float] = {
    "EUR": 1.0,  "USD": 1.09, "GBP": 0.86,  "JPY": 165.0,
    "CAD": 1.48, "CHF": 0.96, "AED": 4.0,   "TND": 3.35,
    "MAD": 10.8, "DZD": 147.0,"TRY": 35.0,  "QAR": 4.0,
    "KWD": 0.33, "SAR": 4.1,
}

_rates_cache: Dict[str, Any] = {}
_RATES_TTL = 3600 


async def _get_exchange_rates() -> Dict[str, float]:
    """
    Récupère les taux de change live depuis frankfurter.app (gratuit, sans clé).
    Fallback sur les taux hardcodés si l'API est indisponible.
    Cache en mémoire 1h.
    """
    now = _time.time()
    if _rates_cache.get("rates") and now - _rates_cache.get("ts", 0) < _RATES_TTL:
        return _rates_cache["rates"]

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("https://api.frankfurter.app/latest?base=EUR")
            if r.status_code == 200:
                data = r.json()
                rates = {"EUR": 1.0, **data["rates"]}
                _rates_cache["rates"] = rates
                _rates_cache["ts"]    = now
                logger.info(f"[SalesOptimizer] ✅ Taux live chargés ({len(rates)} devises)")
                return rates
    except Exception as e:
        logger.warning(f"[SalesOptimizer] ⚠ Taux live indisponibles: {e} → fallback")

    return _EXCHANGE_RATES_FALLBACK


def _convert_amount(amount: float, from_cur: str, to_cur: str,
                    rates: Dict[str, float]) -> float:
    """
    Convertit un montant de from_cur vers to_cur via EUR comme devise pivot.
    Si même devise → retourne directement sans calcul.
    """
    from_cur = (from_cur or "").upper()
    to_cur   = (to_cur   or "").upper()

    if from_cur == to_cur or not from_cur or not to_cur:
        return round(amount, 2)

    rate_from = rates.get(from_cur, 1.0)
    rate_to   = rates.get(to_cur,   1.0)

    in_eur = amount / rate_from
    result = in_eur * rate_to
    return round(result, 2)


def _needs_conversion(from_cur: str, to_cur: str) -> bool:
    return (from_cur or "").upper() != (to_cur or "").upper()


def _detect_result_currency(result: dict, tenant_currency: str) -> str:
    """
    Détecte la devise du résultat.
    Si absente → retourne la devise du tenant (pas de conversion nécessaire).
    """
    if result.get("currency"):
        return result["currency"].upper()

    for key in ("results", "offers", "hotels", "flights"):
        items = result.get(key, [])
        if isinstance(items, list) and items and isinstance(items[0], dict):
            cur = items[0].get("currency")
            if cur:
                return cur.upper()

    logger.debug(f"[SalesOptimizer] Devise absente → utilise devise tenant: {tenant_currency}")
    return tenant_currency.upper()



# =============================================================================
# EXTRACTION DU CONTEXTE
# =============================================================================

def _extract_commercial_context(state: AgentState, results: dict,
                                 rates: Dict[str, float]) -> dict:
    entities       = state.get("entities", {})
    loyalty        = state.get("loyalty_summary", {})
    tenant_config  = state.get("tenant_config", {})
    classification = state.get("classification", [{}])
    sub_category   = classification[0].get("sub_category", "") if classification else ""
    language       = state.get("language", "query_fr")
    raw_text       = state.get("raw_text", "")

    # ── Localisation ──────────────────────────────────────────────────
    locs        = entities.get("LOC", [])
    origin      = locs[0] if len(locs) > 0 else ""
    destination = locs[1] if len(locs) > 1 else (locs[0] if locs else "")

    # ── Dates ─────────────────────────────────────────────────────────
    dates   = entities.get("DATE", [])
    checkin  = dates[0] if dates else ""
    checkout = dates[1] if len(dates) > 1 else ""

    # ── Budget ────────────────────────────────────────────────────────
    prices = entities.get("PRICE", [])
    budget_values = []
    for p in prices:
        try:
            budget_values.append(float(str(p).replace(",", ".")))
        except:
            pass

    # ── Offres extraites ──────────────────────────────────────────────
    target_currency = tenant_config.get("currency", "EUR").upper()
    all_offers      = []
    agent_types     = []
    info_sections   = [] 

    for agent_name, result in results.items():
        if not isinstance(result, dict):
            continue

        agent_types.append(agent_name)
        status      = result.get("status", "")
        result_type = result.get("type", "")

        has_real_offers = bool(
            result.get("results") or result.get("offers") or
            result.get("activities") or result.get("hotels") or
            result.get("flights")
        )

        if result_type == "info_results" or (
            status in ("rag_only", "llm_only") and not has_real_offers
        ):
            sections = result.get("sections", [])
            for sec in sections[:3]:
                info_sections.append({
                    "title":   sec.get("title", ""),
                    "content": sec.get("content", "")[:200],
                    "items":   sec.get("items", [])[:3],
                })
            continue

        offers = _extract_offers(result)
        if not offers:
            continue

        result_currency = _detect_result_currency(result, target_currency)

        for offer in offers[:5]:
            price    = _get_offer_price(offer)
            offer_cur = (offer.get("currency") or result_currency).upper()

            price_in_target = (
                _convert_amount(price, offer_cur, target_currency, rates)
                if _needs_conversion(offer_cur, target_currency)
                else price
            )

            offer_summary = {
                "agent":       agent_name,
                "type":        _detect_offer_type(agent_name, offer),
                "name":        offer.get("name") or offer.get("airline") or offer.get("hotel_name", ""),
                "price":       price_in_target,
                "currency":    target_currency,
                "destination": (
                    offer.get("city") or
                    (offer.get("arrival", {}).get("city", "") if isinstance(offer.get("arrival"), dict) else "") or
                    destination
                ),
                "stars":       offer.get("stars") or offer.get("category", ""),
                "duration":    offer.get("duration") or offer.get("nights", ""),
                "amenities":   _extract_amenity_names(offer),
                "stops":       offer.get("stops"),
            }
            all_offers.append(offer_summary)
            logger.info(
                f"[SalesOptimizer] 📊 Contexte: {len(all_offers)} offres extraites | "
                f"sections info: {len(info_sections)}"
            )

    # ── Loyalty ───────────────────────────────────────────────────────
    tier       = loyalty.get("tier", "bronze")
    discount   = loyalty.get("discount", 0)
    points     = loyalty.get("points_after", 0)
    next_tier  = loyalty.get("next_tier", {})

    travel_type = _detect_travel_type(sub_category, agent_types)

    prices_list  = [o["price"] for o in all_offers if o["price"] > 0]
    price_range  = {
        "min": min(prices_list) if prices_list else 0,
        "max": max(prices_list) if prices_list else 0,
    }

    return {
        "language":       language,
        "raw_text":       raw_text,
        "travel_type":    travel_type,
        "sub_category":   sub_category,
        "origin":         origin,
        "destination":    destination,
        "checkin":        checkin,
        "checkout":       checkout,
        "budget_values":  budget_values,
        "budget_min":     min(budget_values) if budget_values else 0,
        "budget_max":     max(budget_values) if budget_values else 0,
        "offers":         all_offers,
        "offers_count":   len(all_offers),
        "price_range":    price_range,
        "target_currency": target_currency,
        "margin":         float(tenant_config.get("margin_percentage", 0)),
        "info_sections":  info_sections,
        "loyalty": {
            "tier":       tier,
            "discount":   discount,
            "points":     points,
            "total_spent": loyalty.get("total_spent", 0),
            "next_tier":  next_tier,
        },
        "agent_types": agent_types,
    }


# =============================================================================
# NODE PRINCIPAL
# =============================================================================

async def sales_optimizer_node(state: AgentState) -> AgentState:
    logger.info("[SalesOptimizer] ========== DÉBUT ==========")

    # ── 1. Charger les taux de change live ───────────────────────────
    rates = await _get_exchange_rates()

    # ── 2. Lire config tenant depuis le state ────────────────────────
    tenant_config   = state.get("tenant_config", {})
    target_currency = tenant_config.get("currency", "EUR").upper()
    margin          = float(tenant_config.get("margin_percentage", 0))

    # ── 3. Réduction fidélité ────────────────────────────────────────
    loyalty_tier = state.get("loyalty_summary", {}).get("tier", "bronze")
    from services.loyalty_service import LoyaltyService
    tier_config      = LoyaltyService.TIERS_CONFIG.get(loyalty_tier, LoyaltyService.TIERS_CONFIG["bronze"])
    loyalty_discount = tier_config["discount"]
    effective_margin = max(0.0, margin - loyalty_discount)

    logger.info(
        f"[SalesOptimizer] Tenant currency={target_currency} | "
        f"margin={margin}% | loyalty_discount={loyalty_discount}% | "
        f"effective_margin={effective_margin}% | tier={loyalty_tier}"
    )

    results           = state.get("results", {})
    optimized_results = {}

    # ── 4. Optimisation prix pour chaque agent ───────────────────────
    for agent_name, result in results.items():
        if not isinstance(result, dict):
            optimized_results[agent_name] = result
            continue

        status = result.get("status", "unknown")

        if status in ("need_more_info", "human_required", "api_error"):
            optimized_results[agent_name] = result
            continue

        result_currency = _detect_result_currency(result, target_currency)
        needs_conv      = _needs_conversion(result_currency, target_currency)

        logger.info(
            f"[SalesOptimizer] Agent={agent_name} | "
            f"result_currency={result_currency} | "
            f"target={target_currency} | "
            f"conversion={'OUI' if needs_conv else 'NON'} | "
            f"margin={effective_margin}%"
        )

        optimized = _optimize_result(
            result, result_currency, target_currency,
            effective_margin, rates, needs_conv
        )
        optimized_results[agent_name] = optimized

        offers = _extract_offers(optimized)
        if offers:
            logger.info(f"[SalesOptimizer] ✅ {len(offers)} offres optimisées")

    # ── 5. Extraction contexte commercial ───────────────────────────
    commercial_context = _extract_commercial_context(state, optimized_results, rates)

    # ── 6. Suggestions désactivées ───────────────────────────────────
    all_suggestions = state.get("suggestions", [])

    logger.info(
        f"[SalesOptimizer] ✅ {len(optimized_results)} agents | "
        f"0 suggestions (désactivées)"
    )
    logger.info("[SalesOptimizer] ========== FIN ==========")
     # ── 8. Générer cross-sell proposals ──────────────────────────────────
    if not state.get("is_cross_sell"):
        cross_sell_proposals = await _generate_cross_sell_proposals(state, optimized_results)  
        logger.info(f"[SalesOptimizer] 🎯 {len(cross_sell_proposals)} propositions cross-sell")
    else:
        cross_sell_proposals = []
        logger.info("[SalesOptimizer] ⏭ Flux cross-sell → pas de re-génération proposals")

    return {
        **state,
        "results":              optimized_results,
        "suggestions":          all_suggestions,
        "commercial_context":   commercial_context,
        "cross_sell_proposals": cross_sell_proposals,
    }



async def _generate_cross_sell_proposals(state: AgentState, optimized_results: dict = None) -> list:
    """
    Génère des propositions cross-sell via GPT-4o-mini.
    Basées sur les vraies offres + profil + sous-catégories ACTIVES du tenant.
    """
    profile        = state.get("context", {}).get("profile", {})
    loyalty        = state.get("loyalty_summary", {})
    classification = state.get("classification", [])
    entities       = state.get("entities", {})
    tenant_config  = state.get("tenant_config", {})

    if not classification:
        return []

    primary_sub_cat = classification[0].get("sub_category", "") if classification else ""
    tier            = loyalty.get("tier", "bronze")
    language        = state.get("language", "query_fr")
    logger.info(f"[SalesOptimizer] 🌍 langue cross-sell: language={language}")



    active_subcats = state.get("active_subcategories", [])
    if not active_subcats:
        subcat_config = state.get("context", {}).get("tenant_subcat_config", {})
        active_subcats = [
            k for k, v in subcat_config.items()
            if isinstance(v, dict) and v.get("is_active")
            and k != primary_sub_cat  
        ]

    active_subcats = [s for s in active_subcats if s != primary_sub_cat]
    destination = ""
    checkin     = ""
    checkout    = ""

    if optimized_results:
        for agent_name, result in optimized_results.items():
            if not isinstance(result, dict):
                continue
            query = result.get("query", {})
            if not destination:
                destination = (
                    query.get("destination") or
                    query.get("city") or
                    query.get("origin", "")
                )
            if not checkin:
                checkin  = query.get("arrival_date") or query.get("travel_date") or query.get("departure_date", "")
                checkout = query.get("departure_date") or query.get("return_date", "")
                if checkin == checkout:
                    checkout = ""
            if destination and checkin:
                break

    raw_entities = state.get("entities", {})
    if not destination:
        if isinstance(raw_entities, dict):
            destination = (
                raw_entities.get("destination") or
                raw_entities.get("hotel_city") or
                raw_entities.get("tour_city") or
                (raw_entities.get("LOC", [""])[0] if raw_entities.get("LOC") else "")
            )
        elif isinstance(raw_entities, list):
            for e in raw_entities:
                if e.get("type") == "LOC" and e.get("value"):
                    destination = e["value"]
                    break

    if not checkin:
        if isinstance(raw_entities, dict):
            dates    = raw_entities.get("DATE", [])
            checkin  = dates[0] if dates else ""
            checkout = dates[1] if len(dates) > 1 else ""
        elif isinstance(raw_entities, list):
            date_vals = [e["value"] for e in raw_entities if e.get("type") == "DATE" and e.get("value")]
            checkin  = date_vals[0] if date_vals else ""
            checkout = date_vals[1] if len(date_vals) > 1 else ""

    if destination and any('\u0600' <= c <= '\u06ff' for c in destination):
        destination = _translate_to_french(destination, "query_ar")

    # ── Profil ────────────────────────────────────────────────────────
    stars        = profile.get("preferred_hotel_stars", 3)
    travel_style = profile.get("travel_style", "")
    usual_budget = profile.get("usual_budget", "")
    points       = loyalty.get("points_after", 0)

    # ── Langue ────────────────────────────────────────────────────────
    lang_map = {
        "query_fr":      "français",
        "query_ar":      "arabe",
        "query_en":      "anglais",
        "query_derja_l": "arabe",
        "query_derja_a": "arabe",
    }
    target_lang = lang_map.get(language, "français")

    if not active_subcats:
        logger.warning("[SalesOptimizer] ⚠ Aucune sous-catégorie active pour cross-sell")
        return []

    prompt = f"""⚠️ LANGUE OBLIGATOIRE : {target_lang}. Toute réponse dans une autre langue est INTERDITE.

Tu es un agent commercial expert en voyages. 
Un client vient de rechercher : "{primary_sub_cat}" vers "{destination}".

PROFIL CLIENT:
- Tier fidélité: {tier.upper()} ({points} points)
- Préférence hôtel: {stars}★
- Style de voyage: {travel_style or 'non spécifié'}
- Budget habituel: {usual_budget or 'non spécifié'}

DATES: {checkin} → {checkout or 'non spécifié'}

SERVICES DISPONIBLES CHEZ CETTE AGENCE (uniquement ceux-ci):
{chr(10).join(f"- {s}" for s in active_subcats[:10])}

RÈGLES STRICTES:
1. Génère exactement 2 ou 3 propositions cross-sell CLIQUABLES
2. Chaque proposition doit correspondre à UN service de la liste ci-dessus
3. Ne propose PAS "{primary_sub_cat}" (déjà recherché)
4. JAMAIS choisis des services de gestion interne (Management, Administration...)
5. Les propositions doivent être complémentaires à la recherche actuelle
6. Langue: {target_lang} UNIQUEMENT — ni aucune autre langue
7. Labels courts et accrocheurs (max 40 chars)
8. Retourne UNIQUEMENT un tableau JSON valide, sans texte avant ou après

FORMAT:
[
  {{
    "sub_category": "nom exact du service depuis la liste",
    "label": "label court affiché au client",
    "description": "1 phrase persuasive",
    "icon": "hotel|plane|map|car|star|coffee|shield|crown",
    "badge": "RECOMMANDÉ|POPULAIRE|EXCLUSIF|null"
  }}
]

JSON:"""

    try:
        async with httpx.AsyncClient(
            verify=False,
            timeout=httpx.Timeout(20.0, connect=10.0)
        ) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       "gpt-4o-mini",
                    "max_tokens":  400,
                    "temperature": 0.3,
                    "messages": [
                        {
                            "role":    "system",
                            "content": f"Tu es un expert commercial en voyages. Tu génères uniquement du JSON valide. Langue de réponse obligatoire : {target_lang}."

                        },
                        {"role": "user", "content": prompt},
                    ],
                }
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"].strip()

            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            proposals_raw = json.loads(raw)
            if not isinstance(proposals_raw, list):
                return []

            proposals = []
            for p in proposals_raw[:3]:
                sub_cat = p.get("sub_category", "")
                if not sub_cat or sub_cat not in active_subcats:
                    continue

                base_entities = []
                if destination:
                    base_entities.append({"type": "LOC", "value": destination})
                if checkin:
                    base_entities.append({"type": "DATE", "value": checkin})
                if checkout and checkout != checkin:
                    base_entities.append({"type": "DATE", "value": checkout})

                category = _infer_category(sub_cat)

                proposals.append({
                    "id":             f"cs_{sub_cat.lower().replace(' ', '_')}",
                    "label":          p.get("label", sub_cat),
                    "description":    p.get("description", ""),
                    "icon":           p.get("icon", "map"),
                    "badge":          p.get("badge") if p.get("badge") != "null" else None,
                    "classification": [{"sub_category": sub_cat, "category": category, "confidence": 0.95}],
                    "entities":       base_entities,
                })

            logger.info(
                f"[SalesOptimizer] 🎯 {len(proposals)} cross-sell proposals GPT | "
                f"tier={tier} | dest={destination} | checkin={checkin}"
            )
            return proposals

    except Exception as e:
        logger.error(f"[SalesOptimizer] ❌ GPT cross-sell error: {e}")
        return []


# =============================================================================
# OPTIMISATION PRIX
# =============================================================================

def _optimize_result(result: Dict, result_currency: str, target_currency: str,
                     effective_margin: float, rates: Dict[str, float],
                     needs_conv: bool) -> Dict:
    """
    Optimise un résultat agent :
    - Convertit les prix si nécessaire (result_currency != target_currency)
    - Applique la marge effective du tenant
    - Préserve la structure originale
    """
    optimized = result.copy()

    # ── Cas transport ─────────────────────────────────────────────────
    if result.get("type") == "transport_search_results":
        items = result.get("results", [])
        if items:
            optimized["results"] = [
                _optimize_item_price(item, result_currency, target_currency,
                                     effective_margin, rates, needs_conv)
                for item in items
            ]
        optimized["currency"]         = target_currency
        optimized["display_currency"] = target_currency
        return optimized

    # ── Cas restaurant / spécialités ──────────────────────────────────
    if result.get("type") in ("restaurant_search_results", "specialty_search_results"):
        items = result.get("results", [])
        if items:
            optimized["results"] = [
                _optimize_item_price(item, result_currency, target_currency,
                                     effective_margin, rates, needs_conv)
                for item in items
            ]
        optimized["display_currency"] = target_currency
        return optimized

    # ── Cas info_results : pas de prix à optimiser ────────────────────
    if result.get("type") == "info_results":
        return optimized

    # ── Cas vols / hôtels / activités ────────────────────────────────
    offers = _extract_offers(result)
    if not offers:
        optimized["display_currency"] = target_currency
        return optimized

    optimized_offers = []
    for offer in offers:
        offer_currency = (
            offer.get("currency") or offer.get("original_currency") or result_currency
        ).upper()
        offer_needs_conv = _needs_conversion(offer_currency, target_currency)

        opt = _optimize_offer(
            offer, offer_currency, target_currency,
            effective_margin, rates, offer_needs_conv
        )
        optimized_offers.append(opt)

    optimized_offers.sort(
        key=lambda o: o.get("price_per_night", o.get("price", 0)) or 0
    )

    optimized = _update_offers_in_result(optimized, optimized_offers)

    if optimized_offers:
        best = optimized_offers[0]
        optimized["price_per_night"] = best.get("price_per_night", 0)
        optimized["total_price"]     = best.get("price", 0)
        optimized["best_offer"]      = best.get("best_offer") or best

        all_sub_prices = []
        for o in optimized_offers:
            for s in o.get("offers", []):
                p = s.get("price", 0)
                if p > 0:
                    all_sub_prices.append(p)

        optimized["price_range"] = {
            "min": min(all_sub_prices) if all_sub_prices else optimized_offers[0].get("price", 0),
            "max": max(all_sub_prices) if all_sub_prices else optimized_offers[-1].get("price", 0),
        }

    optimized["currency"]          = target_currency
    optimized["display_currency"]  = target_currency
    optimized["original_currency"] = result_currency
    optimized["margin_applied"]    = effective_margin
    return optimized


def _apply_price_field(value: Any, from_cur: str, to_cur: str,
                       margin: float, rates: Dict, needs_conv: bool) -> Optional[float]:
    """Convertit et applique la marge sur un champ prix."""
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("min") or value.get("total") or value.get("value") or 0
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None

    converted = _convert_amount(v, from_cur, to_cur, rates) if needs_conv else v
    return round(converted * (1 + margin / 100), 2)


def _optimize_item_price(item: dict, result_currency: str, target_currency: str,
                          margin: float, rates: Dict, needs_conv: bool) -> dict:
    """Optimise les prix d'un item simple (transport, restaurant...)."""
    item_copy = item.copy()
    item_cur  = (item_copy.get("currency") or result_currency).upper()
    item_conv = _needs_conversion(item_cur, target_currency)

    for field in ("price", "price_per_night", "total_price", "min_price", "max_price"):
        raw = item_copy.get(field)
        if raw is None:
            continue
        result = _apply_price_field(raw, item_cur, target_currency, margin, rates, item_conv)
        if result is not None:
            item_copy[f"{field}_original"] = raw
            item_copy[field]               = result

    price_range = item_copy.get("price_range")
    if isinstance(price_range, dict):
        for bound in ("min", "max"):
            val = price_range.get(bound)
            if val:
                r = _apply_price_field(val, item_cur, target_currency, margin, rates, item_conv)
                if r:
                    price_range[bound] = r
        item_copy["price_range"] = price_range

    item_copy["currency"]         = target_currency
    item_copy["display_currency"] = target_currency
    if item_conv:
        item_copy["original_currency"] = item_cur
    item_copy["margin_applied"] = margin
    return item_copy


def _optimize_offer(offer: dict, offer_currency: str, target_currency: str,
                    margin: float, rates: Dict, needs_conv: bool) -> dict:
    """Optimise une offre complète (vol, hôtel...) avec ses sous-offres."""
    opt = offer.copy()
    _normalize_flight_prices(opt)

    for field in ("price", "price_per_night", "total_price", "price_with_markup",
                  "base_price", "min_price", "max_price", "price_min", "price_max"):
        raw = opt.get(field)
        if raw is None:
            continue
        field_currency = opt.get(f"{field}_currency", offer_currency)
        field_conv     = _needs_conversion(field_currency, target_currency)
        result = _apply_price_field(raw, field_currency, target_currency, margin, rates, field_conv)
        if result is not None:
            opt[f"{field}_original"]          = raw
            opt[f"{field}_original_currency"] = field_currency
            opt[f"{field}_converted"]         = result
            opt[field]                        = result


    fare_options = opt.get("fare_options", [])
    if isinstance(fare_options, list):
        updated_fares = []
        for fare in fare_options:
            if not isinstance(fare, dict):
                updated_fares.append(fare)
                continue
            fc     = fare.copy()
            fp     = fc.get("price")
            f_cur  = (fc.get("currency") or offer_currency).upper()
            f_conv = _needs_conversion(f_cur, target_currency)
            if fp is not None:
                r = _apply_price_field(fp, f_cur, target_currency, margin, rates, f_conv)
                if r is not None:
                    fc["price_original"] = fp
                    fc["price"]          = r
                    fc["currency"]       = target_currency
            updated_fares.append(fc)
        opt["fare_options"] = updated_fares

    sub_offers = opt.get("offers", [])
    if isinstance(sub_offers, list) and sub_offers:
        converted_subs = []
        for sub in sub_offers:
            if not isinstance(sub, dict):
                converted_subs.append(sub)
                continue
            sub_copy = sub.copy()
            sub_cur  = (sub_copy.get("currency") or offer_currency).upper()
            sub_conv = _needs_conversion(sub_cur, target_currency)
            for field in ("price", "price_per_night", "price_with_markup"):
                raw = sub_copy.get(field)
                if raw is None:
                    continue
                r = _apply_price_field(raw, sub_cur, target_currency, margin, rates, sub_conv)
                if r is not None:
                    sub_copy[f"{field}_original"] = raw
                    sub_copy[field]               = r
            sub_copy["currency"]         = target_currency
            sub_copy["margin_applied"]   = margin
            if sub_conv:
                sub_copy["original_currency"] = sub_cur
            converted_subs.append(sub_copy)

        opt["offers"] = converted_subs

        prices_conv = [s.get("price", 0) for s in converted_subs if s.get("price", 0) > 0]
        if prices_conv:
            opt["price_range"] = {"min": min(prices_conv), "max": max(prices_conv)}

        best = opt.get("best_offer")
        if isinstance(best, dict) and converted_subs:
            best_id = best.get("id")
            for s in converted_subs:
                if s.get("id") == best_id:
                    opt["best_offer"] = s
                    break
            else:
                opt["best_offer"] = min(converted_subs, key=lambda x: x.get("price", float("inf")))

    opt["currency"]         = target_currency
    opt["display_currency"] = target_currency
    opt["margin_applied"]   = margin
    if needs_conv:
        opt["original_currency"] = offer_currency

    pn = opt.get("price_per_night") or opt.get("price") or 0
    if isinstance(pn, dict):
        pn = pn.get("min") or 0
    opt["relevance_score"] = round(min(1.0, float(pn or 0) / 500), 3)

    return opt



def _detect_offer_type(agent_name: str, offer: dict) -> str:
    name = agent_name.lower()
    if any(k in name for k in ("transport", "flight", "vol")): return "flight"
    if any(k in name for k in ("stay", "hotel")):              return "hotel"
    if any(k in name for k in ("activity", "event", "tour")):  return "activity"
    if "restaurant" in name:                                    return "restaurant"
    return offer.get("type", "unknown")


def _detect_travel_type(sub_category: str, agent_types: list) -> str:
    sc     = sub_category.lower()
    agents = " ".join(agent_types).lower()
    if "transport" in sc or "flight" in sc or "transport" in agents: return "vol"
    if "hotel" in sc or "stay" in sc or "stay" in agents:            return "hébergement"
    if "activity" in sc or "activity" in agents:                     return "activité"
    if "restaurant" in sc or "restaurant" in agents:                 return "restaurant"
    if "general" in agents:                                           return "information"
    return "voyage"


def _get_offer_price(offer: dict) -> float:
    for field in ("price", "price_min", "price_per_night", "total_price", "min_price"):
        val = offer.get(field)
        if val is None:
            continue
        if isinstance(val, dict):
            val = val.get("min") or val.get("total") or 0
        try:
            v = float(val)
            if v > 0:
                return v
        except:
            continue
    fares = offer.get("fare_options", [])
    if isinstance(fares, list) and fares:
        try:
            return float(fares[0].get("price", 0))
        except:
            pass
    return 0.0


def _extract_amenity_names(offer: dict) -> list:
    amenities = offer.get("amenities", [])
    if isinstance(amenities, list):
        return [a.get("name", "") for a in amenities if isinstance(a, dict)][:3]
    return []


def _normalize_flight_prices(offer: dict) -> None:
    price = offer.get("price")
    if isinstance(price, dict):
        offer["price_min"] = price.get("min", 0)
        offer["price_max"] = price.get("max", 0)
        offer["price"]     = price.get("min") or price.get("total") or 0
        if price.get("currency"):
            offer["currency"] = price["currency"]
    if not offer.get("price") and offer.get("fare_options"):
        fares = offer["fare_options"]
        if isinstance(fares, list) and fares and isinstance(fares[0], dict):
            offer["price"] = fares[0].get("price", 0)
            if fares[0].get("currency"):
                offer["currency"] = fares[0]["currency"]


def _extract_offers(result: Dict) -> Optional[List[Dict]]:
    if result.get("type") in ("info_results",):
        return None

    for key in ("results", "offers", "hotels", "flights", "activities", "transports"):
        val = result.get(key)
        if isinstance(val, list) and val and all(isinstance(v, dict) for v in val[:3]):
            return val
        if isinstance(val, dict):
            for sub in ("hotels", "flights", "flightOffers", "data"):
                sub_val = val.get(sub)
                if isinstance(sub_val, list) and sub_val:
                    return sub_val
    return None


def _update_offers_in_result(result: Dict, offers: List[Dict]) -> Dict:
    updated      = result.copy()
    original_type = result.get("type")

    if original_type == "hotel_search_results":
        hotels = [copy.deepcopy(o) for o in offers]
        updated["offers"]  = hotels
        updated["results"] = hotels
        updated["hotels"]  = hotels
        updated["type"]    = "hotel_search_results"
        return updated

    for key in ("offers", "results", "hotels", "flights", "activities", "transports"):
        if key in updated and isinstance(updated[key], list):
            updated[key] = offers
            break

    if original_type:
        updated["type"] = original_type

    updated["offers"] = offers
    return updated