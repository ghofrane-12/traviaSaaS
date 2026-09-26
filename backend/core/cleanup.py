from core.database import get_pool
from core.firebase import is_email_verified, delete_firebase_user
from datetime import datetime, timedelta

async def cleanup_unverified_users(hours: int = 24):
    """
    Supprime les users email/mdp qui n'ont pas vérifié leur email
    après X heures. Les comptes Google sont ignorés (toujours vérifiés).
    """
    print(f"🧹 Nettoyage des comptes non vérifiés (>{hours}h)...")
    pool = await get_pool()

    cutoff = datetime.utcnow() - timedelta(hours=hours)


    rows = await pool.fetch(
    """
    SELECT u.user_id::text, u.firebase_uid, u.email
    FROM users u
    WHERE u.created_at < $1
      AND NOT EXISTS (
          SELECT 1 FROM user_tenants ut
          WHERE ut.user_id = u.user_id
            AND ut.role IN ('admin', 'sub_admin', 'super_admin')
      )
    """,
    cutoff
    )

    deleted_count = 0
    for row in rows:
        firebase_uid = row["firebase_uid"]

        try:
            from firebase_admin import auth as firebase_auth
            user = firebase_auth.get_user(firebase_uid)

            if user.email_verified:
                continue

            delete_firebase_user(firebase_uid)

        except Exception:
            print(f"⚠️ Orphelin Firebase, nettoyage DB: {row['email']}")

        try:
            await pool.execute(
                "DELETE FROM users WHERE user_id = $1::uuid",
                row["user_id"]
            )
            print(f"🗑️ Supprimé: {row['email']}")
            deleted_count += 1
        except Exception as e:
            print(f"❌ Erreur suppression DB {row['email']}: {e}")

    print(f"✅ Nettoyage terminé: {deleted_count} comptes supprimés")
    return deleted_count