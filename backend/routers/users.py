# routers/users.py
from fastapi import APIRouter, Depends, HTTPException
from methods.auth import get_current_user
from core.database import get_pool
from core.firebase import send_password_reset
from core.schemas import AuthContext, UserResponse
import uuid

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me", response_model=UserResponse)
async def get_profile(user: AuthContext = Depends(get_current_user)):
    """
    Retourne le profil de l'utilisateur connecté avec son rôle
    dans son tenant courant.
    """
    if user.is_anonymous:
        raise HTTPException(status_code=401, detail="Connectez-vous pour accéder à votre profil")

    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT
            u.user_id, u.firebase_uid, ut.tenant_id,
            u.email, u.first_name, u.last_name,
            ut.role, ut.can_create_admin, ut.is_active,
            u.created_at
        FROM users u
        JOIN user_tenants ut ON ut.user_id = u.user_id
        WHERE u.user_id = $1 AND ut.tenant_id = $2
        """,
        uuid.UUID(user.user_id),
        uuid.UUID(user.tenant_id)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Profil introuvable")

    return UserResponse(**dict(row))


@router.post("/password-reset")
async def request_password_reset(email: str):
    """
    Déclenche l'email reset Firebase.
    Pas d'authentification requise.
    Toujours retourner 200 pour éviter l'énumération d'emails.
    """
    try:
        send_password_reset(email)
    except Exception:
        pass
    return {"message": "Si ce compte existe, un email a été envoyé."}