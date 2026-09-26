# core/firebase.py
import firebase_admin
from firebase_admin import credentials, auth as firebase_auth, firestore
from core.config import settings
import os
from typing import Optional
from fastapi import HTTPException
from datetime import datetime, timedelta
import asyncio

if not firebase_admin._apps:
    if settings.FIREBASE_CREDENTIALS_PATH:
        cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
        firebase_admin.initialize_app(cred)
    else:
        firebase_admin.initialize_app(options={
            'projectId': 'travel-saas-pfe'
        })

db = firestore.client()

# ── 4 rôles possibles ──
ROLES = {
    "superadmin": 4,
    "admin": 3,
    "user": 2,
    "visiteur": 1
}

def verify_id_token(id_token: str) -> dict:
    try:
        return firebase_auth.verify_id_token(id_token, clock_skew_seconds=5)
    except firebase_auth.ExpiredIdTokenError as e:
        print(f"[FIREBASE] ❌ Expiré: {e}")
        raise ValueError("TOKEN_EXPIRED")
    except firebase_auth.InvalidIdTokenError as e:
        print(f"[FIREBASE] ❌ Invalide: {e}")
        raise ValueError("TOKEN_INVALID")
    except Exception as e:
        print(f"[FIREBASE] ❌ Autre erreur: {type(e).__name__}: {e}")
        raise ValueError("TOKEN_ERROR")

def verify_token(token: str) -> dict:
    """Alias pour verify_id_token (compatibilité)"""
    return verify_id_token(token)

def create_firebase_user(email: str, password: str) -> str:
    """Crée un user Firebase, retourne le firebase_uid."""
    user = firebase_auth.create_user(email=email, password=password)
    return user.uid

def delete_firebase_user(firebase_uid: str) -> None:
    firebase_auth.delete_user(firebase_uid)

def send_password_reset(email: str) -> None:
    """Déclenche l'email reset depuis Firebase."""
    firebase_auth.generate_password_reset_link(email)

def get_user_role(decoded_token: dict) -> str:
    """Retourne le rôle depuis les custom claims"""
    claims = decoded_token.get("custom_claims", {})
    return claims.get("role", "user")

def set_user_role(uid: str, role: str):
    """Définit le rôle d'un utilisateur (appelé par superadmin/admin)."""
    if role not in ROLES:
        raise HTTPException(status_code=400, detail=f"Rôle invalide. Choisir parmi : {list(ROLES.keys())}")
    try:
        firebase_auth.set_custom_user_claims(uid, {"role": role})
        return {"message": f"Rôle '{role}' assigné à l'utilisateur {uid}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_all_users():
    """Retourne tous les utilisateurs (superadmin uniquement)."""
    users = []
    page = firebase_auth.list_users()
    while page:
        for user in page.users:
            users.append({
                "uid": user.uid,
                "email": user.email,
                "display_name": user.display_name,
                "disabled": user.disabled,
                "role": user.custom_claims.get("role", "user") if user.custom_claims else "user"
            })
        page = page.get_next_page()
    return users

async def delete_anonymous_users_older_than(hours: int = 1):
    try:
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        deleted_count = 0

        def _delete_sync():
            count = 0
            for user in firebase_auth.list_users().iterate_all():
                is_anonymous = (
                    not user.provider_data
                    and not user.email
                    and not user.phone_number
                    and not user.display_name
                )
                if not is_anonymous:
                    continue

                timestamp = (
                    user.user_metadata.last_sign_in_timestamp
                    or user.user_metadata.creation_timestamp
                )

                if timestamp is None:
                    firebase_auth.delete_user(user.uid)
                    count += 1
                    continue

                user_date = datetime.utcfromtimestamp(timestamp / 1000)
                if user_date < cutoff_time:
                    firebase_auth.delete_user(user.uid)
                    count += 1
                    print(f"🗑️ Supprimé: {user.uid[:20]}...")

            return count

        deleted_count = await asyncio.to_thread(_delete_sync)
        print(f"✅ Firebase Auth nettoyage: {deleted_count} comptes supprimés")
        return deleted_count

    except Exception as e:
        print(f"❌ Erreur nettoyage Firebase Auth: {e}")
        import traceback
        traceback.print_exc()
        return 0
    
def generate_email_verification_link(email: str, tenant_id: str, frontend_url: str) -> str:
    """
    Génère un lien de vérification email via Firebase Admin SDK.
    Ce lien redirige vers Firebase qui vérifie le token,
    puis redirige vers continueUrl.
    """
    action_code_settings = firebase_auth.ActionCodeSettings(
        url=f"{frontend_url}/verify-email?tenant={tenant_id}&verified=true",
        handle_code_in_app=False,
    )
    return firebase_auth.generate_email_verification_link(
        email, action_code_settings=action_code_settings
    )
def is_email_verified(firebase_uid: str) -> bool:
    """Vérifie si l'email d'un user Firebase est confirmé."""
    try:
        user = firebase_auth.get_user(firebase_uid)
        return user.email_verified
    except Exception:
        return False