from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from core.database import get_db
from core.models import Provider
from core.schemas import ProviderResponse

router = APIRouter(prefix="/providers", tags=["Providers"])

@router.get("/", response_model=list[ProviderResponse])
def get_providers(db: Session = Depends(get_db)):
    return db.query(Provider).filter(Provider.is_active == True).all()