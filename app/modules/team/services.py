from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone
from fastapi import HTTPException, status
from sqlalchemy import select, func, or_, delete
from sqlalchemy.orm import selectinload, joinedload

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.core.entitlements import EntitlementService
from app.modules.auth.models import User, Tenant, Role, TenantUser, Permission, RolePermission
from app.modules.plans.models import SubscriptionPlan
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

TENANT_DEFAULT_ROLE_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "Admin": {
        "description": "Business owner and full operations administrator with all permissions.",
        "permissions": ["*"],  # All active permissions of available modules
    },
}


class TeamService:

    @staticmethod
    async def seed_tenant_default_roles(db: AsyncSession, tenant_id: int) -> Role:
        """
        Seed dedicated default roles and basic permissions for a newly created business tenant.
        Returns the tenant's Admin role.
        """
        now = datetime.now(timezone.utc)

        # 1. Fetch all master permissions
        all_perms_res = await db.execute(select(Permission))
        all_perms = all_perms_res.scalars().all()
        perm_map = {p.code: p for p in all_perms}

        # 2. Check existing roles for this tenant
        existing_res = await db.execute(select(Role).where(Role.tenant_id == tenant_id))
        existing_roles = {r.name: r for r in existing_res.scalars().all()}

        admin_role: Optional[Role] = None

        for role_name, tpl in TENANT_DEFAULT_ROLE_TEMPLATES.items():
            if role_name in existing_roles:
                r = existing_roles[role_name]
            else:
                r = Role(
                    tenant_id=tenant_id,
                    name=role_name,
                    description=tpl["description"],
                    is_system=True,
                    created_at=now,
                )
                db.add(r)
                await db.flush()

            if role_name == "Admin":
                admin_role = r

            # Check if role already has permissions assigned
            rp_check = await db.execute(
                select(func.count(RolePermission.id)).where(RolePermission.role_id == r.id)
            )
            if (rp_check.scalar() or 0) == 0:
                if tpl["permissions"] == ["*"]:
                    for p in all_perms:
                        db.add(RolePermission(role_id=r.id, permission_id=p.id, created_at=now))
                else:
                    for code in tpl["permissions"]:
                        if code in perm_map:
                            db.add(RolePermission(role_id=r.id, permission_id=perm_map[code].id, created_at=now))

        await db.flush()
        if not admin_role:
            admin_role = existing_roles.get("Admin")

        return admin_role

    @staticmethod
    async def resolve_active_tenant(
        current_user: User,
        db: AsyncSession,
        requested_tenant_id: Optional[int] = None,
    ) -> Tenant:
        """
        Resolve the business (tenant) context for the current user.
        Super Admin can optionally impersonate via requested_tenant_id.
        """
        if current_user.is_superadmin and requested_tenant_id:
            tenant_stmt = select(Tenant).where(Tenant.id == requested_tenant_id)
            res = await db.execute(tenant_stmt)
            tenant = res.scalar_one_or_none()
            if not tenant:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Business tenant with ID {requested_tenant_id} not found.",
                )
            return tenant

        # Check default_tenant_id first
        if current_user.default_tenant_id:
            membership_stmt = (
                select(TenantUser)
                .where(
                    TenantUser.tenant_id == current_user.default_tenant_id,
                    TenantUser.user_id == current_user.id,
                    TenantUser.is_active.is_(True),
                )
                .options(joinedload(TenantUser.tenant))
            )
            res = await db.execute(membership_stmt)
            membership = res.scalar_one_or_none()
            if membership and membership.tenant:
                return membership.tenant

        # Fallback to any active membership
        fallback_stmt = (
            select(TenantUser)
            .where(
                TenantUser.user_id == current_user.id,
                TenantUser.is_active.is_(True),
            )
            .order_by(TenantUser.is_owner.desc(), TenantUser.id.asc())
            .options(joinedload(TenantUser.tenant))
        )
        res = await db.execute(fallback_stmt)
        first_mem = res.scalar_one_or_none()
        if first_mem and first_mem.tenant:
            return first_mem.tenant

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active business found for your account. Please select or register a business first.",
        )

    @staticmethod
    async def get_tenant_roles(db: AsyncSession, tenant_id: int) -> List[RoleResponse]:
        """
        Retrieve assignable roles for staff members belonging strictly to this business tenant.
        """
        stmt = (
            select(Role)
            .where(Role.tenant_id == tenant_id)
            .order_by(Role.id.asc())
        )
        res = await db.execute(stmt)
        roles = res.scalars().all()
        return [RoleResponse.model_validate(r) for r in roles]


    @staticmethod
    async def get_plan_staff_quota(db: AsyncSession, tenant: Tenant) -> Tuple[int, Optional[int], str]:
        """
        Return (used_staff_count, max_allowed_staff, plan_name).
        """
        # Count current users in this tenant
        count_stmt = select(func.count(TenantUser.id)).where(TenantUser.tenant_id == tenant.id)
        count_res = await db.execute(count_stmt)
        used_staff = count_res.scalar() or 0

        # Resolve tier slug
        tier_val = tenant.plan_tier.value if hasattr(tenant.plan_tier, "value") else str(tenant.plan_tier)
        
        plan_stmt = select(SubscriptionPlan).where(SubscriptionPlan.slug == tier_val)
        plan_res = await db.execute(plan_stmt)
        plan = plan_res.scalar_one_or_none()

        max_staff = None
        plan_name = tier_val.replace("_", " ").title()
        if plan:
            plan_name = plan.name
            if isinstance(plan.limits, dict) and "max_staff" in plan.limits:
                max_staff = int(plan.limits["max_staff"])
        else:
            # Default fallback for free trial
            max_staff = 2

        return used_staff, max_staff, plan_name

    @staticmethod
    async def get_user_permissions_in_tenant(
        current_user: User,
        tenant_id: int,
        db: AsyncSession,
    ) -> List[str]:
        """Resolve all effective permission codes for the user in this tenant."""
        if current_user.is_superadmin:
            return ["*"]

        stmt = (
            select(TenantUser)
            .where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.user_id == current_user.id,
                TenantUser.is_active.is_(True),
            )
            .options(
                selectinload(TenantUser.role)
                .selectinload(Role.role_permissions)
                .joinedload(RolePermission.permission)
            )
        )
        res = await db.execute(stmt)
        membership = res.scalar_one_or_none()
        if not membership:
            return []

        if membership.is_owner or (membership.role and membership.role.name == "Admin"):
            return ["*"]

        if not membership.role:
            return []

        return [
            rp.permission.code
            for rp in membership.role.role_permissions
            if rp.permission
        ]

    @staticmethod
    async def get_user_management_access(
        current_user: User,
        tenant: Tenant,
        db: AsyncSession,
    ) -> bool:
        """
        Returns True if current user is Super Admin, Admin/Owner, or has explicit users CRUD permissions.
        """
        perms = await TeamService.get_user_permissions_in_tenant(current_user, tenant.id, db)
        allowed = {"*", "users:create", "users:edit", "users:delete", "users:manage"}
        return any(p in perms for p in allowed)

    @staticmethod
    async def verify_staff_view_permission(
        current_user: User,
        tenant: Tenant,
        db: AsyncSession,
    ) -> None:
        """
        Check if user has permission to view staff accounts (users:view or any user CRUD or Admin).
        """
        perms = await TeamService.get_user_permissions_in_tenant(current_user, tenant.id, db)
        allowed = {"*", "users:view", "users:create", "users:edit", "users:delete", "users:manage"}
        if not any(p in perms for p in allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. You do not have permission to view staff users.",
            )

    @staticmethod
    async def verify_staff_create_permission(
        current_user: User,
        tenant: Tenant,
        db: AsyncSession,
    ) -> None:
        """Check if user has permission to add/invite staff users."""
        perms = await TeamService.get_user_permissions_in_tenant(current_user, tenant.id, db)
        allowed = {"*", "users:create", "users:manage"}
        if not any(p in perms for p in allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. You do not have permission to add new staff users.",
            )

    @staticmethod
    async def verify_staff_edit_permission(
        current_user: User,
        tenant: Tenant,
        db: AsyncSession,
    ) -> None:
        """Check if user has permission to edit staff users."""
        perms = await TeamService.get_user_permissions_in_tenant(current_user, tenant.id, db)
        allowed = {"*", "users:edit", "users:manage"}
        if not any(p in perms for p in allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. You do not have permission to edit staff users.",
            )

    @staticmethod
    async def verify_staff_delete_permission(
        current_user: User,
        tenant: Tenant,
        db: AsyncSession,
    ) -> None:
        """Check if user has permission to remove/deactivate staff users."""
        perms = await TeamService.get_user_permissions_in_tenant(current_user, tenant.id, db)
        allowed = {"*", "users:delete", "users:manage"}
        if not any(p in perms for p in allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. You do not have permission to remove staff users.",
            )

    @staticmethod
    async def verify_roles_view_permission(
        current_user: User,
        tenant: Tenant,
        db: AsyncSession,
    ) -> None:
        """Check if user has permission to view roles and permissions."""
        perms = await TeamService.get_user_permissions_in_tenant(current_user, tenant.id, db)
        allowed = {"*", "roles:view", "roles:create", "roles:edit", "roles:delete", "roles:manage"}
        if not any(p in perms for p in allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. You do not have permission to view roles and permissions.",
            )

    @staticmethod
    async def verify_roles_create_permission(
        current_user: User,
        tenant: Tenant,
        db: AsyncSession,
    ) -> None:
        """Check if user has permission to create custom roles."""
        perms = await TeamService.get_user_permissions_in_tenant(current_user, tenant.id, db)
        allowed = {"*", "roles:create", "roles:manage"}
        if not any(p in perms for p in allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. You do not have permission to create custom roles.",
            )

    @staticmethod
    async def verify_roles_edit_permission(
        current_user: User,
        tenant: Tenant,
        db: AsyncSession,
    ) -> None:
        """Check if user has permission to update role matrix or permission details."""
        perms = await TeamService.get_user_permissions_in_tenant(current_user, tenant.id, db)
        allowed = {"*", "roles:edit", "roles:manage"}
        if not any(p in perms for p in allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. You do not have permission to update roles or permissions matrix.",
            )

    @staticmethod
    async def verify_roles_delete_permission(
        current_user: User,
        tenant: Tenant,
        db: AsyncSession,
    ) -> None:
        """Check if user has permission to delete custom roles."""
        perms = await TeamService.get_user_permissions_in_tenant(current_user, tenant.id, db)
        allowed = {"*", "roles:delete", "roles:manage"}
        if not any(p in perms for p in allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. You do not have permission to delete roles.",
            )

    @staticmethod
    async def verify_branch_view_permission(
        current_user: User,
        tenant: Tenant,
        db: AsyncSession,
    ) -> None:
        """Check if user has permission to view shops and branches."""
        perms = await TeamService.get_user_permissions_in_tenant(current_user, tenant.id, db)
        allowed = {"*", "branches:view", "branches:create", "branches:edit", "branches:delete", "branches:manage"}
        if not any(p in perms for p in allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. You do not have permission to view shops and branches.",
            )

    @staticmethod
    async def verify_branch_create_permission(
        current_user: User,
        tenant: Tenant,
        db: AsyncSession,
    ) -> None:
        """Check if user has permission to create new shops or godowns."""
        perms = await TeamService.get_user_permissions_in_tenant(current_user, tenant.id, db)
        allowed = {"*", "branches:create", "branches:manage"}
        if not any(p in perms for p in allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. You do not have permission to create new shops or branches.",
            )

    @staticmethod
    async def verify_branch_edit_permission(
        current_user: User,
        tenant: Tenant,
        db: AsyncSession,
    ) -> None:
        """Check if user has permission to update branch locations."""
        perms = await TeamService.get_user_permissions_in_tenant(current_user, tenant.id, db)
        allowed = {"*", "branches:edit", "branches:manage"}
        if not any(p in perms for p in allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. You do not have permission to edit shops or branches.",
            )

    @staticmethod
    async def verify_branch_delete_permission(
        current_user: User,
        tenant: Tenant,
        db: AsyncSession,
    ) -> None:
        """Check if user has permission to delete branch locations."""
        perms = await TeamService.get_user_permissions_in_tenant(current_user, tenant.id, db)
        allowed = {"*", "branches:delete", "branches:manage"}
        if not any(p in perms for p in allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. You do not have permission to delete shops or branches.",
            )


    @staticmethod
    async def list_staff(
        db: AsyncSession,
        tenant: Tenant,
        current_user: User,
    ) -> StaffListResponse:
        """
        List all staff accounts for the current business tenant with quota metadata and manage permission.
        """
        await TeamService.verify_staff_view_permission(current_user, tenant, db)
        used_staff, max_staff, plan_name = await TeamService.get_plan_staff_quota(db, tenant)
        can_manage = await TeamService.get_user_management_access(current_user, tenant, db)

        stmt = (
            select(TenantUser)
            .where(TenantUser.tenant_id == tenant.id)
            .options(
                joinedload(TenantUser.user),
                joinedload(TenantUser.role),
            )
            .order_by(TenantUser.is_owner.desc(), TenantUser.joined_at.asc())
        )
        res = await db.execute(stmt)
        members = res.scalars().all()

        items = []
        for m in members:
            items.append(
                StaffMemberResponse(
                    id=m.id,
                    user_id=m.user_id,
                    full_name=m.user.full_name if m.user else "Unknown",
                    email=m.user.email if m.user else None,
                    phone=m.user.phone if m.user else None,
                    role_id=m.role_id,
                    role_name=m.role.name if m.role else "Staff",
                    is_owner=m.is_owner,
                    is_active=m.is_active,
                    max_discount_pct=float(m.max_discount_pct or 0.0),
                    joined_at=m.joined_at,
                    last_login_at=m.user.last_login_at if m.user else None,
                )
            )

        can_add_more = True if max_staff is None else (used_staff < max_staff)

        return StaffListResponse(
            items=items,
            total=len(items),
            used_staff=used_staff,
            max_staff=max_staff,
            plan_name=plan_name,
            can_add_more=can_add_more,
            can_manage=can_manage,
        )

    @staticmethod
    async def create_staff(
        db: AsyncSession,
        tenant: Tenant,
        payload: StaffCreateRequest,
        current_user: User,
    ) -> StaffMemberResponse:
        """
        Provision or assign a staff member to the business.
        Enforces Owner/Admin permission, plan staff quotas, and uniqueness.
        """
        await TeamService.verify_staff_create_permission(current_user, tenant, db)

        if not payload.phone and not payload.email:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Either a mobile phone number or email address is required.",
            )

        used_staff, max_staff, plan_name = await TeamService.get_plan_staff_quota(db, tenant)
        await EntitlementService.check_quota_limit(
            db=db,
            tenant=tenant,
            resource_key="max_staff",
            current_count=used_staff,
            resource_name="Staff user accounts",
        )

        # Verify role exists and is allowed
        role_stmt = select(Role).where(
            Role.id == payload.role_id,
            or_(Role.tenant_id == tenant.id, Role.tenant_id.is_(None)),
        )
        role_res = await db.execute(role_stmt)
        role = role_res.scalar_one_or_none()
        if not role:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid role selected for staff member.",
            )

        # Check existing user
        query_conditions = []
        if payload.phone:
            query_conditions.append(User.phone == payload.phone)
        if payload.email:
            query_conditions.append(User.email == payload.email)

        existing_user_stmt = select(User).where(or_(*query_conditions))
        existing_res = await db.execute(existing_user_stmt)
        user = existing_res.scalar_one_or_none()

        if user:
            # Check if already a member of this tenant
            check_mem = select(TenantUser).where(
                TenantUser.tenant_id == tenant.id,
                TenantUser.user_id == user.id,
            )
            mem_res = await db.execute(check_mem)
            if mem_res.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A user with this mobile number or email already exists in your business team.",
                )
        else:
            # Create new user identity
            user = User(
                full_name=payload.full_name,
                phone=payload.phone,
                email=payload.email,
                password_hash=hash_password(payload.password),
                is_phone_verified=True if payload.phone else False,
                is_email_verified=True if payload.email else False,
                is_active=True,
                default_tenant_id=tenant.id,
                created_at=datetime.now(timezone.utc),
            )
            db.add(user)
            await db.flush()

        # Create TenantUser link
        membership = TenantUser(
            tenant_id=tenant.id,
            user_id=user.id,
            role_id=role.id,
            is_owner=False,
            is_active=True,
            max_discount_pct=payload.max_discount_pct,
            joined_at=datetime.now(timezone.utc),
        )
        db.add(membership)
        await db.commit()
        await db.refresh(membership)
        await db.refresh(user)

        return StaffMemberResponse(
            id=membership.id,
            user_id=user.id,
            full_name=user.full_name,
            email=user.email,
            phone=user.phone,
            role_id=role.id,
            role_name=role.name,
            is_owner=False,
            is_active=membership.is_active,
            max_discount_pct=float(membership.max_discount_pct or 0.0),
            joined_at=membership.joined_at,
            last_login_at=user.last_login_at,
        )

    @staticmethod
    async def update_staff(
        db: AsyncSession,
        tenant: Tenant,
        staff_id: int,
        payload: StaffUpdateRequest,
        current_user: User,
    ) -> StaffMemberResponse:
        """
        Update staff member attributes (role, full_name, discount limit, active status, password).
        Enforces Owner/Admin permission.
        """
        await TeamService.verify_staff_edit_permission(current_user, tenant, db)

        stmt = (
            select(TenantUser)
            .where(
                TenantUser.id == staff_id,
                TenantUser.tenant_id == tenant.id,
            )
            .options(
                joinedload(TenantUser.user),
                joinedload(TenantUser.role),
            )
        )
        res = await db.execute(stmt)
        membership = res.scalar_one_or_none()
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Staff member not found.",
            )

        if membership.is_owner and payload.is_active is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The business owner account cannot be deactivated.",
            )

        # Update role if provided
        if payload.role_id is not None:
            if membership.is_owner:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Owner role cannot be changed directly.",
                )
            role_stmt = select(Role).where(
                Role.id == payload.role_id,
                or_(Role.tenant_id == tenant.id, Role.tenant_id.is_(None)),
            )
            role_res = await db.execute(role_stmt)
            role = role_res.scalar_one_or_none()
            if not role:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid role selected.",
                )
            membership.role_id = role.id

        if payload.max_discount_pct is not None:
            membership.max_discount_pct = payload.max_discount_pct

        if payload.is_active is not None and not membership.is_owner:
            membership.is_active = payload.is_active

        # Update User fields if provided
        if payload.full_name and membership.user:
            membership.user.full_name = payload.full_name

        if payload.password and membership.user:
            membership.user.password_hash = hash_password(payload.password)

        await db.commit()
        await db.refresh(membership)
        if membership.user:
            await db.refresh(membership.user)

        # Reload role
        role_stmt = select(Role).where(Role.id == membership.role_id)
        role_res = await db.execute(role_stmt)
        current_role = role_res.scalar_one_or_none()

        return StaffMemberResponse(
            id=membership.id,
            user_id=membership.user_id,
            full_name=membership.user.full_name if membership.user else "Unknown",
            email=membership.user.email if membership.user else None,
            phone=membership.user.phone if membership.user else None,
            role_id=membership.role_id,
            role_name=current_role.name if current_role else "Staff",
            is_owner=membership.is_owner,
            is_active=membership.is_active,
            max_discount_pct=float(membership.max_discount_pct or 0.0),
            joined_at=membership.joined_at,
            last_login_at=membership.user.last_login_at if membership.user else None,
        )

    @staticmethod
    async def delete_staff(
        db: AsyncSession,
        tenant: Tenant,
        staff_id: int,
        current_user: User,
    ) -> Dict[str, Any]:
        """
        Remove staff member from business tenant.
        Enforces Owner/Admin permission.
        """
        await TeamService.verify_staff_delete_permission(current_user, tenant, db)

        stmt = select(TenantUser).where(
            TenantUser.id == staff_id,
            TenantUser.tenant_id == tenant.id,
        )
        res = await db.execute(stmt)
        membership = res.scalar_one_or_none()
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Staff member not found.",
            )

        if membership.is_owner:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The primary business owner account cannot be deleted.",
            )

        await db.delete(membership)
        await db.commit()

        return {"message": "Staff member removed successfully."}

    @staticmethod
    async def get_master_permissions(db: AsyncSession) -> List[ModulePermissionsGroup]:
        """
        Fetch all master permissions catalog grouped by module.
        """
        module_labels = {
            "users": "Users & Staff",
            "roles": "Roles & Permissions",
            "pos": "POS Counter Billing",
            "sales": "Sales & Delivery Challans",
            "purchase": "Purchasing & Vendor Bills",
            "inventory": "Inventory & Godowns",
            "accounting": "Accounts & General Ledger",
            "parties": "Customers & Suppliers (Khata)",
            "reports": "Financial & Operational Reports",
            "settings": "Business Profile & Tax Settings",
        }

        stmt = select(Permission).order_by(Permission.module.asc(), Permission.id.asc())
        res = await db.execute(stmt)
        permissions = res.scalars().all()

        groups_map: Dict[str, List[PermissionItem]] = {}
        for p in permissions:
            groups_map.setdefault(p.module, []).append(PermissionItem.model_validate(p))

        result = []
        # Maintain sensible business order (active modules first)
        order = ["users", "roles", "pos", "sales", "purchase", "inventory", "accounting", "parties", "reports", "settings"]
        for mod in order:
            if mod in groups_map:
                result.append(
                    ModulePermissionsGroup(
                        module=mod,
                        label=module_labels.get(mod, mod.capitalize()),
                        permissions=groups_map[mod],
                    )
                )

        # Append any other modules if exist
        for mod, items in groups_map.items():
            if mod not in order:
                result.append(
                    ModulePermissionsGroup(
                        module=mod,
                        label=module_labels.get(mod, mod.capitalize()),
                        permissions=items,
                    )
                )

        return result

    @staticmethod
    async def get_roles_matrix(
        db: AsyncSession,
        tenant: Tenant,
    ) -> List[RoleMatrixItem]:
        """
        Retrieve all available roles strictly for this business tenant with their assigned permissions and member counts.
        """
        # Load all roles strictly belonging to this tenant
        roles_stmt = (
            select(Role)
            .where(
                Role.tenant_id == tenant.id,
                Role.name != "Super Admin",
            )
            .options(
                selectinload(Role.role_permissions).joinedload(RolePermission.permission)
            )
            .order_by(Role.is_system.desc(), Role.id.asc())
        )
        roles_res = await db.execute(roles_stmt)
        roles = roles_res.scalars().all()

        # If tenant has no roles yet (e.g. newly created or legacy), auto-seed now!
        if not roles:
            await TeamService.seed_tenant_default_roles(db, tenant.id)
            roles_res = await db.execute(roles_stmt)
            roles = roles_res.scalars().all()

        # Get all master permissions for Admin full access representation
        all_perms_stmt = select(Permission)
        all_perms_res = await db.execute(all_perms_stmt)
        all_permissions = all_perms_res.scalars().all()

        # Count active members per role in this tenant
        count_stmt = (
            select(TenantUser.role_id, func.count(TenantUser.id))
            .where(TenantUser.tenant_id == tenant.id)
            .group_by(TenantUser.role_id)
        )
        count_res = await db.execute(count_stmt)
        member_counts = dict(count_res.all())

        matrix_items = []
        for r in roles:
            user_count = member_counts.get(r.id, 0)

            p_ids = [rp.permission_id for rp in r.role_permissions if rp.permission]
            p_codes = [rp.permission.code for rp in r.role_permissions if rp.permission]
            if r.name == "Admin" and r.is_system and not p_ids:
                p_ids = [p.id for p in all_permissions]
                p_codes = [p.code for p in all_permissions]

            matrix_items.append(
                RoleMatrixItem(
                    id=r.id,
                    tenant_id=r.tenant_id,
                    name=r.name,
                    description=r.description,
                    is_system=r.is_system,
                    user_count=user_count,
                    permission_ids=p_ids,
                    permission_codes=p_codes,
                )
            )

        return matrix_items

    @staticmethod
    async def create_custom_role(
        db: AsyncSession,
        tenant: Tenant,
        payload: CreateRoleRequest,
        current_user: User,
    ) -> RoleMatrixItem:
        """
        Create a new custom business role with assigned permissions.
        Enforces Admin permission.
        """
        await TeamService.verify_roles_create_permission(current_user, tenant, db)

        # Enforce plan feature entitlement for creating custom roles
        if not current_user.is_superadmin:
            await EntitlementService.check_feature_access(
                db=db,
                tenant=tenant,
                feature_key="custom_roles",
                feature_name="Custom Roles & Permissions",
            )

        # Check unique name strictly within this tenant
        exist_stmt = select(Role).where(
            Role.tenant_id == tenant.id,
            func.lower(Role.name) == payload.name.strip().lower(),
        )
        exist_res = await db.execute(exist_stmt)
        if exist_res.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"A role with the name '{payload.name}' already exists in your business.",
            )

        new_role = Role(
            tenant_id=tenant.id,
            name=payload.name.strip(),
            description=payload.description.strip() if payload.description else None,
            is_system=False,
            created_at=datetime.now(timezone.utc),
        )
        db.add(new_role)
        await db.flush()

        # Assign permissions
        assigned_pids = []
        assigned_codes = []
        if payload.permission_ids:
            perms_stmt = select(Permission).where(Permission.id.in_(payload.permission_ids))
            perms_res = await db.execute(perms_stmt)
            valid_perms = perms_res.scalars().all()

            for p in valid_perms:
                rp = RolePermission(
                    role_id=new_role.id,
                    permission_id=p.id,
                    created_at=datetime.now(timezone.utc),
                )
                db.add(rp)
                assigned_pids.append(p.id)
                assigned_codes.append(p.code)

        await db.commit()
        await db.refresh(new_role)

        return RoleMatrixItem(
            id=new_role.id,
            tenant_id=new_role.tenant_id,
            name=new_role.name,
            description=new_role.description,
            is_system=False,
            user_count=0,
            permission_ids=assigned_pids,
            permission_codes=assigned_codes,
        )

    @staticmethod
    async def update_role_matrix(
        db: AsyncSession,
        tenant: Tenant,
        role_id: int,
        payload: UpdateRoleRequest,
        current_user: User,
    ) -> RoleMatrixItem:
        """
        Update role permissions and metadata for a role belonging to this business.
        Admin role has protected full access.
        All other roles (Cashier, Salesman, Accountant, Warehouse Manager, Custom) can have permissions updated.
        """
        await TeamService.verify_roles_edit_permission(current_user, tenant, db)

        role_stmt = (
            select(Role)
            .where(
                Role.id == role_id,
                Role.tenant_id == tenant.id,
            )
            .options(selectinload(Role.role_permissions).joinedload(RolePermission.permission))
        )
        res = await db.execute(role_stmt)
        role = res.scalar_one_or_none()
        if not role:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Role not found in your business.",
            )

        # Update name and description if not system role
        if not role.is_system:
            if payload.name:
                role.name = payload.name.strip()
            if payload.description is not None:
                role.description = payload.description.strip() if payload.description else None

        # Update permissions if provided
        assigned_pids = []
        assigned_codes = []
        if payload.permission_ids is not None:
            # 1. Atomic delete of existing permissions for this role and flush
            await db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
            await db.flush()

            # 2. Insert new permissions (deduplicated)
            unique_pids = list(dict.fromkeys(payload.permission_ids))
            if unique_pids:
                perms_stmt = select(Permission).where(Permission.id.in_(unique_pids))
                perms_res = await db.execute(perms_stmt)
                valid_perms = perms_res.scalars().all()

                for p in valid_perms:
                    rp = RolePermission(
                        role_id=role.id,
                        permission_id=p.id,
                        created_at=datetime.now(timezone.utc),
                    )
                    db.add(rp)
                    assigned_pids.append(p.id)
                    assigned_codes.append(p.code)
        else:
            assigned_pids = [rp.permission_id for rp in role.role_permissions if rp.permission]
            assigned_codes = [rp.permission.code for rp in role.role_permissions if rp.permission]

        await db.commit()
        await db.refresh(role)


        # Count users
        count_stmt = select(func.count(TenantUser.id)).where(
            TenantUser.role_id == role.id,
            TenantUser.tenant_id == tenant.id,
        )
        count_res = await db.execute(count_stmt)
        user_count = count_res.scalar() or 0

        return RoleMatrixItem(
            id=role.id,
            tenant_id=role.tenant_id,
            name=role.name,
            description=role.description,
            is_system=role.is_system,
            user_count=user_count,
            permission_ids=assigned_pids,
            permission_codes=assigned_codes,
        )

    @staticmethod
    async def delete_custom_role(
        db: AsyncSession,
        tenant: Tenant,
        role_id: int,
        current_user: User,
    ) -> Dict[str, Any]:
        """
        Delete a custom role.
        Enforces Owner/Admin permission, prevents deleting system roles or roles with active members.
        """
        await TeamService.verify_roles_delete_permission(current_user, tenant, db)

        role_stmt = select(Role).where(
            Role.id == role_id,
            Role.tenant_id == tenant.id,
        )
        res = await db.execute(role_stmt)
        role = res.scalar_one_or_none()
        if not role:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Custom role not found or cannot be deleted.",
            )

        if role.is_system:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Default system roles cannot be deleted.",
            )

        # Check if users are assigned
        count_stmt = select(func.count(TenantUser.id)).where(
            TenantUser.role_id == role.id,
            TenantUser.tenant_id == tenant.id,
        )
        count_res = await db.execute(count_stmt)
        user_count = count_res.scalar() or 0
        if user_count > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot delete role '{role.name}'. {user_count} staff user(s) are currently assigned to this role. Please reassign them first.",
            )

        await db.delete(role)
        await db.commit()

        return {"message": f"Role '{role.name}' deleted successfully."}

    @staticmethod
    async def create_permission(
        db: AsyncSession,
        tenant: Tenant,
        payload: CreatePermissionRequest,
        current_user: User,
    ) -> PermissionItem:
        """
        Create a new permission action under a module.
        Enforces Admin/Owner permission.
        """
        await TeamService.verify_roles_edit_permission(current_user, tenant, db)

        code_clean = payload.code.strip().lower()
        exist_stmt = select(Permission).where(Permission.code == code_clean)
        exist_res = await db.execute(exist_stmt)
        if exist_res.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Permission code '{code_clean}' already exists. Please choose a unique code.",
            )

        new_perm = Permission(
            module=payload.module.strip().lower(),
            code=code_clean,
            name=payload.name.strip(),
            description=payload.description.strip() if payload.description else None,
            created_at=datetime.now(timezone.utc),
        )
        db.add(new_perm)
        await db.flush()

        # Automatically link this permission to this tenant's Admin role
        admin_stmt = select(Role).where(Role.tenant_id == tenant.id, Role.name == "Admin")
        admin_res = await db.execute(admin_stmt)
        admin_role = admin_res.scalar_one_or_none()
        if admin_role:
            db.add(RolePermission(role_id=admin_role.id, permission_id=new_perm.id, created_at=datetime.now(timezone.utc)))

        await db.commit()
        await db.refresh(new_perm)

        return PermissionItem.model_validate(new_perm)

    @staticmethod
    async def update_permission(
        db: AsyncSession,
        tenant: Tenant,
        permission_id: int,
        payload: UpdatePermissionRequest,
        current_user: User,
    ) -> PermissionItem:
        """
        Update an existing permission's name, description, module or code.
        """
        await TeamService.verify_roles_edit_permission(current_user, tenant, db)

        perm_stmt = select(Permission).where(Permission.id == permission_id)
        res = await db.execute(perm_stmt)
        perm = res.scalar_one_or_none()
        if not perm:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Permission not found.",
            )

        if payload.code:
            new_code = payload.code.strip().lower()
            if new_code != perm.code:
                exist_check = await db.execute(select(Permission).where(Permission.code == new_code))
                if exist_check.scalar_one_or_none():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Permission code '{new_code}' already exists.",
                    )
                perm.code = new_code

        if payload.name:
            perm.name = payload.name.strip()
        if payload.module:
            perm.module = payload.module.strip().lower()
        if payload.description is not None:
            perm.description = payload.description.strip() if payload.description else None

        await db.commit()
        await db.refresh(perm)
        return PermissionItem.model_validate(perm)

    @staticmethod
    async def delete_permission(
        db: AsyncSession,
        tenant: Tenant,
        permission_id: int,
        current_user: User,
    ) -> Dict[str, Any]:
        """
        Delete a permission from the system.
        """
        await TeamService.verify_roles_delete_permission(current_user, tenant, db)

        perm_stmt = select(Permission).where(Permission.id == permission_id)
        res = await db.execute(perm_stmt)
        perm = res.scalar_one_or_none()
        if not perm:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Permission not found.",
            )

        perm_name = perm.name
        # Delete permission (RolePermission cascade cleans up automatically)
        await db.delete(perm)
        await db.commit()

        return {"message": f"Permission '{perm_name}' deleted successfully."}


