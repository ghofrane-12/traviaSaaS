# routers/results.py
from fastapi import APIRouter, Depends
from methods.auth import get_current_user
from core.schemas import AuthContext
import datetime
from core.firestore import firestore_service

router = APIRouter(prefix="/results", tags=["results"])
RESULTS_TTL = 60 * 60 * 2  

@router.post("/save")
async def save_results(
    body: dict,
    current_user: AuthContext = Depends(get_current_user)
):

    doc_id = (
        f"{current_user.tenant_id}_{current_user.user_id}"
        f"_{body.get('session_id')}"
    )
    try:
        firestore_service.db.collection("results_cache").document(doc_id).set({
            "segments":   body.get("segments", []),
            "session_id": body.get("session_id"),
            "tenant_id":  current_user.tenant_id,
            "user_id":    current_user.user_id,
            "created_at": datetime.datetime.utcnow().isoformat(),
        })
        print(f"[Results] ✅ Sauvegardé Firestore | doc_id={doc_id}")
        return {"success": True, "source": "firestore"}
    except Exception as e:
        print(f"[Results] ❌ Erreur Firestore: {e}")
        return {"success": False, "reason": str(e)}


@router.get("/last/{session_id}")
async def get_last_results(
    session_id: str,
    current_user: AuthContext = Depends(get_current_user)
):
    doc_id = (
        f"{current_user.tenant_id}_{current_user.user_id}"
        f"_{session_id}"
    )
    try:
        doc = firestore_service.db.collection("results_cache").document(doc_id).get()
        if doc.exists:
            data = doc.to_dict()
            print(f"[Results] ✅ Lu Firestore | doc_id={doc_id} | segments={len(data.get('segments', []))}")
            return {"segments": data.get("segments", []), "found": True}
    except Exception as e:
        print(f"[Results] ❌ Erreur lecture Firestore: {e}")
    return {"segments": [], "found": False}


@router.delete("/clear/{session_id}")
async def clear_results(
    session_id: str,
    current_user: AuthContext = Depends(get_current_user)
):

    doc_id = (
        f"{current_user.tenant_id}_{current_user.user_id}"
        f"_{session_id}"
    )
    try:
        firestore_service.db.collection("results_cache").document(doc_id).delete()
    except Exception:
        pass
    return {"success": True}