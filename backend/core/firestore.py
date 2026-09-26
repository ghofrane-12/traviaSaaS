# core/firestore.py - Version corrigée

from core.firebase import db
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
import asyncio
import logging
from google.cloud.firestore import Increment

logger = logging.getLogger("firestore_service")
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(console_handler)
    
    file_handler = logging.FileHandler("firestore.log", mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(file_handler)

class FirestoreConversationService:
    """Service de gestion des conversations Firestore"""
    
    def __init__(self):
        self.db = db
        logger.info("✅ FirestoreConversationService initialisé")
        

    async def save_message(
        self, 
        session_id: str, 
        tenant_id: str, 
        user_uid: str, 
        user_role: str, 
        user_message: str, 
        assistant_response: Dict[str, Any], 
        is_anonymous: bool = False
    ) -> str:
        try:
            logger.info(f"[FIRESTORE] 💾 Saving message | session={session_id} | tenant={tenant_id} | user={user_uid}")
            
            if not tenant_id or tenant_id == "None" or tenant_id == "null":
                logger.error(f"[FIRESTORE] ❌ tenant_id invalide: {tenant_id}")
                return ""
            
            if not session_id:
                logger.error(f"[FIRESTORE] ❌ session_id invalide: {session_id}")
                return ""
            
            doc_ref = self.db.collection("conversations")\
                .document(tenant_id)\
                .collection(session_id)\
                .document()
            
            existing_msgs = self.db.collection("conversations")\
                .document(tenant_id)\
                .collection(session_id)\
                .limit(1)\
                .get()
            
            is_first_message = len(list(existing_msgs)) == 0
            logger.info(f"[FIRESTORE] First message: {is_first_message}")
            
            data = {
                "user_uid": user_uid,
                "user_role": user_role,
                "user_message": user_message,
                "assistant_response": assistant_response,
                "timestamp": datetime.utcnow(),
                "is_anonymous": is_anonymous,
                "session_id": session_id,
                "tenant_id": tenant_id
            }
            
            doc_ref.set(data)
            logger.info(f"[FIRESTORE] ✅ Message saved | doc_id={doc_ref.id}")
            
            session_meta_ref = self.db.collection("conversations")\
                .document(tenant_id)\
                .collection("_sessions_meta")\
                .document(session_id)
            
            if is_first_message:
                session_meta = {
                    "session_id": session_id,
                    "tenant_id": tenant_id,
                    "user_uid": user_uid,
                    "user_role": user_role,
                    "title": user_message[:50] + ("..." if len(user_message) > 50 else ""),
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                    "message_count": 1,
                    "is_anonymous": is_anonymous,
                    "last_message_preview": user_message[:100]
                }
                session_meta_ref.set(session_meta)
                logger.info(f"[FIRESTORE] ✅ Session metadata created for {session_id}")
            else:
                try:
                    session_meta_ref.update({
                        "updated_at": datetime.utcnow(),
                        "message_count": Increment(1),
                        "last_message_preview": user_message[:100]
                    })
                    logger.debug(f"[FIRESTORE] Updated metadata for {session_id}")
                except Exception as e:
                    logger.warning(f"[FIRESTORE] Could not update metadata: {e}")
            
            return doc_ref.id
            
        except Exception as e:
            logger.error(f"[FIRESTORE] ❌ Error: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return ""
    
    async def get_conversation_history(
        self,
        session_id: str,
        tenant_id: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Récupère l'historique d'une conversation."""
        logger.info(f"[FIRESTORE] 📖 Getting history | session={session_id} | tenant={tenant_id}")
        
        if not tenant_id or tenant_id == "None" or tenant_id == "null":
            logger.warning(f"[FIRESTORE] ⚠️ tenant_id invalide: {tenant_id}")
            return []
        
        if not session_id:
            logger.warning(f"[FIRESTORE] ⚠️ session_id invalide: {session_id}")
            return []
        
        try:
            messages_ref = self.db.collection("conversations")\
                .document(tenant_id)\
                .collection(session_id)\
                .order_by("timestamp")\
                .limit(limit)
            
            docs = messages_ref.stream()
            history = []
            
            for doc in docs:
                data = doc.to_dict()
                if data.get("timestamp"):
                    if hasattr(data["timestamp"], 'isoformat'):
                        data["timestamp"] = data["timestamp"].isoformat()
                    else:
                        data["timestamp"] = str(data["timestamp"])
                history.append(data)
            
            logger.info(f"[FIRESTORE] ✅ Retrieved {len(history)} messages")
            return history
            
        except Exception as e:
            logger.error(f"[FIRESTORE] ❌ Error getting history: {e}")
            return []
    
    async def get_user_conversations(
        self, 
        user_uid: str, 
        tenant_id: str, 
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Récupère toutes les conversations d'un utilisateur triées par dernier message."""
        logger.info(f"[FIRESTORE] 📋 Getting user conversations | user={user_uid} | tenant={tenant_id}")
        
        if not tenant_id or tenant_id == "None" or tenant_id == "null":
            logger.warning(f"[FIRESTORE] ⚠️ tenant_id invalide: {tenant_id}")
            return []

        try:
            collections = self.db.collection("conversations") \
                .document(tenant_id) \
                .collections()
            
            conversations = []
            
            for session_collection in collections:
                session_id = session_collection.id
                
                if session_id.startswith("_"):
                    continue
                
                all_docs = list(session_collection.order_by("timestamp").stream())
                if not all_docs:
                    continue
                
                first_doc = all_docs[0]
                first_msg_data = first_doc.to_dict()
                
                session_user_id = first_msg_data.get("user_uid")
                if session_user_id != user_uid:
                    continue
                
                last_doc = all_docs[-1]
                last_msg_data = last_doc.to_dict()
                last_timestamp = last_msg_data.get("timestamp")
                
                message_count = len(all_docs)
                
                title = None
                for doc in all_docs:
                    data = doc.to_dict()
                    if data.get("title"):
                        title = data["title"]
                        break
                
                if not title:
                    title = first_msg_data.get("user_message", "Nouvelle conversation")[:50]
                
                created_at = first_msg_data.get("timestamp")
                if created_at and hasattr(created_at, 'isoformat'):
                    created_at = created_at.isoformat()
                
                updated_at = last_timestamp
                if updated_at and hasattr(updated_at, 'isoformat'):
                    updated_at = updated_at.isoformat()
                
                conversations.append({
                    "session_id": session_id,
                    "created_at": created_at,
                    "updated_at": updated_at,  
                    "first_message": first_msg_data.get("user_message", "")[:100],
                    "message_count": message_count,
                    "title": title
                })
            
            conversations.sort(
                key=lambda x: x.get("updated_at", x.get("created_at", "")),
                reverse=True  
            )
            
            logger.info(f"[FIRESTORE] ✅ Found {len(conversations)} conversations for user {user_uid}")
            return conversations[:limit]
            
        except Exception as e:
            logger.error(f"[FIRESTORE] ❌ Error getting user conversations: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    async def delete_conversation(
        self,
        session_id: str,
        tenant_id: str
    ) -> bool:
        """Supprime une conversation complète."""
        logger.info(f"[FIRESTORE] 🗑️ Deleting conversation | session={session_id} | tenant={tenant_id}")
        
        try:
            messages_ref = self.db.collection("conversations")\
                .document(tenant_id)\
                .collection(session_id)
            
            docs = list(messages_ref.stream())
            for doc in docs:
                doc.reference.delete()
            
            meta_ref = self.db.collection("conversations")\
                .document(tenant_id)\
                .collection("_sessions_meta")\
                .document(session_id)
            
            if meta_ref.get().exists:
                meta_ref.delete()
            
            logger.info(f"[FIRESTORE] ✅ Deleted {len(docs)} messages from session {session_id}")
            return True
            
        except Exception as e:
            logger.error(f"[FIRESTORE] ❌ Error deleting conversation: {e}")
            return False
    
    async def rename_conversation(
        self,
        session_id: str,
        tenant_id: str,
        new_title: str
    ) -> bool:
        """Renomme une conversation."""
        logger.info(f"[FIRESTORE] ✏️ Renaming conversation | session={session_id} | new_title={new_title}")
        
        try:
            meta_ref = self.db.collection("conversations")\
                .document(tenant_id)\
                .collection("_sessions_meta")\
                .document(session_id)
            
            if meta_ref.get().exists:
                meta_ref.update({"title": new_title})
                logger.info(f"[FIRESTORE] ✅ Conversation renamed to '{new_title}'")
                return True
            else:
                logger.warning(f"[FIRESTORE] ⚠️ Session metadata not found for {session_id}")
                return False
                
        except Exception as e:
            logger.error(f"[FIRESTORE] ❌ Error renaming conversation: {e}")
            return False
    
    async def delete_old_anonymous_conversations(self, hours: int = 1):
        """Supprime les conversations anonymes de plus de X heures."""
        cutoff = datetime.utcnow().replace(tzinfo=timezone.utc) - timedelta(hours=hours)
        deleted_count = 0
        sessions_deleted = 0

        try:
            logger.info(f"[FIRESTORE] 🧹 Cleaning old anonymous conversations > {hours}h")
            
            tenants_ref = self.db.collection("conversations").list_documents()
            
            for tenant_doc in tenants_ref:
                tenant_id = tenant_doc.id
                
                sessions_meta_ref = self.db.collection("conversations")\
                    .document(tenant_id)\
                    .collection("_sessions_meta")
                
                docs = sessions_meta_ref.where("is_anonymous", "==", True).stream()
                
                for doc in docs:
                    meta_data = doc.to_dict()
                    created_at = meta_data.get("created_at")
                    
                    if created_at and created_at < cutoff:
                        session_id = doc.id
                        logger.info(f"[FIRESTORE] Deleting old anonymous session: {session_id}")
                        
                        messages_ref = self.db.collection("conversations")\
                            .document(tenant_id)\
                            .collection(session_id)
                        
                        messages = list(messages_ref.stream())
                        for msg in messages:
                            msg.reference.delete()
                        
                        doc.reference.delete()
                        
                        sessions_deleted += 1
                        deleted_count += len(messages)
                        logger.info(f"[FIRESTORE] ✅ Deleted session {session_id} ({len(messages)} messages)")
            
            logger.info(f"[FIRESTORE] ✅ Cleanup complete: {deleted_count} messages, {sessions_deleted} sessions")
            return deleted_count
            
        except Exception as e:
            logger.error(f"[FIRESTORE] ❌ Error cleaning up: {e}")
            import traceback
            traceback.print_exc()
            return 0


firestore_service = FirestoreConversationService()