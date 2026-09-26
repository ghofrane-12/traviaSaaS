# routers/admin.py

from fastapi import APIRouter, HTTPException, Depends
from methods.auth import get_current_user, require_role, require_min_role
from core.database import get_pool
from core.firebase import create_firebase_user, delete_firebase_user
from core.schemas import AuthContext, UserCreate, UserUpdate, UserResponse
import uuid

router = APIRouter(prefix="/admin", tags=["Admin"])



def _can_create_role(creator: AuthContext, target_role: str) -> bool:
    """
    Vérifie si le créateur a le droit de créer un utilisateur
    avec le rôle cible selon la matrice de permissions.
    """
    if creator.role == "super_admin":
        return target_role in ("admin", "sub_admin", "client")

    if creator.role == "admin":
        if creator.can_create_admin:
            return target_role in ("admin", "sub_admin", "client")
        else:
            return target_role in ("sub_admin", "client")

    if creator.role == "sub_admin":
        return target_role == "client"

    return False


# =========================================================================
# LISTER LES UTILISATEURS DU TENANT
# =========================================================================

@router.get("/users", response_model=list[UserResponse])
async def list_users(
    user: AuthContext = Depends(require_min_role("sub_admin"))
):
    """
    Liste les utilisateurs du tenant courant.
    - super_admin : voit tous les tenants
    - admin / sub_admin : voit uniquement son tenant
    """
    pool = await get_pool()

    if user.role == "super_admin":
        rows = await pool.fetch(
            """
            SELECT
                u.user_id, u.firebase_uid, ut.tenant_id,
                u.email, u.first_name, u.last_name,
                ut.role, ut.can_create_admin, ut.is_active,
                u.created_at
            FROM users u
            JOIN user_tenants ut ON ut.user_id = u.user_id
            ORDER BY u.created_at DESC
            """
        )
    else:
        rows = await pool.fetch(
            """
            SELECT
                u.user_id, u.firebase_uid, ut.tenant_id,
                u.email, u.first_name, u.last_name,
                ut.role, ut.can_create_admin, ut.is_active,
                u.created_at
            FROM users u
            JOIN user_tenants ut ON ut.user_id = u.user_id
            WHERE ut.tenant_id = $1
            ORDER BY u.created_at DESC
            """,
            uuid.UUID(user.tenant_id)
        )

    return [UserResponse(**dict(r)) for r in rows]


# =========================================================================
# CRÉER UN UTILISATEUR
# =========================================================================

@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    payload: UserCreate,
    creator: AuthContext = Depends(require_min_role("sub_admin"))
):
    """
    Création atomique : Firebase Auth → users → user_tenants.
    Rollback Firebase si PostgreSQL échoue.

    Permissions :
    - super_admin → peut créer admin, sub_admin, client
    - admin (can_create_admin=True) → peut créer admin, sub_admin, client
    - admin (can_create_admin=False) → peut créer sub_admin, client
    - sub_admin → peut créer client uniquement
    """
    if not _can_create_role(creator, payload.role):
        raise HTTPException(
            status_code=403,
            detail=f"Vous n'avez pas le droit de créer un utilisateur avec le rôle '{payload.role}'"
        )

    can_create_admin = payload.can_create_admin
    if can_create_admin and creator.role not in ("super_admin",) and not creator.can_create_admin:
        can_create_admin = False

    try:
        firebase_uid = create_firebase_user(payload.email, payload.password)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"FIREBASE_ERROR: {str(e)}")

    pool = await get_pool()
    try:
        user_row = await pool.fetchrow(
            """
            INSERT INTO users (firebase_uid, email, first_name, last_name)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (firebase_uid) DO UPDATE
                SET email = EXCLUDED.email
            RETURNING user_id, firebase_uid, email, first_name, last_name, created_at
            """,
            firebase_uid,
            payload.email,
            payload.first_name,
            payload.last_name,
        )

        tenant_id = uuid.UUID(creator.tenant_id)
        await pool.execute(
            """
            INSERT INTO user_tenants (user_id, tenant_id, role, can_create_admin)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, tenant_id) DO UPDATE
                SET role = EXCLUDED.role,
                    can_create_admin = EXCLUDED.can_create_admin
            """,
            user_row["user_id"],
            tenant_id,
            payload.role,
            can_create_admin,
        )

    except Exception as e:
        try:
            delete_firebase_user(firebase_uid)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"DB_ERROR: {str(e)}")

    return UserResponse(
        user_id=user_row["user_id"],
        firebase_uid=user_row["firebase_uid"],
        tenant_id=tenant_id,
        email=user_row["email"],
        first_name=user_row["first_name"],
        last_name=user_row["last_name"],
        role=payload.role,
        can_create_admin=can_create_admin,
        is_active=True,
        created_at=user_row["created_at"],
    )


# =========================================================================
# MODIFIER UN UTILISATEUR
# =========================================================================

@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    payload: UserUpdate,
    admin: AuthContext = Depends(require_min_role("admin"))
):
    """
    Modifier le rôle, le statut ou les informations d'un utilisateur.
    Un admin ne peut pas modifier un super_admin.
    """
    pool = await get_pool()

    existing = await pool.fetchrow(
        """
        SELECT u.user_id, u.firebase_uid, ut.tenant_id, ut.role,
               ut.can_create_admin, ut.is_active, u.email,
               u.first_name, u.last_name, u.created_at
        FROM users u
        JOIN user_tenants ut ON ut.user_id = u.user_id
        WHERE u.user_id = $1 AND ut.tenant_id = $2
        """,
        uuid.UUID(user_id),
        uuid.UUID(admin.tenant_id)
    )

    if not existing:
        raise HTTPException(status_code=404, detail="USER_NOT_FOUND")

    if existing["role"] == "super_admin" and admin.role != "super_admin":
        raise HTTPException(status_code=403, detail="Impossible de modifier un super_admin")

    if payload.role and not _can_create_role(admin, payload.role):
        raise HTTPException(
            status_code=403,
            detail=f"Vous n'avez pas le droit d'assigner le rôle '{payload.role}'"
        )

    user_updates = {}
    if payload.first_name: user_updates["first_name"] = payload.first_name
    if payload.last_name:  user_updates["last_name"]  = payload.last_name

    if user_updates:
        set_clause = ", ".join(f"{col} = ${i+2}" for i, col in enumerate(user_updates))
        await pool.execute(
            f"UPDATE users SET {set_clause} WHERE user_id = $1",
            uuid.UUID(user_id), *user_updates.values()
        )

    ut_updates = {}
    if payload.role      is not None: ut_updates["role"]             = payload.role
    if payload.is_active is not None: ut_updates["is_active"]        = payload.is_active
    if payload.can_create_admin is not None:
        ut_updates["can_create_admin"] = payload.can_create_admin

    if ut_updates:
        set_clause = ", ".join(f"{col} = ${i+3}" for i, col in enumerate(ut_updates))
        await pool.execute(
            f"UPDATE user_tenants SET {set_clause} WHERE user_id = $1 AND tenant_id = $2",
            uuid.UUID(user_id), uuid.UUID(admin.tenant_id), *ut_updates.values()
        )

    updated = await pool.fetchrow(
        """
        SELECT u.user_id, u.firebase_uid, ut.tenant_id, ut.role,
               ut.can_create_admin, ut.is_active, u.email,
               u.first_name, u.last_name, u.created_at
        FROM users u
        JOIN user_tenants ut ON ut.user_id = u.user_id
        WHERE u.user_id = $1 AND ut.tenant_id = $2
        """,
        uuid.UUID(user_id), uuid.UUID(admin.tenant_id)
    )

    return UserResponse(**dict(updated))


# =========================================================================
# SUPPRIMER UN UTILISATEUR
# =========================================================================

@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    admin: AuthContext = Depends(require_min_role("admin"))
):
    """
    Supprime le lien user_tenants (isolation tenant).
    Si l'utilisateur n'appartient plus à aucun tenant → suppression Firebase + users.
    """
    pool = await get_pool()

    row = await pool.fetchrow(
        """
        SELECT u.firebase_uid, ut.role
        FROM users u
        JOIN user_tenants ut ON ut.user_id = u.user_id
        WHERE u.user_id = $1 AND ut.tenant_id = $2
        """,
        uuid.UUID(user_id), uuid.UUID(admin.tenant_id)
    )

    if not row:
        raise HTTPException(status_code=404, detail="USER_NOT_FOUND")

    if row["role"] == "super_admin" and admin.role != "super_admin":
        raise HTTPException(status_code=403, detail="Impossible de supprimer un super_admin")

    await pool.execute(
        "DELETE FROM user_tenants WHERE user_id = $1 AND tenant_id = $2",
        uuid.UUID(user_id), uuid.UUID(admin.tenant_id)
    )

    remaining = await pool.fetchval(
        "SELECT COUNT(*) FROM user_tenants WHERE user_id = $1",
        uuid.UUID(user_id)
    )

    if remaining == 0:
        try:
            delete_firebase_user(row["firebase_uid"])
        except Exception:
            pass
        await pool.execute("DELETE FROM users WHERE user_id = $1", uuid.UUID(user_id))


# =========================================================================
# ENDPOINT SUPER ADMIN — créer un admin pour un tenant spécifique
# =========================================================================

@router.post("/tenants/{tenant_id}/admins", response_model=UserResponse, status_code=201)
async def create_tenant_admin(
    tenant_id: str,
    payload: UserCreate,
    super_admin: AuthContext = Depends(require_role("super_admin"))
):
    """
    Réservé au super_admin.
    Crée un admin dans n'importe quel tenant.
    """
    if payload.role not in ("admin", "sub_admin"):
        raise HTTPException(
            status_code=422,
            detail="Ce endpoint crée uniquement des admins ou sub_admins"
        )

    try:
        firebase_uid = create_firebase_user(payload.email, payload.password)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"FIREBASE_ERROR: {str(e)}")

    pool = await get_pool()
    try:
        user_row = await pool.fetchrow(
            """
            INSERT INTO users (firebase_uid, email, first_name, last_name)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (firebase_uid) DO UPDATE SET email = EXCLUDED.email
            RETURNING user_id, firebase_uid, email, first_name, last_name, created_at
            """,
            firebase_uid, payload.email, payload.first_name, payload.last_name
        )

        await pool.execute(
            """
            INSERT INTO user_tenants (user_id, tenant_id, role, can_create_admin)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, tenant_id) DO UPDATE
                SET role = EXCLUDED.role,
                    can_create_admin = EXCLUDED.can_create_admin
            """,
            user_row["user_id"],
            uuid.UUID(tenant_id),
            payload.role,
            payload.can_create_admin,
        )

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
        role=payload.role,
        can_create_admin=payload.can_create_admin,
        is_active=True,
        created_at=user_row["created_at"],
    )
# =========================================================================
# FACEBOOK STATUS — accessible par l'admin du tenant
# =========================================================================

@router.get("/tenants/{tenant_id}/facebook/status")
async def check_facebook_status(
    tenant_id: str,
    auth: AuthContext = Depends(require_min_role("admin"))
):
    """
    Vérifie si la page Facebook est connectée pour ce tenant.
    Accessible par l'admin du tenant uniquement.
    """
    pool = await get_pool()

    if auth.role != "super_admin" and auth.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="FORBIDDEN")

    row = await pool.fetchrow(
        "SELECT facebook_page_id, facebook_page_token FROM tenants WHERE tenant_id = $1",
        uuid.UUID(tenant_id)
    )
    if not row:
        raise HTTPException(status_code=404, detail="TENANT_NOT_FOUND")

    return {
        "ready":       bool(row["facebook_page_id"]) and bool(row["facebook_page_token"]),
        "page_id":     row["facebook_page_id"],
        "token_preview": row["facebook_page_token"][:10] + "..." if row["facebook_page_token"] else None
    }