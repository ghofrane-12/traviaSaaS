# set_super_admin.py
# Placez ce fichier à la racine de votre dossier backend/
# Exécutez-le UNE SEULE FOIS : python set_super_admin.py
# Supprimez-le ensuite.

import firebase_admin
from firebase_admin import credentials, auth as firebase_auth
from core.config import settings

if not firebase_admin._apps:
    if settings.FIREBASE_CREDENTIALS_PATH:
        cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
        firebase_admin.initialize_app(cred)
    else:
        firebase_admin.initialize_app()

UID = "RKui6fZcvzZmh1amQYAJy1dgBiJ2"  # votre user_id

firebase_auth.set_custom_user_claims(UID, {"role": "super_admin"})
print(f"✅ Custom claim 'role: super_admin' assigné à {UID}")

# Vérification
user = firebase_auth.get_user(UID)
print(f"Claims actuels : {user.custom_claims}")