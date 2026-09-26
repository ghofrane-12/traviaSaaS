# services/loyalty_service.py

from decimal import Decimal
from uuid import UUID
import logging
from datetime import datetime

logger = logging.getLogger("chat_logger")


class LoyaltyService:
    """Service de gestion de la fidélité — asyncpg uniquement."""

    TIERS_CONFIG = {
        "bronze": {
            "min_spent":        0,
            "points_multiplier": 1,
            "discount":         0,
            "color":            "🥉",
            "benefits":         []
        },
        "silver": {
            "min_spent":        500,
            "points_multiplier": 1.5,
            "discount":         5,
            "color":            "🥈",
            "benefits":         ["Check-in prioritaire", "Support dédié"]
        },
        "gold": {
            "min_spent":        1500,
            "points_multiplier": 2,
            "discount":         10,
            "color":            "🥇",
            "benefits":         [
                "Check-in prioritaire", "Support dédié",
                "Bagage offert", "Annulation flexible"
            ]
        },
        "platinum": {
            "min_spent":        5000,
            "points_multiplier": 3,
            "discount":         15,
            "color":            "💎",
            "benefits":         [
                "Check-in prioritaire", "Support 24/7",
                "Bagage offert", "Repas offert",
                "Surclassement possible", "Accès lounge"
            ]
        },
    }

    # =========================================================================
    # GET OR CREATE
    # =========================================================================

    async def get_or_create_loyalty(
        self,
        tenant_id: UUID,
        user_id:   UUID,
        pool=None,      
        session=None,    
    ):
        """
        Récupère ou crée un compte de fidélité.
        Retourne un objet-like avec les attributs loyalty_id, tier, points, total_spent.
        """
        from core.database import get_pool as _get_pool
        p = pool or await _get_pool()

        user_exists = await p.fetchval(
            "SELECT 1 FROM users WHERE user_id = $1", user_id
        )
        if not user_exists:
            logger.warning(f"[LoyaltyService] ⚠ User {user_id} inexistant — skip loyalty")
            return None

        row = await p.fetchrow(
            """
            SELECT loyalty_id, tenant_id, user_id, points, tier, total_spent,
                   last_transaction_at, created_at, updated_at
            FROM tenant_loyalty
            WHERE tenant_id = $1 AND user_id = $2
            """,
            tenant_id, user_id
        )

        if row:
            return _LoyaltyRecord(row)

        new_row = await p.fetchrow(
            """
            INSERT INTO tenant_loyalty (tenant_id, user_id, points, tier, total_spent)
            VALUES ($1, $2, 0, 'bronze', 0.00)
            RETURNING loyalty_id, tenant_id, user_id, points, tier, total_spent,
                      last_transaction_at, created_at, updated_at
            """,
            tenant_id, user_id
        )
        logger.info(f"[LoyaltyService] ✅ Nouveau compte créé pour user {user_id}")
        return _LoyaltyRecord(new_row)

    # =========================================================================
    # UPDATE
    # =========================================================================

    async def update_loyalty(
        self,
        loyalty,
        amount_spent:  Decimal,
        points_earned: int,
        pool=None,
        session=None,   
    ) -> dict:
        """Met à jour les points et le tier du client."""
        from core.database import get_pool as _get_pool
        p = pool or await _get_pool()

        old_tier   = loyalty.tier
        old_points = loyalty.points

        new_points      = old_points + points_earned
        new_total_spent = Decimal(str(loyalty.total_spent)) + amount_spent
        new_tier        = self._calculate_tier(new_total_spent)
        tier_upgraded   = new_tier != old_tier

        await p.execute(
            """
            UPDATE tenant_loyalty
            SET points              = $1,
                tier                = $2,
                total_spent         = $3,
                last_transaction_at = $4,
                updated_at          = now()
            WHERE loyalty_id = $5
            """,
            new_points,
            new_tier,
            new_total_spent,
            datetime.utcnow(),
            loyalty.loyalty_id,
        )

        updated_row = await p.fetchrow(
            "SELECT loyalty_id, tier, points, total_spent FROM tenant_loyalty WHERE loyalty_id = $1",
            loyalty.loyalty_id
        )
        if updated_row:
            loyalty._update(updated_row)

        if tier_upgraded:
            logger.info(f"[LoyaltyService] 🎉 Upgrade! {old_tier} → {new_tier}")

        return {
            "previous_tier":  old_tier,
            "new_tier":       new_tier,
            "points_earned":  points_earned,
            "points_before":  old_points,
            "points_after":   new_points,
            "tier_upgraded":  tier_upgraded,
            "benefits":       self.TIERS_CONFIG[new_tier]["benefits"],
        }

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _calculate_tier(self, total_spent: Decimal) -> str:
        for tier, config in sorted(
            self.TIERS_CONFIG.items(),
            key=lambda x: x[1]["min_spent"],
            reverse=True
        ):
            if total_spent >= config["min_spent"]:
                return tier
        return "bronze"

    def calculate_points(self, amount: Decimal, tier: str) -> int:
        multiplier = self.TIERS_CONFIG[tier]["points_multiplier"]
        return max(int(float(amount) * multiplier), 0)

    def get_tier_config(self, tier: str) -> dict:
        return self.TIERS_CONFIG.get(tier, self.TIERS_CONFIG["bronze"])


class _LoyaltyRecord:
    """
    Remplace TenantLoyalty (SQLAlchemy ORM).
    Expose les mêmes attributs pour que loyalty.py continue de fonctionner.
    """
    def __init__(self, row):
        self.loyalty_id             = row["loyalty_id"]
        self.tenant_id              = row["tenant_id"]
        self.user_id                = row["user_id"]
        self.points                 = row["points"]
        self.tier                   = row["tier"]
        self.total_spent            = Decimal(str(row["total_spent"]))
        self.last_transaction_at    = row.get("last_transaction_at")
        self.created_at             = row.get("created_at")
        self.updated_at             = row.get("updated_at")

    def _update(self, row):
        self.points      = row["points"]
        self.tier        = row["tier"]
        self.total_spent = Decimal(str(row["total_spent"]))