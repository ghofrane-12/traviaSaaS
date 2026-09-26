# services/conversation_service.py
from core.firestore import firestore_service
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger("conversation_service")

class ConversationService:
    """Service métier pour les conversations"""
    
    @staticmethod
    async def save_conversation(
        session_id: str,
        tenant_id: str,
        user_uid: str,
        user_role: str,
        user_message: str,
        assistant_response: Dict[str, Any],
        is_anonymous: bool = False
    ) -> str:
        """Sauvegarde une conversation"""
        logger.info(f"[SERVICE] Saving conversation | session={session_id}")
        return await firestore_service.save_message(
            session_id=session_id,
            tenant_id=tenant_id,
            user_uid=user_uid,
            user_role=user_role,
            user_message=user_message,
            assistant_response=assistant_response,
            is_anonymous=is_anonymous
        )
    
    @staticmethod
    async def get_history(
        session_id: str,
        tenant_id: str,
        user_uid: str,
        user_role: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Récupère l'historique avec vérification des droits."""
        logger.info(f"[SERVICE] Getting history | session={session_id} | user={user_uid}")
        
        history = await firestore_service.get_conversation_history(
            session_id=session_id,
            tenant_id=tenant_id,
            limit=limit
        )
        
        if user_role == "visiteur":
            return []
        
        if user_role in ["superadmin", "admin"]:
            return history
        else:
            return [msg for msg in history if msg.get("user_uid") == user_uid]
    
    @staticmethod
    async def get_user_sessions(
        user_uid: str,
        tenant_id: str,
        user_role: str
    ) -> List[Dict[str, Any]]:
        """Récupère la liste des sessions d'un utilisateur"""
        logger.info(f"[SERVICE] Getting user sessions | user={user_uid} | role={user_role}")
        
        if user_role == "visiteur":
            return []
        
        if not tenant_id or tenant_id == "None":
            logger.warning(f"[SERVICE] Invalid tenant_id: {tenant_id}")
            return []
        
        return await firestore_service.get_user_conversations(
            user_uid=user_uid,
            tenant_id=tenant_id
        )
    
    @staticmethod
    async def delete_conversation(
        session_id: str,
        tenant_id: str,
        user_uid: str,
        user_role: str
    ) -> bool:
        """Supprime une conversation complète."""
        logger.info(f"[SERVICE] Deleting conversation | session={session_id} | user={user_uid}")
        
        if user_role == "visiteur":
            return False
        
        # Vérifier les droits pour les users normaux
        if user_role not in ["superadmin", "admin"]:
            # Vérifier que l'utilisateur est propriétaire
            sessions = await firestore_service.get_user_conversations(user_uid, tenant_id)
            session_exists = any(s.get("session_id") == session_id for s in sessions)
            if not session_exists:
                logger.warning(f"[SERVICE] User {user_uid} not owner of session {session_id}")
                return False
        
        return await firestore_service.delete_conversation(session_id, tenant_id)
    
    @staticmethod
    async def rename_conversation(
        session_id: str,
        tenant_id: str,
        new_title: str,
        user_uid: str,
        user_role: str
    ) -> bool:
        """Renomme une conversation."""
        logger.info(f"[SERVICE] Renaming conversation | session={session_id} | new_title={new_title}")
        
        if user_role == "visiteur":
            return False
        
        # Vérifier les droits pour les users normaux
        if user_role not in ["superadmin", "admin"]:
            sessions = await firestore_service.get_user_conversations(user_uid, tenant_id)
            session_exists = any(s.get("session_id") == session_id for s in sessions)
            if not session_exists:
                logger.warning(f"[SERVICE] User {user_uid} not owner of session {session_id}")
                return False
        
        return await firestore_service.rename_conversation(session_id, tenant_id, new_title)

conversation_service = ConversationService()