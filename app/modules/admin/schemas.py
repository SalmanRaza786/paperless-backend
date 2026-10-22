from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
from app.modules.auth.models import TenantStatus, SubscriptionTier


class CreateTenantRequest(BaseModel):
    # Business Info
    name: str = Field(..., min_length=2, max_length=150, description="Business / Trade Name")
    legal_name: Optional[str] = Field(None, max_length=200, description="Registered legal entity name")
    ntn: Optional[str] = Field(None, max_length=30, description="National Tax Number")
    strn: Optional[str] = Field(None, max_length=30, description="Sales Tax Registration Number")
    phone: str = Field(..., min_length=7, max_length=20, description="Business official phone")
    email: Optional[str] = Field(None, description="Business official email")
    city: str = Field(..., min_length=2, max_length=50, description="Operating City")
    address: Optional[str] = Field(None, description="Shop / Warehouse Address")

    # Subscription / Plan
    plan_tier: SubscriptionTier = Field(default=SubscriptionTier.BASIC, description="Subscription Tier")
    subscription_days: int = Field(default=365, ge=14, le=1825, description="Validity period in days")

    # Primary Owner User Info
    owner_full_name: str = Field(..., min_length=2, max_length=100, description="Full name of the business owner")
    owner_phone: str = Field(..., min_length=10, max_length=20, description="Owner mobile number for login")
    owner_email: Optional[str] = Field(None, description="Owner email for login and reports")
    owner_password: str = Field(..., min_length=6, description="Initial password for owner login")


class TenantOwnerSummary(BaseModel):
    id: int
    full_name: str
    phone: Optional[str]
    email: Optional[str]

    class Config:
        from_attributes = True


class TenantListItemResponse(BaseModel):
    id: int
    name: str
    slug: str
    city: str
    phone: str
    email: Optional[str]
    status: TenantStatus
    plan_tier: SubscriptionTier
    trial_ends_at: datetime
    subscription_ends_at: Optional[datetime]
    is_read_only: bool
    owner: Optional[TenantOwnerSummary] = None
    total_users: int = 1
    created_at: datetime

    class Config:
        from_attributes = True


class PaginatedTenantsResponse(BaseModel):
    items: List[TenantListItemResponse]
    total: int
    page: int
    size: int
    pages: int


class UpdateTenantStatusRequest(BaseModel):
    status: Optional[TenantStatus] = None
    is_read_only: Optional[bool] = None
    extend_days: Optional[int] = Field(None, ge=1, le=730, description="Extend subscription by N days")


class CreateTenantResponse(BaseModel):
    tenant: TenantListItemResponse
    owner_credentials: dict
    message: str
