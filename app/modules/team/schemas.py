from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator


class RoleResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    is_system: bool
    permissions: Dict[str, Any] = {}

    class Config:
        from_attributes = True


class PermissionItem(BaseModel):
    id: int
    code: str
    name: str
    description: Optional[str] = None
    module: str

    class Config:
        from_attributes = True


class ModulePermissionsGroup(BaseModel):
    module: str
    label: str
    permissions: List[PermissionItem]


class RoleMatrixItem(BaseModel):
    id: int
    tenant_id: Optional[int] = None
    name: str
    description: Optional[str] = None
    is_system: bool
    user_count: int = 0
    permission_ids: List[int] = []
    permission_codes: List[str] = []

    class Config:
        from_attributes = True


class CreateRoleRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=50, description="Name of custom role")
    description: Optional[str] = Field(None, max_length=255)
    permission_ids: List[int] = Field(default_factory=list)


class UpdateRoleRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=50)
    description: Optional[str] = Field(None, max_length=255)
    permission_ids: Optional[List[int]] = None


class CreatePermissionRequest(BaseModel):
    module: str = Field(..., min_length=2, max_length=50, description="Module code (e.g. sales, pos, inventory)")
    code: str = Field(..., min_length=3, max_length=100, description="Unique permission code (e.g. sales:custom_action)")
    name: str = Field(..., min_length=2, max_length=100, description="Display name of the permission")
    description: Optional[str] = Field(None, max_length=255)


class UpdatePermissionRequest(BaseModel):
    module: Optional[str] = Field(None, min_length=2, max_length=50)
    code: Optional[str] = Field(None, min_length=3, max_length=100)
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=255)



class StaffMemberResponse(BaseModel):
    id: int  # TenantUser ID
    user_id: int
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    role_id: int
    role_name: str
    is_owner: bool
    is_active: bool
    max_discount_pct: float = 0.0
    joined_at: datetime
    last_login_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class StaffListResponse(BaseModel):
    items: List[StaffMemberResponse]
    total: int
    used_staff: int
    max_staff: Optional[int] = None
    plan_name: str
    can_add_more: bool
    can_manage: bool = True


class StaffCreateRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=100, description="Full name of staff member")
    phone: Optional[str] = Field(None, max_length=20, description="Mobile number (03XXXXXXXXX)")
    email: Optional[str] = Field(None, max_length=100, description="Email address")
    password: str = Field(..., min_length=6, max_length=128, description="Login password")
    role_id: int = Field(..., description="ID of the assigned role")
    max_discount_pct: float = Field(0.0, ge=0.0, le=100.0, description="Maximum allowable discount percentage")

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v:
            clean = v.strip().replace(" ", "").replace("-", "")
            return clean
        return None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v:
            clean = v.strip().lower()
            return clean
        return None


class StaffUpdateRequest(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=100)
    role_id: Optional[int] = None
    max_discount_pct: Optional[float] = Field(None, ge=0.0, le=100.0)
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=6, max_length=128)
