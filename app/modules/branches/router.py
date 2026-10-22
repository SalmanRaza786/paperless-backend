from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.auth.router import get_current_user
from app.modules.team.services import TeamService
from app.modules.branches.models import BranchType
from app.modules.branches.schemas import (
    BranchCreate,
    BranchUpdate,
    BranchResponse,
    BranchListResponse,
)
from app.modules.branches.services import BranchService


router = APIRouter(prefix="/tenant/branches", tags=["Tenant Shops & Branches"])


@router.get(
    "",
    response_model=BranchListResponse,
    summary="List All Shops & Branches with Quota Usage",
)
async def list_branches(
    search: Optional[str] = Query(None, description="Search by name, code, city, phone"),
    city: Optional[str] = Query(None, description="Filter by city name"),
    branch_type: Optional[BranchType] = Query(None, description="Filter by branch/godown type"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    tenant_id: Optional[int] = Query(None, description="Optional tenant ID (for Super Admin)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns all shops/branches for the authenticated tenant along with active plan quota breakdown.
    Requires `branches:view` permission.
    Auto-initializes the #1 Main Headquarters branch if none exists yet.
    """
    tenant = await TeamService.resolve_active_tenant(current_user, db, requested_tenant_id=tenant_id)
    await TeamService.verify_branch_view_permission(current_user, tenant, db)
    return await BranchService.list_branches(
        db=db,
        tenant=tenant,
        search=search,
        city=city,
        branch_type=branch_type,
        is_active=is_active,
    )


@router.post(
    "",
    response_model=BranchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a New Shop or Godown",
)
async def create_branch(
    payload: BranchCreate,
    tenant_id: Optional[int] = Query(None, description="Optional tenant ID (for Super Admin)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Creates a new branch or warehouse.
    Requires `branches:create` permission.
    Strictly validates uniqueness of branch code and name within the tenant.
    Enforces the active subscription plan's `max_branches` quota (returns 403 Forbidden if exceeded).
    """
    tenant = await TeamService.resolve_active_tenant(current_user, db, requested_tenant_id=tenant_id)
    await TeamService.verify_branch_create_permission(current_user, tenant, db)
    new_branch = await BranchService.create_branch(db=db, tenant=tenant, payload=payload)
    return BranchResponse.model_validate(new_branch)


@router.get(
    "/{branch_id}",
    response_model=BranchResponse,
    summary="Get Shop / Branch Details",
)
async def get_branch(
    branch_id: int,
    tenant_id: Optional[int] = Query(None, description="Optional tenant ID (for Super Admin)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Fetches full details of a specific branch belonging to the business.
    Requires `branches:view` permission.
    """
    tenant = await TeamService.resolve_active_tenant(current_user, db, requested_tenant_id=tenant_id)
    await TeamService.verify_branch_view_permission(current_user, tenant, db)
    branch = await BranchService.get_branch(db=db, tenant_id=tenant.id, branch_id=branch_id)
    return BranchResponse.model_validate(branch)


@router.put(
    "/{branch_id}",
    response_model=BranchResponse,
    summary="Update Shop / Branch Details",
)
async def update_branch(
    branch_id: int,
    payload: BranchUpdate,
    tenant_id: Optional[int] = Query(None, description="Optional tenant ID (for Super Admin)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Updates branch details (name, code, type, contact info, FBR POS ID, active status).
    Requires `branches:edit` permission.
    """
    tenant = await TeamService.resolve_active_tenant(current_user, db, requested_tenant_id=tenant_id)
    await TeamService.verify_branch_edit_permission(current_user, tenant, db)
    branch = await BranchService.update_branch(
        db=db,
        tenant_id=tenant.id,
        branch_id=branch_id,
        payload=payload,
    )
    return BranchResponse.model_validate(branch)


@router.post(
    "/{branch_id}/set-main",
    response_model=BranchResponse,
    summary="Designate Branch as Main Headquarters",
)
async def set_main_branch(
    branch_id: int,
    tenant_id: Optional[int] = Query(None, description="Optional tenant ID (for Super Admin)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Sets this branch as the primary business headquarters (`is_main=True`) and unsets all other branches.
    Requires `branches:edit` permission.
    """
    tenant = await TeamService.resolve_active_tenant(current_user, db, requested_tenant_id=tenant_id)
    await TeamService.verify_branch_edit_permission(current_user, tenant, db)
    branch = await BranchService.set_main_branch(db=db, tenant_id=tenant.id, branch_id=branch_id)
    return BranchResponse.model_validate(branch)


@router.delete(
    "/{branch_id}",
    summary="Delete Shop / Branch",
)
async def delete_branch(
    branch_id: int,
    tenant_id: Optional[int] = Query(None, description="Optional tenant ID (for Super Admin)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Permanently removes a shop or branch location.
    Requires `branches:delete` permission.
    Guards prevent deletion of the only remaining location or the main headquarters.
    """
    tenant = await TeamService.resolve_active_tenant(current_user, db, requested_tenant_id=tenant_id)
    await TeamService.verify_branch_delete_permission(current_user, tenant, db)
    return await BranchService.delete_branch(db=db, tenant_id=tenant.id, branch_id=branch_id)
