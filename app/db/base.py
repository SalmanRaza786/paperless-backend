"""Database models export for Alembic and application runtime."""
from app.db.base_class import Base
from app.modules.auth.models import (
    Tenant,
    User,
    Role,
    TenantUser,
    OTPVerification,
    UserSession,
    TenantInvitation,
    AuthAuditLog,
    Permission,
    RolePermission,
    UserPermissionOverride,
    TenantStatus,
    SubscriptionTier,
    OTPPurpose,
    OTPChannel,
    OTPDeliveryStatus,
    TwoFactorMethod,
    InviteStatus,
)

from app.modules.plans.models import SubscriptionPlan
from app.modules.branches.models import Branch, BranchType

__all__ = [
    "Base",
    "Tenant",
    "User",
    "Role",
    "TenantUser",
    "OTPVerification",
    "UserSession",
    "TenantInvitation",
    "AuthAuditLog",
    "Permission",
    "RolePermission",
    "UserPermissionOverride",
    "TenantStatus",
    "SubscriptionTier",
    "OTPPurpose",
    "OTPChannel",
    "OTPDeliveryStatus",
    "TwoFactorMethod",
    "InviteStatus",
    "SubscriptionPlan",
    "Branch",
    "BranchType",
]

