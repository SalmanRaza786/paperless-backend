from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.auth.router import get_current_user
from app.modules.team.schemas import (
    StaffCreateRequest,
    StaffUpdateRequest,
    StaffMemberResponse,
    StaffListResponse,
    RoleResponse,
    PermissionItem,
    ModulePermissionsGroup,
    RoleMatrixItem,
    CreateRoleRequest,
    UpdateRoleRequest,
    CreatePermissionRequest,
    UpdatePermissionRequest,
)
from app.modules.team.services import TeamService
from app.core.entitlements import EntitlementService


router = APIRouter(prefix="/tenant", tags=["Tenant Staff & Team Management"])


@router.get(
    "/roles",
    response_model=List[RoleResponse],
    summary="List Assignable Roles for Staff Members",
)
async def get_roles(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns available staff roles (e.g. Admin, Cashier, Salesman, Accountant, Warehouse Manager).
    """
    tenant = await TeamService.resolve_active_tenant(current_user, db)
    return await TeamService.get_tenant_roles(db, tenant.id)


@router.get(
    "/users",
    response_model=StaffListResponse,
    summary="List Staff Members and Quota Usage",
)
async def list_staff_members(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tenant_id: Optional[int] = Query(None, description="Optional tenant ID (for Super Admin impersonation)"),
):
    """
    List staff members belonging to the current business tenant with plan limit information.
    """
    tenant = await TeamService.resolve_active_tenant(current_user, db, requested_tenant_id=tenant_id)
    return await TeamService.list_staff(db, tenant, current_user)


@router.get(
    "/quotas",
    summary="Get Current Tenant Plan Limits & Quota Breakdown",
)
async def get_tenant_quotas(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tenant_id: Optional[int] = Query(None, description="Optional tenant ID (for Super Admin impersonation)"),
):
    """
    Retrieve active plan limits, current resource consumption (staff, branches), and boolean feature access flags.
    """
    tenant = await TeamService.resolve_active_tenant(current_user, db, requested_tenant_id=tenant_id)
    return await EntitlementService.get_tenant_quota_summary(db, tenant)



@router.post(
    "/users",
    response_model=StaffMemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create & Assign New Staff Member",
)
async def create_staff_member(
    payload: StaffCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tenant_id: Optional[int] = Query(None, description="Optional tenant ID (for Super Admin)"),
):
    """
    Create a new staff member under the current business tenant.
    Only Owner or Admin can provision staff. Validates mobile/email uniqueness and plan quota constraints.
    """
    tenant = await TeamService.resolve_active_tenant(current_user, db, requested_tenant_id=tenant_id)
    return await TeamService.create_staff(db, tenant, payload, current_user)


@router.put(
    "/users/{staff_id}",
    response_model=StaffMemberResponse,
    summary="Update Staff Member Details, Role, or Status",
)
async def update_staff_member(
    staff_id: int,
    payload: StaffUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tenant_id: Optional[int] = Query(None, description="Optional tenant ID (for Super Admin)"),
):
    """
    Update staff member role, full name, discount limit, active status, or reset password.
    Only Owner or Admin can update staff.
    """
    tenant = await TeamService.resolve_active_tenant(current_user, db, requested_tenant_id=tenant_id)
    return await TeamService.update_staff(db, tenant, staff_id, payload, current_user)


@router.delete(
    "/users/{staff_id}",
    summary="Remove Staff Member from Business",
)
async def delete_staff_member(
    staff_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tenant_id: Optional[int] = Query(None, description="Optional tenant ID (for Super Admin)"),
):
    """
    Remove staff member from the current business tenant.
    Only Owner or Admin can remove staff.
    """
    tenant = await TeamService.resolve_active_tenant(current_user, db, requested_tenant_id=tenant_id)
    return await TeamService.delete_staff(db, tenant, staff_id, current_user)


@router.get(
    "/permissions",
    response_model=List[ModulePermissionsGroup],
    summary="Get System Master Permissions Catalog (Grouped by Module)",
)
async def get_permissions_catalog(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns all system permissions grouped under business modules (POS, Sales, Accounting, etc.).
    """
    return await TeamService.get_master_permissions(db)


@router.get(
    "/roles-matrix",
    response_model=List[RoleMatrixItem],
    summary="Get Roles and Permissions Matrix for Active Business",
)
async def get_roles_matrix(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tenant_id: Optional[int] = Query(None, description="Optional tenant ID (for Super Admin)"),
):
    """
    Returns all roles (System Defaults + Tenant Custom Roles) with their assigned permission IDs/codes and member counts.
    """
    tenant = await TeamService.resolve_active_tenant(current_user, db, requested_tenant_id=tenant_id)
    return await TeamService.get_roles_matrix(db, tenant)


@router.post(
    "/roles",
    response_model=RoleMatrixItem,
    status_code=status.HTTP_201_CREATED,
    summary="Create Custom Role with Permissions",
)
async def create_custom_role(
    payload: CreateRoleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tenant_id: Optional[int] = Query(None, description="Optional tenant ID (for Super Admin)"),
):
    """
    Create a new custom business role with a tailored set of permissions.
    Only Owner or Admin can create custom roles.
    """
    tenant = await TeamService.resolve_active_tenant(current_user, db, requested_tenant_id=tenant_id)
    return await TeamService.create_custom_role(db, tenant, payload, current_user)


@router.put(
    "/roles/{role_id}",
    response_model=RoleMatrixItem,
    summary="Update Role Permissions Matrix and Details",
)
async def update_role_permissions(
    role_id: int,
    payload: UpdateRoleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tenant_id: Optional[int] = Query(None, description="Optional tenant ID (for Super Admin)"),
):
    """
    Update permissions matrix for a role.
    Only Owner or Admin can update role permissions. Owner role permissions are permanent.
    """
    tenant = await TeamService.resolve_active_tenant(current_user, db, requested_tenant_id=tenant_id)
    return await TeamService.update_role_matrix(db, tenant, role_id, payload, current_user)


@router.delete(
    "/roles/{role_id}",
    summary="Delete Custom Role",
)
async def delete_custom_role(
    role_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tenant_id: Optional[int] = Query(None, description="Optional tenant ID (for Super Admin)"),
):
    """
    Delete custom role. System roles and roles with active assigned users cannot be deleted.
    Only Owner or Admin can delete custom roles.
    """
    tenant = await TeamService.resolve_active_tenant(current_user, db, requested_tenant_id=tenant_id)
    return await TeamService.delete_custom_role(db, tenant, role_id, current_user)


@router.post(
    "/permissions",
    response_model=PermissionItem,
    status_code=status.HTTP_201_CREATED,
    summary="Create New Permission Action",
)
async def create_permission(
    payload: CreatePermissionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tenant_id: Optional[int] = Query(None, description="Optional tenant ID (for Super Admin)"),
):
    """
    Create a new permission action under a module.
    Only business Owner or Admin can create permissions.
    """
    tenant = await TeamService.resolve_active_tenant(current_user, db, requested_tenant_id=tenant_id)
    return await TeamService.create_permission(db, tenant, payload, current_user)


@router.patch(
    "/permissions/{permission_id}",
    response_model=PermissionItem,
    summary="Update Permission Action",
)
async def update_permission(
    permission_id: int,
    payload: UpdatePermissionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tenant_id: Optional[int] = Query(None, description="Optional tenant ID (for Super Admin)"),
):
    """
    Update an existing permission action.
    Only business Owner or Admin can update permissions.
    """
    tenant = await TeamService.resolve_active_tenant(current_user, db, requested_tenant_id=tenant_id)
    return await TeamService.update_permission(db, tenant, permission_id, payload, current_user)


@router.delete(
    "/permissions/{permission_id}",
    summary="Delete Permission Action",
)
async def delete_permission(
    permission_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tenant_id: Optional[int] = Query(None, description="Optional tenant ID (for Super Admin)"),
):
    """
    Delete a permission action.
    Only business Owner or Admin can delete permissions.
    """
    tenant = await TeamService.resolve_active_tenant(current_user, db, requested_tenant_id=tenant_id)
    return await TeamService.delete_permission(db, tenant, permission_id, current_user)


