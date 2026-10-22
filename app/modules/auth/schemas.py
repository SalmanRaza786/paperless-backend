from typing import Optional, List
from pydantic import BaseModel, Field
from app.modules.auth.models import OTPChannel, SubscriptionTier


class LoginRequest(BaseModel):
    """Payload for login endpoint with support for both Email and Phone."""
    identifier: str = Field(..., description="Email address or Phone number (+92...)")
    password: str = Field(..., min_length=6, description="User password")
    remember_me: bool = Field(default=False, description="Extend session duration")


class TenantSummaryResponse(BaseModel):
    id: int
    name: str
    slug: str
    plan_tier: SubscriptionTier
    is_read_only: bool
    role_name: Optional[str] = None
    permissions: List[str] = []

    class Config:
        from_attributes = True


class UserResponse(BaseModel):
    id: int
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    avatar_url: Optional[str] = None
    is_superadmin: bool
    is_phone_verified: bool
    is_email_verified: bool
    preferred_otp_channel: OTPChannel
    current_tenant: Optional[TenantSummaryResponse] = None
    available_tenants: List[TenantSummaryResponse] = []

    class Config:
        from_attributes = True


class AuthTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse
    current_tenant: Optional[TenantSummaryResponse] = None
    available_tenants: List[TenantSummaryResponse] = []


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None


class SelfRegisterRequest(BaseModel):
    business_name: str = Field(..., min_length=2, max_length=150, description="Trading/Shop Name")
    legal_name: Optional[str] = Field(None, max_length=150)
    city: str = Field(..., min_length=2, max_length=50)
    phone: str = Field(..., min_length=7, max_length=20, description="Business contact number")
    ntn: Optional[str] = Field(None, max_length=30)
    strn: Optional[str] = Field(None, max_length=30)
    address: Optional[str] = Field(None, max_length=255)
    plan_tier: SubscriptionTier = Field(default=SubscriptionTier.FREE_TRIAL)
    owner_full_name: str = Field(..., min_length=2, max_length=100)
    owner_phone: str = Field(..., min_length=7, max_length=20)
    owner_email: str = Field(..., min_length=5, max_length=100, description="Owner email (Required for Email OTP)")
    owner_password: str = Field(..., min_length=6, max_length=100)
    otp_channel: OTPChannel = Field(default=OTPChannel.EMAIL)


class RegisterInitiatedResponse(BaseModel):
    success: bool = True
    verification_id: int
    destination: str
    channel: OTPChannel
    resend_available_in: int = 60
    message: str
    debug_otp: Optional[str] = None


class VerifyRegistrationOtpRequest(BaseModel):
    verification_id: int
    otp_code: str = Field(..., min_length=6, max_length=6)


class ResendOtpRequest(BaseModel):
    verification_id: int


class ResendOtpResponse(BaseModel):
    success: bool = True
    verification_id: int
    destination: str
    channel: OTPChannel
    resend_available_in: int = 60
    message: str
    debug_otp: Optional[str] = None

