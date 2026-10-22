from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field
from app.modules.branches.models import BranchType


class BranchBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=100, description="Branch or shop display name")
    code: str = Field(..., min_length=2, max_length=30, description="Unique shop/branch identifier code")
    type: BranchType = Field(default=BranchType.RETAIL_SHOP, description="Shop or storage location type")
    phone: str = Field(..., min_length=7, max_length=25, description="Primary contact phone number")
    email: Optional[str] = Field(None, max_length=100, description="Branch email address")
    city: str = Field(..., min_length=2, max_length=50, description="City location")
    address: Optional[str] = Field(None, max_length=300, description="Street address")
    fbr_pos_id: Optional[str] = Field(None, max_length=50, description="Optional FBR digital POS machine identifier")
    is_active: bool = Field(default=True, description="Whether this branch is active")


class BranchCreate(BranchBase):
    is_main: bool = Field(default=False, description="Set as main business headquarters/primary shop")


class BranchUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    code: Optional[str] = Field(None, min_length=2, max_length=30)
    type: Optional[BranchType] = None
    phone: Optional[str] = Field(None, min_length=7, max_length=25)
    email: Optional[str] = None
    city: Optional[str] = Field(None, min_length=2, max_length=50)
    address: Optional[str] = None
    fbr_pos_id: Optional[str] = None
    is_active: Optional[bool] = None
    is_main: Optional[bool] = None


class BranchResponse(BranchBase):
    id: int
    tenant_id: int
    is_main: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BranchQuotaUsage(BaseModel):
    used: int
    max: int
    is_unlimited: bool
    is_reached: bool
    plan_name: str
    plan_slug: str


class BranchListResponse(BaseModel):
    branches: List[BranchResponse]
    quota: BranchQuotaUsage
    total: int
