# core/models.py
from sqlalchemy import Column, Integer, String, Text, Numeric, TIMESTAMP, Boolean, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import declarative_base, relationship
import sqlalchemy.sql.functions as func
from enum import Enum
import uuid

Base = declarative_base()

class AgentType(str, Enum):
    TRANSPORT   = "transport"
    STAY        = "stay"
    ACTIVITY    = "activity"
    DISCOVERY   = "discovery"
    INFO        = "info"
    COMPLIANCE  = "compliance"
    LOGISTICS   = "logistics"
    CLAIMS      = "claims"
    SECURITY    = "security"
    ASSISTANCE  = "assistance"
    GENERAL     = "general"
    HOTEL      = "hotel"
    FLIGHT     = "flight"
    OMRA       = "omra"
    CIRCUIT    = "circuit"

# ── Rôles disponibles ─────────────────────────────────────────
class UserRole(str, Enum):
    SUPER_ADMIN = "super_admin"  
    ADMIN       = "admin"         
    SUB_ADMIN   = "sub_admin"    
    VISITOR     = "visitor"       

class OfferType(str, Enum):
    FLIGHT  = "flight"
    HOTEL   = "hotel"
    OMRA    = "omra"
    CIRCUIT = "circuit"


class OfferMapping(Base):
    __tablename__ = "offer_mappings"

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id      = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    offer_type     = Column(String(20), nullable=False)
    field_map      = Column(JSON, nullable=False, default=dict)
    default_values = Column(JSON, nullable=False, default=dict)
    created_at     = Column(TIMESTAMP, server_default=func.now())
    updated_at     = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "offer_type", name="uq_tenant_offer_type"),
        CheckConstraint(
            "offer_type IN ('flight','hotel','omra','circuit','stay','transport','activity')",
            name="chk_offer_type"
        ),
    )

    tenant = relationship("Tenant", back_populates="offer_mappings")

class Tenant(Base):
    __tablename__ = "tenants"

    tenant_id = Column(UUID(as_uuid=True), primary_key=True)
    name      = Column(String(100), nullable=False)
    logo_url  = Column(Text)

    currency           = Column(String(10),  nullable=False, default="EUR")
    margin_percentage  = Column(Numeric(5,2), nullable=False, default=0.0)
    tone               = Column(String(20),  nullable=False, default="formal")
    facebook_page_id = Column(String(50), nullable=True, unique=True)
    facebook_page_token = Column(Text)


    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    user_tenants        = relationship("UserTenant", back_populates="tenant", cascade="all, delete")
    api_keys            = relationship("TenantApiKey", back_populates="tenant", cascade="all, delete")
    files               = relationship("TenantFile", back_populates="tenant", cascade="all, delete")
    loyalty             = relationship("TenantLoyalty", back_populates="tenant", cascade="all, delete")
    subcategory_configs = relationship("TenantSubcategoryConfig", back_populates="tenant", cascade="all, delete")
    offer_mappings      = relationship("OfferMapping", back_populates="tenant", cascade="all, delete")
    reviews = relationship("TenantReview", back_populates="tenant", cascade="all, delete")
    tickets = relationship("SupportTicket", back_populates="tenant", cascade="all, delete")
    bookings = relationship("UserBooking", back_populates="tenant", cascade="all, delete")


class TenantReview(Base):
    __tablename__ = "tenant_reviews"

    review_id  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id  = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    user_id    = Column(UUID(as_uuid=True), nullable=False)
    rating     = Column(Integer, nullable=False)
    comment    = Column(Text, default='')
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="chk_rating"),
    )

    tenant = relationship("Tenant", back_populates="reviews")


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    ticket_id  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id  = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    user_id    = Column(UUID(as_uuid=True), nullable=False)
    subject    = Column(Text, nullable=False)
    message    = Column(Text, nullable=False)
    phone      = Column(Text, default='')
    status     = Column(String(20), default='open')
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('open', 'in_progress', 'closed')", name="chk_ticket_status"),
    )

    tenant = relationship("Tenant", back_populates="tickets")

class Provider(Base):
    __tablename__ = "providers"

    provider_id = Column(UUID(as_uuid=True), primary_key=True)
    name        = Column(String(100), nullable=False)
    code        = Column(String(50), unique=True)
    category    = Column(String(50))
    base_url    = Column(Text)
    is_active   = Column(Boolean, default=True)
    created_at  = Column(TIMESTAMP, server_default=func.now())


# ── TABLE GLOBALE — identité Firebase ────────────────────────
class User(Base):
    __tablename__ = "users"

    user_id      = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    firebase_uid = Column(Text, unique=True, nullable=False)
    email        = Column(Text, unique=True, nullable=True)
    first_name   = Column(String(50), nullable=False, default="")
    last_name    = Column(String(50), nullable=False, default="")
    created_at   = Column(TIMESTAMP, server_default=func.now())
    updated_at   = Column(TIMESTAMP, server_default=func.now())

    tenants = relationship("UserTenant", back_populates="user", cascade="all, delete")
    bookings = relationship("UserBooking", back_populates="user", cascade="all, delete")


# ── TABLE DE LIAISON — rôle par tenant ───────────────────────
class UserTenant(Base):
    """
    Un utilisateur Google peut être client chez l'agence A
    et admin chez l'agence B — avec des IDs différents dans user_tenants.
    """
    __tablename__ = "user_tenants"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id          = Column(UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    tenant_id        = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    role             = Column(String(20), nullable=False, default="client")
    can_create_admin = Column(Boolean, nullable=False, default=False)
    is_active        = Column(Boolean, nullable=False, default=True)
    created_at       = Column(TIMESTAMP, server_default=func.now())
    updated_at       = Column(TIMESTAMP, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant"),
        CheckConstraint(
            "role IN ('super_admin', 'admin', 'sub_admin', 'client')",
            name="chk_role"
        ),
    )

    user   = relationship("User",   back_populates="tenants")
    tenant = relationship("Tenant", back_populates="user_tenants")

class UserBooking(Base):
    __tablename__ = "user_bookings"

    booking_id    = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id     = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    user_id       = Column(UUID(as_uuid=True), ForeignKey("users.user_id",    ondelete="CASCADE"), nullable=False)
    session_id    = Column(Text, nullable=False)
    booking_type  = Column(String(20), nullable=False)
    status        = Column(String(20), nullable=False, default="completed")
    origin         = Column(String(100))
    destination    = Column(String(100))
    departure_date = Column(TIMESTAMP)
    return_date    = Column(TIMESTAMP)
    adults         = Column(Integer)
    cabin_class    = Column(String(20))
    airline        = Column(String(100))
    flight_number  = Column(String(20))
    city       = Column(String(100))
    check_in   = Column(TIMESTAMP)
    check_out  = Column(TIMESTAMP)
    guests     = Column(Integer)
    hotel_name = Column(String(200))
    activity_name = Column(String(200))
    activity_date = Column(TIMESTAMP)
    venue         = Column(String(200))
    activity_city = Column(String(100))

    price    = Column(Numeric(10, 2))
    currency = Column(String(10), default="EUR")
    raw_data = Column(JSON)

    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(
            "booking_type IN ('flight','hotel','tour','restaurant')",
            name="chk_booking_type"
        ),
        CheckConstraint(
            "status IN ('completed','cancelled','pending')",
            name="chk_booking_status"
        ),
    )

    tenant = relationship("Tenant", back_populates="bookings")
    user   = relationship("User",   back_populates="bookings")

class TenantApiKey(Base):
    __tablename__ = "tenant_api_keys"

    key_id      = Column(UUID(as_uuid=True), primary_key=True)
    tenant_id   = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id", ondelete="CASCADE"))
    provider_id = Column(UUID(as_uuid=True), ForeignKey("providers.provider_id"))
    provider    = relationship("Provider")
    agent_type  = Column(String(50), nullable=False)
    label       = Column(Text, nullable=False)
    api_key     = Column(Text, nullable=False)
    api_url     = Column(Text, nullable=False)
    http_method = Column(String(10), default="POST")
    headers_template = Column(JSON)
    payload_template = Column(JSON)
    param_mapping    = Column(JSON)
    result_path      = Column(Text)
    priority    = Column(Integer, default=1)
    timeout_ms  = Column(Integer, default=5000)
    retry_count = Column(Integer, default=1)
    is_active   = Column(Boolean, default=True)
    created_at  = Column(TIMESTAMP, server_default=func.now())

    tenant = relationship("Tenant", back_populates="api_keys")


class TenantFile(Base):
    __tablename__ = "tenant_files"

    file_id          = Column(UUID(as_uuid=True), primary_key=True)
    tenant_id        = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id", ondelete="CASCADE"))
    name             = Column(String(255), nullable=False)
    file_type        = Column(String(50),  nullable=False)
    url              = Column(Text,        nullable=False)
    size_bytes       = Column(Integer)
    agent_type       = Column(String(50),  nullable=False)
    embedding_status = Column(String(20),  default="pending")
    chunk_count      = Column(Integer,     default=0)
    uploaded_by      = Column(UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL"))
    created_at       = Column(TIMESTAMP,   server_default=func.now())

    tenant            = relationship("Tenant", back_populates="files")
    subcategory_links = relationship("TenantFileSubcategory", back_populates="file", cascade="all, delete-orphan")


class TenantLoyalty(Base):
    __tablename__ = "tenant_loyalty"

    loyalty_id  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id   = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id", ondelete="CASCADE"))
    user_id     = Column(UUID(as_uuid=True), ForeignKey("users.user_id",    ondelete="CASCADE"))
    points      = Column(Integer,      default=0)
    tier        = Column(String(20),   default="bronze")
    total_spent = Column(Numeric(10,2), default=0.0)
    last_transaction_at = Column(TIMESTAMP)
    created_at  = Column(TIMESTAMP, server_default=func.now())
    updated_at  = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    tenant = relationship("Tenant", back_populates="loyalty")
    user   = relationship("User")


class TenantSubcategoryConfig(Base):
    __tablename__ = "tenant_subcategory_config"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id    = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    sub_category = Column(String(100), nullable=False)
    is_active    = Column(Boolean, default=True)
    response_type        = Column(String(20), default='api')
    llm_prompt_override  = Column(Text,    nullable=True)
    agent_type = Column(String(50), nullable=True)
    human_contact        = Column(String(200), nullable=True)
    created_at   = Column(TIMESTAMP, server_default=func.now())
    updated_at   = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("response_type IN ('api', 'rag', 'llm', 'human')", name="check_response_type"),
        UniqueConstraint('tenant_id', 'sub_category', name='uq_tenant_subcategory'),
    )

    tenant      = relationship("Tenant", back_populates="subcategory_configs")
    file_links  = relationship("TenantFileSubcategory", back_populates="subcategory", cascade="all, delete-orphan")

    @property
    def rag_files(self):
        return [link.file for link in self.file_links if link.is_active]


class TenantFileSubcategory(Base):
    __tablename__ = "tenant_file_subcategory"

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id        = Column(UUID(as_uuid=True), ForeignKey("tenant_files.file_id",            ondelete="CASCADE"))
    subcategory_id = Column(UUID(as_uuid=True), ForeignKey("tenant_subcategory_config.id",    ondelete="CASCADE"))
    priority       = Column(Integer, default=0)
    is_active      = Column(Boolean, default=True)
    created_at     = Column(TIMESTAMP, server_default=func.now())

    file        = relationship("TenantFile",              back_populates="subcategory_links")
    subcategory = relationship("TenantSubcategoryConfig", back_populates="file_links")

    __table_args__ = (
        UniqueConstraint('file_id', 'subcategory_id', name='uq_file_subcategory'),
    )