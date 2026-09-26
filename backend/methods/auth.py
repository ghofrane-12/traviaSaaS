# methods/auth.py
from fastapi import Depends, HTTPException, status, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
from core.firebase import verify_id_token
from core.database import get_pool
from core.schemas import AuthContext
import uuid

bearer_scheme = HTTPBearer(auto_error=False)  

VISITOR_NAMESPACE = uuid.UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')
DEFAULT_TENANT_ID = "a1b2c3d4-0001-0000-0000-000000000001"

ROLE_HIERARCHY = {
    "super_admin": 4,
    "admin":       3,
    "sub_admin":   2,
    "client":      1,
    "visitor":     0,
}


def firebase_uid_to_uuid(firebase_uid: str) -> str:
    return str(uuid.uuid5(VISITOR_NAMESPACE, f"visitor_{firebase_uid}"))


def _resolve_tenant_id(
    x_tenant_id: Optional[str],
    fallback: str = DEFAULT_TENANT_ID
) -> str:
    """
    Résout le tenant_id dans cet ordre :
    1. Header X-Tenant-ID envoyé par le frontend
    2. Fallback par défaut
    """
    if x_tenant_id and x_tenant_id.strip():
        try:
            uuid.UUID(x_tenant_id.strip())
            return x_tenant_id.strip()
        except ValueError:
            pass
    return fallback


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    x_tenant_id: Optional[str] = Header(None),  
    authorization: Optional[str] = Header(None)
) -> AuthContext:

    token = None
    if credentials:
        token = credentials.credentials
    elif authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "")

    if not token:
        tenant_id = _resolve_tenant_id(x_tenant_id)
        return AuthContext(
            user_id="anonymous",
            tenant_id=tenant_id, 
            role="visitor",
            is_active=True,
            email=None,
            first_name="Visiteur",
            last_name="",
            firebase_uid=None,
            can_create_admin=False,
        )

    try:
        decoded = verify_id_token(token)
    except ValueError:
        tenant_id = _resolve_tenant_id(x_tenant_id)
        return AuthContext(
            user_id="anonymous",
            tenant_id=tenant_id,  
            role="visitor",
            is_active=True,
            email=None,
            first_name="Visiteur",
            last_name="",
            firebase_uid=None,
            can_create_admin=False,
        )

    firebase_uid = decoded["uid"]
    is_anonymous = decoded.get("firebase", {}).get("sign_in_provider") == "anonymous"

    if is_anonymous:
        user_uuid = firebase_uid_to_uuid(firebase_uid)
        tenant_id = _resolve_tenant_id(x_tenant_id)  
        return AuthContext(
            user_id=user_uuid,
            tenant_id=tenant_id,
            role="visitor",
            is_active=True,
            email=None,
            first_name="Visiteur",
            last_name="",
            firebase_uid=firebase_uid,
            can_create_admin=False,
        )

    pool = await get_pool()
    target_tenant_id = None
    if x_tenant_id:
        try:
            target_tenant_id = uuid.UUID(x_tenant_id.strip())
        except ValueError:
            pass

    base_row = await pool.fetchrow(
        """
        SELECT
            u.user_id::text,
            u.firebase_uid,
            u.email,
            u.first_name,
            u.last_name,
            ut.tenant_id::text,
            ut.role,
            ut.can_create_admin,
            ut.is_active,
            t.name     AS agency_name,
            t.logo_url AS agency_logo,
            t.currency,
            t.tone
        FROM users u
        JOIN user_tenants ut ON ut.user_id = u.user_id
        JOIN tenants t       ON t.tenant_id = ut.tenant_id
        WHERE u.firebase_uid = $1
        ORDER BY
            CASE ut.role
                WHEN 'super_admin' THEN 4
                WHEN 'admin'       THEN 3
                WHEN 'sub_admin'   THEN 2
                WHEN 'client'      THEN 1
                ELSE 0
            END DESC
        LIMIT 1
        """,
        firebase_uid
    )

    if not base_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé."
        )

    if not base_row["is_active"]:
        raise HTTPException(status_code=403, detail="ACCOUNT_DISABLED")

    if base_row["role"] == "super_admin":
        resolved_tenant = str(target_tenant_id) if target_tenant_id else base_row["tenant_id"]
        return AuthContext(
            user_id=base_row["user_id"],
            tenant_id=resolved_tenant,
            role="super_admin",
            is_active=True,
            email=base_row["email"],
            first_name=base_row["first_name"],
            last_name=base_row["last_name"],
            firebase_uid=base_row["firebase_uid"],
            can_create_admin=True,  
        )

    if target_tenant_id:
        row = await pool.fetchrow(
            """
            SELECT
                u.user_id::text,
                u.firebase_uid,
                u.email,
                u.first_name,
                u.last_name,
                ut.tenant_id::text,
                ut.role,
                ut.can_create_admin,
                ut.is_active,
                t.name     AS agency_name,
                t.logo_url AS agency_logo,
                t.currency,
                t.tone
            FROM users u
            JOIN user_tenants ut ON ut.user_id = u.user_id
            JOIN tenants t       ON t.tenant_id = ut.tenant_id
            WHERE u.firebase_uid = $1
              AND ut.tenant_id   = $2
            """,
            firebase_uid, target_tenant_id
        )
        if not row:
            raise HTTPException(status_code=403, detail="Accès interdit à ce tenant.")
    else:
        row = base_row

    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="ACCOUNT_DISABLED")

    return AuthContext(
        user_id=row["user_id"],
        tenant_id=row["tenant_id"],
        role=row["role"],
        is_active=row["is_active"],
        email=row["email"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        firebase_uid=row["firebase_uid"],
        can_create_admin=row["can_create_admin"],
    )

def require_role(*roles: str):
    """
    Dépendance FastAPI pour vérifier les rôles.

    Usage :
        Depends(require_role("admin", "super_admin"))
        Depends(require_role("super_admin"))
    """
    async def _check(user: AuthContext = Depends(get_current_user)) -> AuthContext:
        if user.role == "visitor":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Connectez-vous pour accéder à cette ressource"
            )
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Rôle requis : {', '.join(roles)}. Votre rôle : {user.role}"
            )
        return user
    return _check


def require_min_role(min_role: str):
    """
    Dépendance basée sur la hiérarchie des rôles.
    Ex: require_min_role("sub_admin") accepte sub_admin, admin, super_admin.
    """
    min_level = ROLE_HIERARCHY.get(min_role, 0)

    async def _check(user: AuthContext = Depends(get_current_user)) -> AuthContext:
        user_level = ROLE_HIERARCHY.get(user.role, 0)
        if user_level < min_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Niveau minimum requis : {min_role}. Votre rôle : {user.role}"
            )
        return user
    return _check