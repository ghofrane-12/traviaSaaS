from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from uuid import UUID, uuid4
from core.database import get_db
from core.models import TenantApiKey, Provider
from core.schemas import ApiKeyCreate, ApiKeyUpdate
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import uuid

class ApiKeyOut(BaseModel):
    key_id: uuid.UUID
    label: str
    provider_id: uuid.UUID
    provider_name: str
    agent_type: str
    api_url: str
    http_method: str
    headers_template: Optional[dict] = None
    payload_template: Optional[dict] = None
    param_mapping: Optional[dict] = None
    result_path: Optional[str] = None
    priority: int
    timeout_ms: int
    retry_count: int
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

router = APIRouter(prefix="/tenants/{tenant_id}/api-keys", tags=["API Keys"])

def serialize_key(key: TenantApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        key_id=key.key_id,
        label=key.label,
        provider_id=key.provider_id,
        provider_name=key.provider.name if key.provider else "—",
        agent_type=key.agent_type,
        api_url=key.api_url,
        http_method=key.http_method,
        headers_template=key.headers_template,
        payload_template=key.payload_template,
        param_mapping=key.param_mapping,
        result_path=key.result_path,
        priority=key.priority,
        timeout_ms=key.timeout_ms,
        retry_count=key.retry_count,
        is_active=key.is_active,
        created_at=key.created_at
    )

@router.get("/", response_model=list[ApiKeyOut])
def get_api_keys(tenant_id: UUID, db: Session = Depends(get_db)):
    keys = db.query(TenantApiKey).filter(TenantApiKey.tenant_id == tenant_id).all()
    return [serialize_key(k) for k in keys]

@router.post("/", response_model=ApiKeyOut)
def create_api_key(
    tenant_id: UUID,
    payload: ApiKeyCreate,
    db: Session = Depends(get_db)
):
    key = TenantApiKey(
        key_id=uuid4(),
        tenant_id=tenant_id,
        **payload.model_dump()
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    return serialize_key(key)

@router.patch("/{key_id}", response_model=ApiKeyOut)
def update_api_key(
    tenant_id: UUID,
    key_id: UUID,
    payload: ApiKeyUpdate,
    db: Session = Depends(get_db)
):
    key = db.query(TenantApiKey).filter_by(
        key_id=key_id, tenant_id=tenant_id
    ).first()
    if not key:
        raise HTTPException(status_code=404, detail="Clé introuvable")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(key, field, value)
    db.commit()
    db.refresh(key)
    return serialize_key(key)

@router.delete("/{key_id}", status_code=204)
def delete_api_key(
    tenant_id: UUID,
    key_id: UUID,
    db: Session = Depends(get_db)
):
    key = db.query(TenantApiKey).filter_by(
        key_id=key_id, tenant_id=tenant_id
    ).first()
    if not key:
        raise HTTPException(status_code=404, detail="Clé introuvable")
    db.delete(key)
    db.commit()