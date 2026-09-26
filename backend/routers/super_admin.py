# routers/super_admin.py

from fastapi import APIRouter, HTTPException, Depends
from methods.auth import require_role
from core.database import get_pool
from core.firebase import create_firebase_user, delete_firebase_user
from core.schemas import (
    AuthContext,
    TenantCreate, TenantUpdate, TenantResponse,
    UserResponse,
)
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime, timedelta
from decimal import Decimal
import uuid

router = APIRouter(prefix="/superadmin", tags=["SuperAdmin"])



class TenantStats(BaseModel):
    tenant_id:    str
    name:         str
    logo_url:     Optional[str] = None
    currency:     str
    tone:         str
    admin_count:  int
    client_count: int
    total_users:  int
    created_at:   Optional[datetime] = None

class GlobalStats(BaseModel):
    total_tenants:       int
    total_users:         int
    total_admins:        int
    total_clients:       int
    total_sub_admins:    int
    new_users_7d:        int
    new_users_30d:       int
    total_bookings:      int        
    total_tickets:       int        
    avg_rating:          float 
    tenants:             list[TenantStats]

class AdminCreatePayload(BaseModel):
    """Corps du formulaire de création d'un admin (super_admin uniquement)."""
    email:            EmailStr
    password:         str
    first_name:       str
    last_name:        str
    can_create_admin: bool = False



@router.get("/stats", response_model=GlobalStats)
async def get_global_stats(
    _: AuthContext = Depends(require_role("super_admin"))
):
    """
    Dashboard super_admin — statistiques globales.
    Aggrège tenants + user_tenants depuis PostgreSQL.
    """
    pool = await get_pool()

    tenant_rows = await pool.fetch("""
        SELECT
            t.tenant_id::text,
            t.name,
            t.logo_url,
            t.currency,
            t.tone,
            t.created_at,
            COUNT(ut.user_id) FILTER (WHERE ut.role = 'admin')
                AS admin_count,
            COUNT(ut.user_id) FILTER (WHERE ut.role = 'sub_admin')
                AS sub_admin_count,
            COUNT(ut.user_id) FILTER (WHERE ut.role = 'client')
                AS client_count,
            COUNT(ut.user_id) AS total_users
        FROM tenants t
        LEFT JOIN user_tenants ut
            ON ut.tenant_id = t.tenant_id AND ut.is_active = TRUE
        GROUP BY t.tenant_id
        ORDER BY t.created_at DESC
    """)

    tenants_stats = [
        TenantStats(
            tenant_id=str(r["tenant_id"]),
            name=r["name"],
            logo_url=r.get("logo_url"),
            currency=r["currency"],
            tone=r["tone"],
            admin_count=r["admin_count"] or 0,
            client_count=r["client_count"] or 0,
            total_users=r["total_users"] or 0,
            created_at=r.get("created_at"),
        )
        for r in tenant_rows
    ]

    totals = await pool.fetchrow("""
        SELECT
            COUNT(DISTINCT t.tenant_id)                                      AS total_tenants,
            COUNT(DISTINCT u.user_id)                                        AS total_users,
            COUNT(DISTINCT ut.user_id) FILTER (WHERE ut.role = 'admin')      AS total_admins,
            COUNT(DISTINCT ut.user_id) FILTER (WHERE ut.role = 'sub_admin')  AS total_sub_admins,
            COUNT(DISTINCT ut.user_id) FILTER (WHERE ut.role = 'client')     AS total_clients
        FROM tenants t
        LEFT JOIN user_tenants ut ON ut.tenant_id = t.tenant_id
        LEFT JOIN users u         ON u.user_id     = ut.user_id
    """)

    now = datetime.utcnow()
    new_7d  = await pool.fetchval(
        "SELECT COUNT(*) FROM users WHERE created_at >= $1", now - timedelta(days=7)
    )
    new_30d = await pool.fetchval(
        "SELECT COUNT(*) FROM users WHERE created_at >= $1", now - timedelta(days=30)
    )

    avg_rating = await pool.fetchval(
        "SELECT ROUND(AVG(rating)::numeric, 1) FROM tenant_reviews"
    ) or 0.0

    total_bookings = await pool.fetchval(
        "SELECT COUNT(*) FROM user_bookings WHERE status = 'completed'"
    ) or 0

    total_tickets = await pool.fetchval(
        "SELECT COUNT(*) FROM support_tickets"
    ) or 0

    return GlobalStats(
        total_tenants=totals["total_tenants"]       or 0,
        total_users=totals["total_users"]           or 0,
        total_admins=totals["total_admins"]         or 0,
        total_sub_admins=totals["total_sub_admins"] or 0,
        total_clients=totals["total_clients"]       or 0,
        new_users_7d=new_7d                         or 0,
        new_users_30d=new_30d                       or 0,
        total_bookings=int(total_bookings),
        total_tickets=int(total_tickets),
        avg_rating=float(avg_rating),
        tenants=tenants_stats,
    )



@router.get("/tenants", response_model=list[TenantResponse])
async def list_tenants(
    _: AuthContext = Depends(require_role("super_admin"))
):
    """Liste tous les tenants."""
    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT tenant_id, name, logo_url, currency, margin_percentage, tone, facebook_page_id, facebook_page_token,
               created_at, updated_at
        FROM tenants
        ORDER BY created_at DESC
    """)
    return [TenantResponse(**dict(r), api_keys=[], files=[], subcategory_configs=[]) for r in rows]


@router.post("/tenants", response_model=TenantResponse, status_code=201)
async def create_tenant(
    payload: TenantCreate,
    _: AuthContext = Depends(require_role("super_admin"))
):
    pool = await get_pool()
    new_id = uuid.uuid4()
    row = await pool.fetchrow("""
        INSERT INTO tenants (tenant_id, name, logo_url, currency, margin_percentage, tone, facebook_page_id,facebook_page_token)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING tenant_id, name, logo_url, currency, margin_percentage, tone,
                  facebook_page_id, created_at, updated_at
    """,
        new_id,
        payload.name,
        payload.logo_url,
        payload.currency,
        payload.margin_percentage,
        payload.tone,
        payload.facebook_page_id,
        payload.facebook_page_token
    )
    return TenantResponse(**dict(row), api_keys=[], files=[], subcategory_configs=[])


@router.get("/tenants/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: str,
    _: AuthContext = Depends(require_role("super_admin"))
):
    pool = await get_pool()
    row = await pool.fetchrow("""
        SELECT tenant_id, name, logo_url, currency, margin_percentage, tone, facebook_page_id,facebook_page_token,
               created_at, updated_at
        FROM tenants WHERE tenant_id = $1
    """, uuid.UUID(tenant_id))
    if not row:
        raise HTTPException(status_code=404, detail="TENANT_NOT_FOUND")
    return TenantResponse(**dict(row), api_keys=[], files=[], subcategory_configs=[])


@router.patch("/tenants/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: str,
    payload: TenantUpdate,
    _: AuthContext = Depends(require_role("super_admin"))
):
    pool = await get_pool()
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="Aucun champ à mettre à jour")

    set_parts = [f"{col} = ${i+2}" for i, col in enumerate(updates)]
    values    = list(updates.values())
    row = await pool.fetchrow(
        f"""
        UPDATE tenants
        SET {', '.join(set_parts)}, updated_at = NOW()
        WHERE tenant_id = $1
        RETURNING tenant_id, name, logo_url, currency, margin_percentage, tone, facebook_page_id,facebook_page_token,
                  created_at, updated_at
        """,
        uuid.UUID(tenant_id), *values
    )
    if not row:
        raise HTTPException(status_code=404, detail="TENANT_NOT_FOUND")
    return TenantResponse(**dict(row), api_keys=[], files=[], subcategory_configs=[])


@router.delete("/tenants/{tenant_id}", status_code=204)
async def delete_tenant(
    tenant_id: str,
    _: AuthContext = Depends(require_role("super_admin"))
):
    """
    Supprime un tenant. CASCADE supprime user_tenants, api_keys, files, etc.
    Les utilisateurs sans autre tenant sont ensuite supprimés manuellement.
    """
    pool = await get_pool()

    orphan_uids = await pool.fetch("""
        SELECT u.firebase_uid
        FROM users u
        JOIN user_tenants ut ON ut.user_id = u.user_id
        WHERE ut.tenant_id = $1
          AND (
            SELECT COUNT(*) FROM user_tenants ut2
            WHERE ut2.user_id = u.user_id
          ) = 1
    """, uuid.UUID(tenant_id))

    result = await pool.execute(
        "DELETE FROM tenants WHERE tenant_id = $1", uuid.UUID(tenant_id)
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="TENANT_NOT_FOUND")

    for r in orphan_uids:
        try:
            delete_firebase_user(r["firebase_uid"])
        except Exception:
            pass



@router.get("/tenants/{tenant_id}/admins", response_model=list[UserResponse])
async def list_tenant_admins(
    tenant_id: str,
    _: AuthContext = Depends(require_role("super_admin"))
):
    """Liste tous les admins et sub_admins d'un tenant donné."""
    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT
            u.user_id, u.firebase_uid, ut.tenant_id,
            u.email, u.first_name, u.last_name,
            ut.role, ut.can_create_admin, ut.is_active, u.created_at
        FROM users u
        JOIN user_tenants ut ON ut.user_id = u.user_id
        WHERE ut.tenant_id = $1
          AND ut.role IN ('admin', 'sub_admin')
        ORDER BY u.created_at DESC
    """, uuid.UUID(tenant_id))
    return [UserResponse(**dict(r)) for r in rows]


@router.post("/tenants/{tenant_id}/admins", response_model=UserResponse, status_code=201)
async def create_tenant_admin(
    tenant_id: str,
    payload: AdminCreatePayload,
    _: AuthContext = Depends(require_role("super_admin"))
):
    """
    Le super_admin crée un admin pour un tenant donné.
    Flux : Firebase Auth → users → user_tenants (role='admin').
    Rollback Firebase si PostgreSQL échoue.
    """
    pool = await get_pool()

    tenant = await pool.fetchrow(
        "SELECT tenant_id FROM tenants WHERE tenant_id = $1", uuid.UUID(tenant_id)
    )
    if not tenant:
        raise HTTPException(status_code=404, detail="TENANT_NOT_FOUND")

    try:
        firebase_uid = create_firebase_user(payload.email, payload.password)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"FIREBASE_ERROR: {str(e)}")

    try:
        user_row = await pool.fetchrow("""
            INSERT INTO users (firebase_uid, email, first_name, last_name)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (firebase_uid) DO UPDATE
                SET email      = EXCLUDED.email,
                    first_name = EXCLUDED.first_name,
                    last_name  = EXCLUDED.last_name
            RETURNING user_id, firebase_uid, email, first_name, last_name, created_at
        """, firebase_uid, payload.email, payload.first_name, payload.last_name)

        await pool.execute("""
            INSERT INTO user_tenants (user_id, tenant_id, role, can_create_admin)
            VALUES ($1, $2, 'admin', $3)
            ON CONFLICT (user_id, tenant_id) DO UPDATE
                SET role             = 'admin',
                    can_create_admin = EXCLUDED.can_create_admin,
                    is_active        = TRUE
        """, user_row["user_id"], uuid.UUID(tenant_id), payload.can_create_admin)

    except Exception as e:
        try:
            delete_firebase_user(firebase_uid)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"DB_ERROR: {str(e)}")

    return UserResponse(
        user_id=user_row["user_id"],
        firebase_uid=user_row["firebase_uid"],
        tenant_id=uuid.UUID(tenant_id),
        email=user_row["email"],
        first_name=user_row["first_name"],
        last_name=user_row["last_name"],
        role="admin",
        can_create_admin=payload.can_create_admin,
        is_active=True,
        created_at=user_row["created_at"],
    )


@router.delete("/tenants/{tenant_id}/admins/{user_id}", status_code=204)
async def delete_tenant_admin(
    tenant_id: str,
    user_id:   str,
    _: AuthContext = Depends(require_role("super_admin"))
):
    """Supprime le lien admin ↔ tenant. Supprime le user Firebase s'il devient orphelin."""
    pool = await get_pool()

    row = await pool.fetchrow("""
        SELECT u.firebase_uid
        FROM users u
        JOIN user_tenants ut ON ut.user_id = u.user_id
        WHERE u.user_id = $1 AND ut.tenant_id = $2
    """, uuid.UUID(user_id), uuid.UUID(tenant_id))

    if not row:
        raise HTTPException(status_code=404, detail="USER_NOT_FOUND")

    await pool.execute(
        "DELETE FROM user_tenants WHERE user_id = $1 AND tenant_id = $2",
        uuid.UUID(user_id), uuid.UUID(tenant_id)
    )

    remaining = await pool.fetchval(
        "SELECT COUNT(*) FROM user_tenants WHERE user_id = $1", uuid.UUID(user_id)
    )
    if remaining == 0:
        await pool.execute("DELETE FROM users WHERE user_id = $1", uuid.UUID(user_id))
        try:
            delete_firebase_user(row["firebase_uid"])
        except Exception:
            pass



@router.get("/users", response_model=list[UserResponse])
async def list_all_users(
    _: AuthContext = Depends(require_role("super_admin"))
):
    """Tous les utilisateurs de tous les tenants (limité à 1 000)."""
    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT
            u.user_id, u.firebase_uid, ut.tenant_id,
            u.email, u.first_name, u.last_name,
            ut.role, ut.can_create_admin, ut.is_active, u.created_at
        FROM users u
        JOIN user_tenants ut ON ut.user_id = u.user_id
        ORDER BY u.created_at DESC
        LIMIT 1000
    """)
    return [UserResponse(**dict(r)) for r in rows]

@router.get("/tenants/{tenant_id}/facebook/status")
async def check_facebook_status(
    tenant_id: str,
    _: AuthContext = Depends(require_role("super_admin"))
):
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT facebook_page_id, facebook_page_token FROM tenants WHERE tenant_id = $1",
        uuid.UUID(tenant_id)
    )
    if not row:
        raise HTTPException(404, "TENANT_NOT_FOUND")
    
    return {
        "page_id_configured":    bool(row["facebook_page_id"]),
        "token_configured":      bool(row["facebook_page_token"]),
        "ready":                 bool(row["facebook_page_id"]) and bool(row["facebook_page_token"]),
        "page_id":               row["facebook_page_id"],
        "token_preview":         row["facebook_page_token"][:10] + "..." if row["facebook_page_token"] else None
    }