from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from uuid import UUID
from core.database import get_db
from core.models import TenantSubcategoryConfig
from core.schemas import (
    TenantSubcategoryConfigCreate,
    TenantSubcategoryConfigUpdate,
    TenantSubcategoryConfigResponse
)

router = APIRouter(
    prefix="/tenants/{tenant_id}/subcategories",
    tags=["Subcategories"]
)

@router.get("/", response_model=list[TenantSubcategoryConfigResponse])
def get_subcategory_configs(tenant_id: UUID, db: Session = Depends(get_db)):
    return db.query(TenantSubcategoryConfig)\
             .filter(TenantSubcategoryConfig.tenant_id == tenant_id)\
             .all()

@router.post("/", response_model=TenantSubcategoryConfigResponse)
def create_subcategory_config(
    tenant_id: UUID,
    payload: TenantSubcategoryConfigCreate,
    db: Session = Depends(get_db)
):
    existing = db.query(TenantSubcategoryConfig).filter_by(
        tenant_id=tenant_id,
        sub_category=payload.sub_category
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Sous-catégorie déjà configurée")

    config = TenantSubcategoryConfig(
        tenant_id=tenant_id,
        **payload.model_dump(exclude={"tenant_id"})
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return config

@router.patch("/{config_id}", response_model=TenantSubcategoryConfigResponse)
def update_subcategory_config(
    tenant_id: UUID,
    config_id: UUID,
    payload: TenantSubcategoryConfigUpdate,
    db: Session = Depends(get_db)
):
    config = db.query(TenantSubcategoryConfig).filter_by(
        id=config_id,
        tenant_id=tenant_id
    ).first()
    if not config:
        raise HTTPException(status_code=404, detail="Configuration introuvable")

    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(config, field, value)

    db.commit()
    db.refresh(config)
    return config

@router.delete("/{config_id}", status_code=204)
def delete_subcategory_config(
    tenant_id: UUID,
    config_id: UUID,
    db: Session = Depends(get_db)
):
    config = db.query(TenantSubcategoryConfig).filter_by(
        id=config_id,
        tenant_id=tenant_id
    ).first()
    if not config:
        raise HTTPException(status_code=404, detail="Configuration introuvable")
    db.delete(config)
    db.commit()