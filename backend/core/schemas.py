# core/schemas.py
from decimal import Decimal
import uuid
from pydantic import BaseModel, field_validator, Field
from typing import Any, Dict, List, Optional, Literal
from datetime import datetime

# ── Rôles valides ─────────────────────────────────────────────
VALID_ROLES = {"super_admin", "admin", "sub_admin", "client", "visitor"}
STAFF_ROLES = {"super_admin", "admin", "sub_admin"}


# =============================================================================
# AUTH
# =============================================================================

class SessionResponse(BaseModel):
    user_id:          uuid.UUID
    firebase_uid:     str
    tenant_id:        uuid.UUID
    email:            str
    first_name:       str
    last_name:        str
    role:             str
    can_create_admin: bool = False
    is_active:        bool
    agency_name:      str
    agency_logo:      Optional[str] = None
    currency:         str = "EUR"
    tone:             str = "formal"

    @field_validator('role')
    @classmethod
    def validate_role(cls, v):
        allowed = {"super_admin", "admin", "sub_admin", "client"}
        if v not in allowed:
            raise ValueError(f"Rôle invalide pour session: {v}")
        return v


# =============================================================================
# USERS
# =============================================================================

class UserCreate(BaseModel):
    """Création d'un utilisateur par un admin ou super_admin."""
    email:      str
    password:   str
    first_name: str
    last_name:  str
    role:       str
    can_create_admin: bool = False

    @field_validator('role')
    @classmethod
    def validate_role(cls, v):
        allowed = {"admin", "sub_admin", "client"}
        if v not in allowed:
            raise ValueError(f"Rôle invalide. Choisir parmi: {allowed}")
        return v


class UserUpdate(BaseModel):
    first_name:       Optional[str]  = None
    last_name:        Optional[str]  = None
    role:             Optional[str]  = None
    is_active:        Optional[bool] = None
    can_create_admin: Optional[bool] = None

    @field_validator('role')
    @classmethod
    def validate_role(cls, v):
        if v is not None:
            allowed = {"admin", "sub_admin", "client"}
            if v not in allowed:
                raise ValueError(f"Rôle invalide. Choisir parmi: {allowed}")
        return v


class UserResponse(BaseModel):
    user_id:          uuid.UUID
    firebase_uid:     str
    tenant_id:        uuid.UUID
    email:            str
    first_name:       str
    last_name:        str
    role:             str
    can_create_admin: bool = False
    is_active:        bool
    created_at:       datetime

    class Config:
        from_attributes = True


# =============================================================================
# API KEYS
# =============================================================================

class ApiKeyCreate(BaseModel):
    label:       str
    provider_id: uuid.UUID
    agent_type:  str
    api_url:     str
    api_key:     str
    http_method: str = "POST"
    headers_template: Optional[dict] = None
    payload_template: Optional[dict] = None
    param_mapping:    Optional[dict] = None
    result_path:      Optional[str]  = None
    priority:    int = 1
    timeout_ms:  int = 5000
    retry_count: int = 1


class ApiKeyResponse(BaseModel):
    key_id:       uuid.UUID
    label:        str
    provider_id:  uuid.UUID
    provider_name: str
    agent_type:   str
    api_url:      str
    http_method:  str
    headers_template: Optional[dict]
    payload_template: Optional[dict]
    param_mapping:    Optional[dict]
    result_path:      Optional[str]
    priority:    int
    timeout_ms:  int
    retry_count: int
    is_active:   bool
    created_at:  datetime


class ApiKeyUpdate(BaseModel):
    label:            Optional[str]  = None
    api_url:          Optional[str]  = None
    http_method:      Optional[str]  = None
    headers_template: Optional[dict] = None
    payload_template: Optional[dict] = None
    result_path:      Optional[str]  = None
    param_mapping:    Optional[dict] = None
    is_active:        Optional[bool] = None
    agent_type:       Optional[str]  = None


class ProviderResponse(BaseModel):
    provider_id: uuid.UUID
    name:        str
    code:        str
    category:    Optional[str]


# =============================================================================
# FILES
# =============================================================================

class FileCreate(BaseModel):
    name:       str
    file_type:  str
    url:        str
    size_bytes: Optional[int] = None
    agent_type: str


class FileResponse(BaseModel):
    file_id:          uuid.UUID
    tenant_id:        uuid.UUID
    name:             str
    file_type:        str
    url:              str
    agent_type:       str
    embedding_status: str
    chunk_count:      int
    created_at:       datetime


# =============================================================================
# SUBCATEGORY CONFIG
# =============================================================================

class TenantSubcategoryConfigBase(BaseModel):
    sub_category:        str = Field(..., max_length=100)
    is_active:           bool = True
    response_type:       Literal['api', 'rag', 'llm', 'human'] = 'api'
    llm_prompt_override: Optional[str] = None
    human_contact:       Optional[str] = None
    agent_type:          Optional[str] = None


class TenantSubcategoryConfigCreate(TenantSubcategoryConfigBase):
    tenant_id: uuid.UUID
    agent_type: Optional[str] = None


class TenantSubcategoryConfigUpdate(BaseModel):
    is_active:           Optional[bool] = None
    response_type:       Optional[Literal['api', 'rag', 'llm', 'human']] = None
    llm_prompt_override: Optional[str]  = None
    human_contact:       Optional[str]  = None


class TenantSubcategoryConfigResponse(TenantSubcategoryConfigBase):
    id:         uuid.UUID
    tenant_id:  uuid.UUID
    agent_type: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class FileSubcategoryLinkCreate(BaseModel):
    file_id:        uuid.UUID
    subcategory_id: uuid.UUID
    priority:       int  = 0
    is_active:      bool = True


class FileSubcategoryLinkResponse(BaseModel):
    id:             uuid.UUID
    file_id:        uuid.UUID
    subcategory_id: uuid.UUID
    priority:       int
    is_active:      bool
    created_at:     datetime
    file_name:      Optional[str] = None
    sub_category:   Optional[str] = None

    class Config:
        from_attributes = True


class SubcategoryFilesResponse(BaseModel):
    subcategory: TenantSubcategoryConfigResponse
    files:       List[FileResponse] = []


# =============================================================================
# TENANTS
# =============================================================================

class TenantCreate(BaseModel):
    name:              str
    logo_url:          Optional[str]      = None
    currency:          str                = "EUR"
    margin_percentage: Decimal            = Decimal("0.0")
    tone:              str                = "formal"
    facebook_page_id:  Optional[str]      = None  
    facebook_page_token: Optional[str] = None 
    api_keys:          list[ApiKeyCreate] = []


class TenantUpdate(BaseModel):
    name:              Optional[str]     = None
    logo_url:          Optional[str]     = None
    currency:          Optional[str]     = None
    margin_percentage: Optional[Decimal] = None
    tone:              Optional[str]     = None
    facebook_page_id:  Optional[str]      = None  
    facebook_page_token: Optional[str] = None 

class TenantResponse(BaseModel):
    tenant_id:          uuid.UUID
    name:               str
    logo_url:           Optional[str]
    currency:           str
    margin_percentage:  Decimal
    tone:               str
    facebook_page_id:   Optional[str]      = None  
    facebook_page_token: Optional[str] = None 
    created_at:         datetime
    updated_at:         datetime
    api_keys:           list[ApiKeyResponse]                  = []
    files:              list[FileResponse]                    = []
    subcategory_configs: List[TenantSubcategoryConfigResponse] = []


class TenantPublic(BaseModel):
    tenant_id: uuid.UUID
    name:      str
    logo_url:  Optional[str]
    currency:  str
    tone:      str


# =============================================================================
# AUTH CONTEXT — injecté par le middleware dans chaque requête
# =============================================================================

class AuthContext(BaseModel):
    user_id:          str
    tenant_id:        Optional[str] = None
    role:             str
    is_active:        bool
    email:            Optional[str] = None
    first_name:       Optional[str] = None
    last_name:        Optional[str] = None
    firebase_uid:     Optional[str] = None
    can_create_admin: bool = False

    @field_validator('role')
    @classmethod
    def validate_role(cls, v):
        if v == "visiteur":
            return "visitor"
        if v not in VALID_ROLES:
            raise ValueError(f"Rôle invalide: {v}. Choisir parmi: {VALID_ROLES}")
        return v

    class Config:
        from_attributes = True

    @property
    def is_super_admin(self) -> bool:
        return self.role == "super_admin"

    @property
    def is_admin(self) -> bool:
        return self.role in ("super_admin", "admin")

    @property
    def is_staff(self) -> bool:
        return self.role in ("super_admin", "admin", "sub_admin")

    @property
    def is_anonymous(self) -> bool:
        return self.role == "visitor"


# =============================================================================
# CHAT
# =============================================================================

class ChatRequest(BaseModel):
    text:       str = ""
    tenant_id:  str
    session_id: Optional[str] = None
    entities:       Optional[List[Dict[str, Any]]] = None
    classification: Optional[List[Dict[str, Any]]] = None
    language:   Optional[str] = None
    clarification_pending:  bool = False
    clarification_subcats:  list = []
    clarification_segment:  str  = ""
    clarification_choice:   Optional[dict] = None
    clarification_accepted: list = []
    clarification_entities: dict = {}


class ChatResponse(BaseModel):
    reply:          str
    category:       Optional[str] = None
    sub_category:   Optional[str] = None
    language:       Optional[str] = None
    score:          Optional[float] = None
    entities:       Optional[List[Dict[str, Any]]] = None
    session_id:     Optional[str] = None
    tenant_id:      Optional[str] = None
    user_id:        Optional[str] = None


class ChatMessageResponse(BaseModel):
    reply:          str
    category:       Optional[str] = None
    sub_category:   Optional[str] = None
    language:       Optional[str] = None
    score:          Optional[float] = None
    entities:       Optional[List[Dict[str, Any]]] = None
    session_id:     Optional[str] = None
    tenant_id:      Optional[str] = None
    user_id:        Optional[str] = None
    status:         Optional[str] = None
    message:        Optional[str] = None
    form:           Optional[Dict[str, Any]] = None
    steps:          Optional[List[str]] = None
    results:        Optional[List[Any]] = None
    offers:         Optional[List[Any]] = None
    source:         Optional[str] = None
    info:           Optional[Dict[str, Any]] = None
    suggestions:    Optional[List[Any]] = None
    loyalty_summary: Optional[Dict[str, Any]] = None
    follow_up:      Optional[str] = None
    action:         Optional[str] = None
    entities_dict:  Optional[Dict] = None


class ChatFormRequest(BaseModel):
    city:       str
    check_in:   str
    check_out:  str
    guests:     int = 1
    rooms:      int = 1
    price_min:  Optional[int] = None
    price_max:  Optional[int] = None
    stars:      Optional[str] = None
    sort_by:    Optional[str] = None
    order:      Optional[str] = None
    user_id:    Optional[str] = None
    tenant_id:  Optional[str] = None
    session_id: Optional[str] = None

    class Config:
        extra = "allow"


class ChatFormRequestTransport(BaseModel):
    origin:         str
    destination:    str
    departure_date: str
    return_date:    Optional[str] = None
    adults:         int = 1
    cabin_class:    str = "ECONOMY"
    price_min:      Optional[int] = None
    price_max:      Optional[int] = None
    user_id:        Optional[str] = None
    tenant_id:      Optional[str] = None
    session_id:     Optional[str] = None

    class Config:
        extra = "allow"


# =============================================================================
# LOYALTY
# =============================================================================

class LoyaltyData(BaseModel):
    loyalty_id:          uuid.UUID
    tenant_id:           uuid.UUID
    user_id:             uuid.UUID
    points:              int
    tier:                str
    total_spent:         Decimal
    last_transaction_at: Optional[datetime]
    created_at:          datetime
    updated_at:          datetime


class LoyaltyUpdate(BaseModel):
    points_earned:    int
    amount_spent:     Decimal
    transaction_type: str


class LoyaltyResponse(BaseModel):
    previous_tier:  str
    new_tier:       str
    points_earned:  int
    points_before:  int
    points_after:   int
    tier_upgraded:  bool
    benefits:       list[str]

# =============================================================================
# OFFERS
# =============================================================================

class OfferMappingCreate(BaseModel):
    offer_type:     str
    field_map:      Dict[str, str]
    default_values: Dict[str, Any] = {}

    @field_validator('offer_type')
    @classmethod
    def validate_offer_type(cls, v):
        allowed = {
            "flight", "hotel", "omra", "circuit",
            "stay", "transport", "activity"
        }
        if v not in allowed:
            raise ValueError(f"Type invalide. Choisir parmi: {allowed}")
        return v


class OfferMappingResponse(BaseModel):
    id:             uuid.UUID
    tenant_id:      uuid.UUID
    offer_type:     str
    field_map:      Dict[str, str]
    default_values: Dict[str, Any]
    created_at:     datetime
    updated_at:     Optional[datetime]

    class Config:
        from_attributes = True


class OfferItem(BaseModel):
    """Offre normalisée — champs standards pour l'affichage."""
    id:             Optional[str]    = None
    type:           str
    title:          Optional[str]    = None
    destination:    Optional[str]    = None
    origin:         Optional[str]    = None
    price:          Optional[float]  = None
    currency:       str              = "EUR"
    departure_date: Optional[str]    = None
    duration_days:  Optional[int]    = None
    image_url:      Optional[str]    = None
    description:    Optional[str]    = None
    is_available:   bool             = True
    raw:            Optional[Dict[str, Any]] = None 


class OffersResponse(BaseModel):
    tenant_id:   str
    agency_name: str
    total:       int
    offers:      List[OfferItem]
# =============================================================================
# GENERAL — Reviews & Tickets
# =============================================================================

class ReviewCreate(BaseModel):
    tenant_id: str
    user_id:   str
    rating:    int = Field(..., ge=1, le=5)
    comment:   Optional[str] = ""

class ReviewResponse(BaseModel):
    review_id:  uuid.UUID
    rating:     int
    comment:    str
    created_at: datetime

    class Config:
        from_attributes = True

class TicketCreate(BaseModel):
    tenant_id: str
    user_id:   str
    subject:   str
    message:   str
    phone:     Optional[str] = ""

class TicketResponse(BaseModel):
    ticket_id:  uuid.UUID
    subject:    str
    status:     str
    created_at: datetime

    class Config:
        from_attributes = True