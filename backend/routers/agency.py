# routers/agency.py
import uuid
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from core.database import get_pool
from core.schemas import ReviewCreate, TicketCreate, AuthContext
from methods.auth import get_current_user, require_min_role
router = APIRouter(prefix="/general", tags=["general"])
logger = logging.getLogger("chat_logger")

# =============================================================================
# REVIEWS
# =============================================================================

@router.post("/reviews")
async def create_review(
    body: ReviewCreate,
    current_user: AuthContext = Depends(require_min_role("client"))):
    """Sauvegarde un avis client."""
    if str(current_user.user_id) != body.user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        pool = await get_pool()
        review_id = uuid.uuid4()
        await pool.execute(
            """
            INSERT INTO tenant_reviews
              (review_id, tenant_id, user_id, rating, comment, created_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            """,
            review_id,
            uuid.UUID(body.tenant_id),
            uuid.UUID(body.user_id),
            body.rating,
            body.comment or "",
        )
        logger.info(
            f"[Reviews] ✅ Avis créé | "
            f"tenant={body.tenant_id} | user={body.user_id} | rating={body.rating}"
        )
        return {"success": True, "review_id": str(review_id)}
    except Exception as e:
        logger.error(f"[Reviews] ❌ Erreur création avis: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la sauvegarde de l'avis.")

@router.get("/reviews/{tenant_id}")
async def get_reviews(tenant_id: str, limit: int = 20):
    """Retourne les avis d'un tenant."""
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
            uuid.UUID(tenant_id),
            limit,
        )
        reviews = []
        for r in rows:
            reviews.append({
                "review_id":  str(r["review_id"]),
                "rating":     r["rating"],
                "comment":    r["comment"],
                "created_at": r["created_at"].strftime("%d/%m/%Y") if r["created_at"] else "",
            })
        avg = sum(r["rating"] for r in reviews) / len(reviews) if reviews else 0
        return {
            "success":    True,
            "reviews":    reviews,
            "count":      len(reviews),
            "avg_rating": round(avg, 1),
        }
    except Exception as e:
        logger.error(f"[Reviews] ❌ Erreur lecture avis: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la récupération des avis.")

# =============================================================================
# SUPPORT TICKETS
# =============================================================================

@router.post("/tickets")
async def create_ticket(
    body: TicketCreate,
    current_user: AuthContext = Depends(require_min_role("client"))):
    """Crée un ticket de support (Human Handoff)."""
    if str(current_user.user_id) != body.user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        pool      = await get_pool()
        ticket_id = uuid.uuid4()
        await pool.execute(
            """
            INSERT INTO support_tickets
              (ticket_id, tenant_id, user_id, subject, message, phone, status, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, 'open', NOW())
            """,
            ticket_id,
            uuid.UUID(body.tenant_id),
            uuid.UUID(body.user_id),
            body.subject,
            body.message,
            body.phone or "",
        )
        logger.info(
            f"[Tickets] ✅ Ticket créé | "
            f"tenant={body.tenant_id} | user={body.user_id} | subject={body.subject}"
        )
        return {"success": True, "ticket_id": str(ticket_id)}
    except Exception as e:
        logger.error(f"[Tickets] ❌ Erreur création ticket: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la création du ticket.")
    
@router.get("/tickets/{tenant_id}")
async def get_tickets(tenant_id: str):
    try:
        pool = await get_pool()
        rows = await pool.fetch(
            """
            SELECT ticket_id, user_id, subject, message,
                   phone, status, created_at
            FROM support_tickets
            WHERE tenant_id = $1
            ORDER BY created_at DESC
            """,
            uuid.UUID(tenant_id),
        )
        return [dict(r) for r in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/tickets/{ticket_id}")
async def update_ticket_status(ticket_id: str, body: dict):
    status = body.get("status")
    if status not in ("open", "in_progress", "closed"):
        raise HTTPException(status_code=400, detail="Statut invalide")
    try:
        pool = await get_pool()
        await pool.execute(
            """
            UPDATE support_tickets
            SET status = $1, updated_at = NOW()
            WHERE ticket_id = $2
            """,
            status,
            uuid.UUID(ticket_id),
        )
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))