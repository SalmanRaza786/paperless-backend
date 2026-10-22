from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.auth.models import User, TenantStatus, SubscriptionTier
from app.modules.auth.router import get_current_user
from app.modules.admin.schemas import (
    CreateTenantRequest,
    CreateTenantResponse,
    TenantListItemResponse,
    PaginatedTenantsResponse,
    UpdateTenantStatusRequest,
)
from app.modules.admin.services import AdminTenantService

router = APIRouter(prefix="/admin", tags=["Super Admin Platform Management"])


async def require_superadmin(current_user: User = Depends(get_current_user)) -> User:
    """Security guard verifying caller has Super Admin privileges."""
    if not current_user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Super Administrator privileges required.",
        )
    return current_user


@router.post(
    "/tenants",
    response_model=CreateTenantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create & Provision New Business Tenant (Assisted Onboarding)",
)
async def create_tenant(
    payload: CreateTenantRequest,
    current_admin: User = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """
    Super Admin manually provisions a new Business (Tenant),
    sets subscription tier, and provisions the primary Owner account.
    """
    return await AdminTenantService.create_tenant(
        db=db,
        payload=payload,
        admin_user=current_admin,
    )


@router.get(
    "/tenants",
    response_model=PaginatedTenantsResponse,
    summary="List All Business Tenants (Super Admin Directory)",
)
async def list_tenants(
    search: Optional[str] = Query(None, description="Search by name, city, phone, email, slug"),
    status: Optional[TenantStatus] = Query(None, description="Filter by tenant status"),
    plan: Optional[SubscriptionTier] = Query(None, description="Filter by plan tier"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_admin: User = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve paginated directory of all tenant businesses with owner info."""
    return await AdminTenantService.list_tenants(
        db=db,
        search=search,
        status_filter=status,
        plan_filter=plan,
        page=page,
        size=size,
    )


@router.patch(
    "/tenants/{tenant_id}/status",
    response_model=TenantListItemResponse,
    summary="Update Tenant Status & Subscription",
)
async def update_tenant_status(
    tenant_id: int,
    payload: UpdateTenantStatusRequest,
    current_admin: User = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Activate, suspend, or extend subscription for a tenant business."""
    return await AdminTenantService.update_tenant_status(
        db=db,
        tenant_id=tenant_id,
        payload=payload,
        admin_user=current_admin,
    )
