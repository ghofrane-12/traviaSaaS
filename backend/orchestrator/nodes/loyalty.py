# orchestrator/nodes/loyalty.py
from orchestrator.state import AgentState
from services.loyalty_service import LoyaltyService
from core.database import get_pool
from uuid import UUID
from decimal import Decimal
import logging

logger = logging.getLogger("chat_logger")


async def loyalty_node(state: AgentState) -> AgentState:
    print(f"[DEBUG LOYALTY] results type={type(state.get('results'))}")
    print(f"[DEBUG LOYALTY] results val={state.get('results')}")
    logger.info("[Loyalty] ========== DÉBUT ==========")

    results = state.get("results", {})
    if isinstance(results, list):
        results = {f"agent_{i}": r for i, r in enumerate(results) if isinstance(r, dict)}
        state = {**state, "results": results}

    tenant_id = state.get("tenant_id")
    user_id   = state.get("user_id")
    user_role = state.get("role")

    is_visitor = (
        not user_id or
        user_id == "anonymous" or
        user_role in ("visitor", "visiteur") or
        (isinstance(user_id, str) and (
            user_id.startswith("visitor_") or
            len(user_id) < 36
        ))
    )

    if is_visitor:
        logger.info("[Loyalty] 👤 Visiteur détecté → skip loyalty")
        return {**state, "loyalty_summary": {}}

    try:
        user_uuid   = UUID(user_id)
        tenant_uuid = UUID(tenant_id)
    except (ValueError, TypeError) as e:
        logger.warning(f"[Loyalty] ⚠ UUID invalide → skip: {e}")
        return {**state, "loyalty_summary": {}}

    logger.info(f"[Loyalty] tenant_id: {tenant_id}, user_id: {user_id}")

    if not tenant_id or not user_id:
        logger.warning("[Loyalty] ⚠ Pas de tenant_id ou user_id, skip loyalty")
        return state

    logger.info(f"[Loyalty] UUIDs valides: tenant={tenant_uuid}, user={user_uuid}")

    results_before = state.get("results", {})
    logger.info(f"[Loyalty] Résultats avant: {len(results_before)} agents")
    for agent_name, result in results_before.items():
        logger.info(f"[Loyalty]   - {agent_name}: status={result.get('status') if isinstance(result, dict) else 'not dict'}")
        if isinstance(result, dict):
            offers = result.get("offers") or result.get("results")
            if offers:
                if isinstance(offers, dict):
                    logger.info(f"[Loyalty]     Offres: dict avec clés {list(offers.keys())}")
                elif isinstance(offers, list):
                    logger.info(f"[Loyalty]     Offres: {len(offers)} éléments")
            else:
                logger.info(f"[Loyalty]     Pas d'offres")

    try:
        pool           = await get_pool()
        loyalty_service = LoyaltyService()
        logger.info("[Loyalty] Service LoyaltyService initialisé")

        loyalty = await loyalty_service.get_or_create_loyalty(
            tenant_uuid, user_uuid, pool=pool
        )
        if loyalty is None:
            logger.info("[Loyalty] 👤 Visiteur non en base → skip loyalty")
            logger.info("[Loyalty] ========== FIN (VISITEUR) ==========")
            return {**state, "loyalty_summary": {}}

        logger.info(f"[Loyalty] Loyalty compte: tier={loyalty.tier}, points={loyalty.points}, total_spent={loyalty.total_spent}")

        total_amount = _calculate_transaction_amount(state.get("results", {}))
        logger.info(f"[Loyalty] Montant total transaction: {total_amount}€")

        points_earned = loyalty_service.calculate_points(total_amount, loyalty.tier)
        logger.info(f"[Loyalty] Points gagnés: {points_earned}")

        loyalty_update = await loyalty_service.update_loyalty(
            loyalty,
            total_amount,
            points_earned,
            pool=pool,
        )
        logger.info(f"[Loyalty] Mise à jour: {loyalty_update}")

        tier_config = loyalty_service.get_tier_config(loyalty.tier)
        logger.info(f"[Loyalty] Configuration tier: {tier_config}")

        updated_results = _apply_loyalty_to_results(
            state.get("results", {}),
            loyalty.tier,
            tier_config,
            loyalty_update,
        )
        
        logger.info(f"[Loyalty] Résultats mis à jour: {len(updated_results)} agents")

        for agent_name, result in updated_results.items():
            if isinstance(result, dict):
                offers = result.get("offers") or result.get("results")
                if offers:
                    logger.info(f"[Loyalty] ✅ Après réduction: {agent_name} a des offres")
                else:
                    logger.warning(f"[Loyalty] ⚠ Après réduction: {agent_name} n'a plus d'offres")
                    
        tenant_currency = state.get("tenant_config", {}).get("currency", "EUR")

        has_real_offers = any(
            isinstance(r, dict) and r.get("status") in ("success", "rag_only", "llm_only", "gemini_fallback")
            for r in updated_results.values()
        )
        suggestions = _generate_loyalty_suggestions(
            loyalty_update,
            loyalty.total_spent,
            tier_config,
            currency=tenant_currency,
        ) if has_real_offers else []
        logger.info(f"[Loyalty] Suggestions fidélité: {len(suggestions)}")

        existing_suggestions = state.get("suggestions", [])
        logger.info(f"[Loyalty] Suggestions existantes: {len(existing_suggestions)}")
        all_suggestions = suggestions
        logger.info(f"[Loyalty] Total suggestions: {len(all_suggestions)}")

        logger.info(f"[Loyalty] ✅ Tier: {loyalty.tier} | Points: +{points_earned} | Total: {loyalty.total_spent}€")

        result_state = {
            **state,
            "results":    updated_results,
            "suggestions": all_suggestions,
            "loyalty_summary": {
                "tier":             loyalty.tier,
                "tier_upgraded":    loyalty_update["tier_upgraded"],
                "previous_tier":    loyalty_update["previous_tier"],
                "discount":         tier_config["discount"],
                "points_multiplier": tier_config["points_multiplier"],
                "points_earned":    points_earned,
                "points_before":    loyalty_update["points_before"],
                "points_after":     loyalty_update["points_after"],
                "total_spent":      float(loyalty.total_spent),
                "benefits":         loyalty_update["benefits"],
                "next_tier":        _get_next_tier(loyalty.tier, loyalty.total_spent, tier_config),
            },
        }

        logger.info("[Loyalty] ========== FIN (SUCCÈS) ==========")
        return result_state

    except Exception as e:
        logger.error(f"[Loyalty] ❌ Erreur: {e}", exc_info=True)
        logger.info("[Loyalty] ========== FIN (ERREUR) ==========")
        return state



def _calculate_transaction_amount(results: dict) -> Decimal:
    total = Decimal("0.00")
    if isinstance(results, list):
        results = {f"agent_{i}": r for i, r in enumerate(results) if isinstance(r, dict)}
    for result in results.values():
        if not isinstance(result, dict):
            continue
        price = _extract_price(result)
        if price:
            total += Decimal(str(price))
    return total


def _extract_price(result: dict) -> float | None:
    if "price_with_margin" in result: return float(result["price_with_margin"])
    if "final_price"       in result: return float(result["final_price"])
    if "original_price"    in result: return float(result["original_price"])
    if "price" in result:
        price = result["price"]
        if isinstance(price, dict): return price.get("amount")
        return float(price) if price else None
    offers = _extract_offers(result)
    if offers and isinstance(offers, list):
        for offer in offers:
            if "final_price" in offer: return float(offer["final_price"])
            if "price" in offer:
                price = offer["price"]
                if isinstance(price, dict): return price.get("amount")
                return float(price) if price else None
    return None


def _extract_offers(result: dict) -> list | None:
    if "hotels"      in result: return result["hotels"]
    if "flightOffers" in result: return result["flightOffers"]
    if "offers" in result:
        offers = result["offers"]
        if isinstance(offers, dict):
            if "data" in offers:
                if "hotels"       in offers["data"]: return offers["data"]["hotels"]
                if "flightOffers" in offers["data"]: return offers["data"]["flightOffers"]
            if "hotels"       in offers: return offers["hotels"]
            if "flightOffers" in offers: return offers["flightOffers"]
        return offers if isinstance(offers, list) else None
    return None


def _apply_loyalty_to_results(results: dict, tier: str, tier_config: dict, loyalty_update: dict) -> dict:
    if isinstance(results, list):
        results = {f"agent_{i}": r for i, r in enumerate(results) if isinstance(r, dict)}
    updated_results  = {}
    discount_percent = tier_config["discount"]

    for agent_name, result in results.items():
        if not isinstance(result, dict):
            updated_results[agent_name] = result
            continue
        status = result.get("status", "")
        if status not in ("success", "gemini_fallback", "rag_only", "llm_only"):
            updated_results[agent_name] = result
            continue

        updated_result = result.copy()
        if discount_percent > 0:
            if "price" in updated_result:
                price = updated_result["price"]
                if isinstance(price, (int, float)):
                    discount = price * discount_percent / 100
                    updated_result["original_price"]  = price
                    updated_result["price"]           = price - discount
                    updated_result["discount_applied"] = discount_percent
                    updated_result["discount_amount"]  = round(discount, 2)

            offers = _extract_offers(updated_result)
            if offers and isinstance(offers, list):
                discounted_offers = []
                for offer in offers:
                    discounted_offer = offer.copy()
                    price = _extract_price_from_offer(offer)
                    if price:
                        discount = price * discount_percent / 100
                        discounted_offer["original_price"]   = price
                        discounted_offer["discount_percent"] = discount_percent
                        discounted_offer["discount_amount"]  = round(discount, 2)
                        discounted_offer["final_price"]      = round(price - discount, 2)
                    discounted_offers.append(discounted_offer)
                updated_result = _update_offers_in_result(updated_result, discounted_offers)

        updated_result["loyalty_info"] = {
            "tier":          tier,
            "discount":      discount_percent,
            "points_earned": loyalty_update["points_earned"],
            "tier_upgraded": loyalty_update["tier_upgraded"],
            "benefits":      loyalty_update["benefits"],
        }
        updated_results[agent_name] = updated_result

    return updated_results


def _extract_price_from_offer(offer: dict) -> float | None:
    if "price" in offer:
        price = offer["price"]
        if isinstance(price, dict): return price.get("amount")
        return float(price) if price else None
    if "price_per_night" in offer: return float(offer["price_per_night"])
    if "min_total_price" in offer: return float(offer["min_total_price"])
    return None


def _update_offers_in_result(result: dict, offers: list) -> dict:
    updated = result.copy()
    if "offers" in updated:
        if isinstance(updated["offers"], dict):
            if "data" in updated["offers"]:
                if "hotels"  in updated["offers"]["data"]: updated["offers"]["data"]["hotels"]  = offers
                elif "flights" in updated["offers"]["data"]: updated["offers"]["data"]["flights"] = offers
            elif "hotels"  in updated["offers"]: updated["offers"]["hotels"]  = offers
            elif "flights" in updated["offers"]: updated["offers"]["flights"] = offers
        else:
            updated["offers"] = offers
    if "hotels"  in updated: updated["hotels"]  = offers
    if "flights" in updated: updated["flights"] = offers
    return updated


def _generate_loyalty_suggestions(loyalty_update: dict, total_spent: Decimal, tier_config: dict ,currency: str = "€") -> list:
    suggestions = []
    if loyalty_update["points_earned"] > 0:
        suggestions.append({
            "type": "loyalty", "points": loyalty_update["points_earned"],
            "label": f"+{loyalty_update['points_earned']} points fidélité gagnés",
            "priority": "high", "action": "add_points",
        })
    if loyalty_update["tier_upgraded"]:
        suggestions.append({
            "type": "loyalty_upgrade", "new_tier": loyalty_update["new_tier"],
            "benefits": loyalty_update["benefits"],
            "label": f"🎉 Félicitations! Vous êtes maintenant {loyalty_update['new_tier'].upper()}!",
            "priority": "high", "action": "tier_upgrade",
        })
    next_tier_info = _get_next_tier_info(loyalty_update["new_tier"], total_spent)
    if next_tier_info:
        suggestions.append({
            "type": "next_tier", "next_tier": next_tier_info["tier"],
            "points_needed": next_tier_info["points_needed"],
            "label": f"Plus que {next_tier_info['points_needed']} {currency} pour atteindre {next_tier_info['tier'].upper()}",
            "priority": "medium", "action": "next_tier",
        })
    return suggestions


def _get_next_tier(current_tier: str, total_spent: Decimal, tier_config: dict) -> dict | None:
    tiers_order = ["bronze", "silver", "gold", "platinum"]
    try:
        idx = tiers_order.index(current_tier)
        if idx < len(tiers_order) - 1:
            next_tier = tiers_order[idx + 1]
            next_min  = LoyaltyService.TIERS_CONFIG[next_tier]["min_spent"]
            needed    = next_min - float(total_spent)
            return {"tier": next_tier, "min_spent": next_min, "needed": max(0, needed)}
    except ValueError:
        pass
    return None


def _get_next_tier_info(tier: str, total_spent: Decimal) -> dict | None:
    tiers_order = ["bronze", "silver", "gold", "platinum"]
    try:
        idx = tiers_order.index(tier)
        if idx < len(tiers_order) - 1:
            next_tier = tiers_order[idx + 1]
            next_min  = LoyaltyService.TIERS_CONFIG[next_tier]["min_spent"]
            needed    = next_min - float(total_spent)
            if needed > 0:
                return {"tier": next_tier, "points_needed": int(needed)}
    except ValueError:
        pass
    return None