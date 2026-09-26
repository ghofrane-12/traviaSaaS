# routers/authn.py
from fastapi import APIRouter, HTTPException, Depends, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from core.firebase import verify_id_token
from core.database import get_pool
from core.schemas import SessionResponse
import uuid
from datetime import datetime
from typing import Optional
from core.firebase import generate_email_verification_link
from core.email_service import send_verification_email
from core.config import settings
router = APIRouter(prefix="/auth", tags=["Auth"])
bearer_scheme = HTTPBearer(auto_error=False)

def extract_name_from_decoded(decoded: dict) -> tuple:
    given_name  = decoded.get("given_name", "")
    family_name = decoded.get("family_name", "")
    if given_name and family_name:
        return given_name, family_name
    name = decoded.get("name", "")
    if name:
        parts = name.split()
        return parts[0], " ".join(parts[1:]) if len(parts) > 1 else ""
    email = decoded.get("email", "")
    if email:
        return email.split('@')[0], ""
    return "Utilisateur", ""

@router.get("/tenant/{tenant_id}/public")
async def get_tenant_public(tenant_id: str):
    """
    Endpoint public — pas d'auth requise.
    Retourne les infos publiques d'un tenant pour les visiteurs.
    """
    pool = await get_pool()
    try:
        row = await pool.fetchrow(
            """
            SELECT tenant_id, name, logo_url, currency, tone
            FROM tenants
            WHERE tenant_id = $1
            """,
            uuid.UUID(tenant_id)
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="tenant_id invalide")

    if not row:
        raise HTTPException(status_code=404, detail="Tenant introuvable")

    return {
        "tenant_id": str(row["tenant_id"]),
        "agency_name": row["name"],
        "agency_logo": row["logo_url"],
        "currency":    row["currency"],
        "tone":        row["tone"],
    }

@router.post("/session", response_model=SessionResponse)
async def create_session(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    x_tenant_id: Optional[str] = Header(None)
):
    try:
        decoded = verify_id_token(credentials.credentials)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    firebase_uid = decoded["uid"]
    email        = decoded.get("email")
    is_anonymous = decoded.get("firebase", {}).get("sign_in_provider") == "anonymous"

    if is_anonymous:
        raise HTTPException(status_code=403, detail="Les visiteurs anonymes n'ont pas de session.")

    if not email:
        email = f"{firebase_uid}@placeholder.invalid"


    first_name, last_name = extract_name_from_decoded(decoded)
    pool = await get_pool()

    target_tenant_id = None
    if x_tenant_id:
        try:
            target_tenant_id = uuid.UUID(x_tenant_id.strip())
        except ValueError:
            pass

    if not target_tenant_id:
        tenant_row = await pool.fetchrow(
            "SELECT tenant_id FROM tenants LIMIT 1"
        )
        if not tenant_row:
            raise HTTPException(status_code=500, detail="Aucun tenant disponible")
        target_tenant_id = tenant_row["tenant_id"]

    tenant_row = await pool.fetchrow(
        """
        SELECT tenant_id, name, logo_url, currency, tone
        FROM tenants WHERE tenant_id = $1
        """,
        target_tenant_id
    )
    if not tenant_row:
        raise HTTPException(status_code=404, detail="Tenant introuvable")

    user_row = await pool.fetchrow(
        "SELECT user_id, email, first_name, last_name FROM users WHERE firebase_uid = $1",
        firebase_uid
    )

    if not user_row:
        user_id = uuid.uuid4()
        await pool.execute(
            """
            INSERT INTO users (user_id, firebase_uid, email, first_name, last_name)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (firebase_uid) DO NOTHING
            """,
            user_id, firebase_uid, email, first_name, last_name
        )
        user_row = await pool.fetchrow(
            "SELECT user_id, email, first_name, last_name FROM users WHERE firebase_uid = $1",
            firebase_uid
        )

    user_id = user_row["user_id"]

    ut_row = await pool.fetchrow(
        """
        SELECT ut.id, ut.tenant_id, ut.role, ut.can_create_admin, ut.is_active
        FROM user_tenants ut
        WHERE ut.user_id = $1 AND ut.tenant_id = $2
        """,
        user_id, target_tenant_id 
    )

    if not ut_row:
        await pool.execute(
            """
            INSERT INTO user_tenants (user_id, tenant_id, role, can_create_admin)
            VALUES ($1, $2, 'client', FALSE)
            ON CONFLICT (user_id, tenant_id) DO NOTHING
            """,
            user_id, target_tenant_id
        )

        ut_row = await pool.fetchrow(
            """
            SELECT ut.id, ut.tenant_id, ut.role, ut.can_create_admin, ut.is_active
            FROM user_tenants ut
            WHERE ut.user_id = $1 AND ut.tenant_id = $2
            """,
            user_id, target_tenant_id
        )

    if not ut_row["is_active"]:
        raise HTTPException(status_code=403, detail="ACCOUNT_DISABLED")

    try:
        await pool.execute(
            "UPDATE users SET updated_at = $1 WHERE user_id = $2",
            datetime.utcnow(), user_id
        )
    except Exception:
        pass

    return SessionResponse(
    user_id=user_id,
    firebase_uid=firebase_uid,
    tenant_id=ut_row["tenant_id"],
    email=email or user_row["email"] or f"{firebase_uid}@placeholder.invalid",
    first_name=first_name or user_row["first_name"],
    last_name=last_name or user_row["last_name"],
    role=ut_row["role"],
    can_create_admin=ut_row["can_create_admin"],
    is_active=ut_row["is_active"],
    agency_name=tenant_row["name"],
    agency_logo=tenant_row["logo_url"],
    currency=tenant_row["currency"],
    tone=tenant_row["tone"],
)

@router.post("/send-verification")
async def send_verification(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    x_tenant_id: Optional[str] = Header(None)
):
    try:
        decoded = verify_id_token(credentials.credentials)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    email = decoded.get("email")
    
    if not email:
        return {"message": "Vérification non requise pour ce type de compte."}
    
    if decoded.get("email_verified", False):
        return {"message": "Email déjà vérifié."}

    tenant_id = x_tenant_id or ""
    pool = await get_pool()

    try:
        target_uuid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="tenant_id invalide")

    tenant_row = await pool.fetchrow(
        "SELECT name, logo_url FROM tenants WHERE tenant_id = $1",
        target_uuid
    )
    if not tenant_row:
        raise HTTPException(status_code=404, detail="Tenant introuvable")

    link = generate_email_verification_link(email, tenant_id, settings.FRONTEND_URL)

    send_verification_email(
        to_email=email,
        display_name=decoded.get("name", email),
        verification_link=link,
        agency_name=tenant_row["name"],
        agency_logo=tenant_row["logo_url"],
        tenant_id=tenant_id,
    )

    return {"message": "Email de vérification envoyé."}