from typing import Optional, List
from datetime import datetime, timezone
from fastapi import HTTPException, status
from sqlalchemy import select, func, or_, and_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.entitlements import EntitlementService
from app.modules.auth.models import Tenant
from app.modules.branches.models import Branch, BranchType
from app.modules.branches.schemas import (
    BranchCreate,
    BranchUpdate,
    BranchResponse,
    BranchListResponse,
    BranchQuotaUsage,
)


class BranchService:

    @staticmethod
    async def ensure_default_branch(db: AsyncSession, tenant: Tenant) -> Branch:
        """
        Ensures a business has at least one primary main branch (auto-created on onboarding/first access).
        """
        stmt = select(Branch).where(Branch.tenant_id == tenant.id).order_by(Branch.id.asc())
        res = await db.execute(stmt)
        existing = res.scalars().all()

        if existing:
            # If none is marked main, mark the first one as main
            has_main = any(b.is_main for b in existing)
            if not has_main:
                existing[0].is_main = True
                await db.commit()
                await db.refresh(existing[0])
            return existing[0]

        # Auto-create the #1 Primary Headquarters Branch from Tenant profile
        now = datetime.now(timezone.utc)
        default_branch = Branch(
            tenant_id=tenant.id,
            name=f"{tenant.name} (Main)",
            code="BR-01",
            type=BranchType.RETAIL_SHOP,
            phone=tenant.phone,
            email=tenant.email,
            city=tenant.city,
            address=tenant.address or f"{tenant.city}, Pakistan",
            is_main=True,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(default_branch)
        await db.commit()
        await db.refresh(default_branch)
        return default_branch

    @staticmethod
    async def list_branches(
        db: AsyncSession,
        tenant: Tenant,
        search: Optional[str] = None,
        city: Optional[str] = None,
        branch_type: Optional[BranchType] = None,
        is_active: Optional[bool] = None,
    ) -> BranchListResponse:
        """
        List all branches of the current business tenant with quota consumption breakdown.
        """
        # Ensure default branch exists
        await BranchService.ensure_default_branch(db, tenant)

        query = select(Branch).where(Branch.tenant_id == tenant.id)

        if search:
            s = f"%{search.strip()}%"
            query = query.where(
                or_(
                    Branch.name.ilike(s),
                    Branch.code.ilike(s),
                    Branch.city.ilike(s),
                    Branch.phone.ilike(s),
                )
            )

        if city:
            query = query.where(Branch.city.ilike(f"%{city.strip()}%"))

        if branch_type:
            query = query.where(Branch.type == branch_type)

        if is_active is not None:
            query = query.where(Branch.is_active.is_(is_active))

        query = query.order_by(Branch.is_main.desc(), Branch.name.asc())
        res = await db.execute(query)
        branches = res.scalars().all()

        # Compute branch quota
        quota_summary = await EntitlementService.get_tenant_quota_summary(db, tenant)
        b_usage = quota_summary["usage"]["branches"]

        quota_info = BranchQuotaUsage(
            used=b_usage["used"],
            max=b_usage["max"],
            is_unlimited=b_usage["is_unlimited"],
            is_reached=b_usage["is_reached"],
            plan_name=quota_summary["plan_name"],
            plan_slug=quota_summary["plan_slug"],
        )

        return BranchListResponse(
            branches=[BranchResponse.model_validate(b) for b in branches],
            quota=quota_info,
            total=len(branches),
        )

    @staticmethod
    async def get_branch(db: AsyncSession, tenant_id: int, branch_id: int) -> Branch:
        """
        Get a specific branch by ID ensuring multi-tenant isolation.
        """
        stmt = select(Branch).where(Branch.tenant_id == tenant_id, Branch.id == branch_id)
        res = await db.execute(stmt)
        branch = res.scalar_one_or_none()
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Shop/Branch with ID {branch_id} was not found in your business directory.",
            )
        return branch

    @staticmethod
    async def create_branch(db: AsyncSession, tenant: Tenant, payload: BranchCreate) -> Branch:
        """
        Create a new branch with machine-enforced plan quota checks.
        """
        # 1. Enforce Plan Limits (max_branches)
        count_stmt = select(func.count(Branch.id)).where(
            Branch.tenant_id == tenant.id,
            Branch.is_active.is_(True),
        )
        count_res = await db.execute(count_stmt)
        active_count = count_res.scalar() or 0

        await EntitlementService.check_quota_limit(
            db=db,
            tenant=tenant,
            quota_key="max_branches",
            current_count=active_count,
            resource_name="Shops / Branches",
        )

        # 2. Check duplicate code or name within the same tenant
        dup_stmt = select(Branch).where(
            Branch.tenant_id == tenant.id,
            or_(
                func.lower(Branch.code) == payload.code.strip().lower(),
                func.lower(Branch.name) == payload.name.strip().lower(),
            ),
        )
        dup_res = await db.execute(dup_stmt)
        existing_dup = dup_res.scalar_one_or_none()
        if existing_dup:
            if existing_dup.code.lower() == payload.code.strip().lower():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"A branch with code '{payload.code.strip()}' already exists. Please choose a unique code.",
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A branch named '{payload.name.strip()}' already exists. Please choose a distinct name.",
            )

        now = datetime.now(timezone.utc)

        # 3. Handle is_main logic
        if payload.is_main:
            # Unset any existing main branch
            await db.execute(
                update(Branch)
                .where(Branch.tenant_id == tenant.id)
                .values(is_main=False)
            )

        new_branch = Branch(
            tenant_id=tenant.id,
            name=payload.name.strip(),
            code=payload.code.strip().upper(),
            type=payload.type,
            phone=payload.phone.strip(),
            email=payload.email.strip() if payload.email else None,
            city=payload.city.strip(),
            address=payload.address.strip() if payload.address else None,
            fbr_pos_id=payload.fbr_pos_id.strip() if payload.fbr_pos_id else None,
            is_main=payload.is_main,
            is_active=payload.is_active,
            created_at=now,
            updated_at=now,
        )

        db.add(new_branch)
        await db.commit()
        await db.refresh(new_branch)
        return new_branch

    @staticmethod
    async def update_branch(
        db: AsyncSession,
        tenant_id: int,
        branch_id: int,
        payload: BranchUpdate,
    ) -> Branch:
        """
        Update an existing branch details with uniqueness validation.
        """
        branch = await BranchService.get_branch(db, tenant_id, branch_id)

        # Check unique code or name if changed
        if payload.code and payload.code.strip().upper() != branch.code:
            code_dup_stmt = select(Branch).where(
                Branch.tenant_id == tenant_id,
                Branch.id != branch_id,
                func.lower(Branch.code) == payload.code.strip().lower(),
            )
            code_res = await db.execute(code_dup_stmt)
            if code_res.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Branch code '{payload.code.strip()}' is already used by another branch.",
                )
            branch.code = payload.code.strip().upper()

        if payload.name and payload.name.strip() != branch.name:
            name_dup_stmt = select(Branch).where(
                Branch.tenant_id == tenant_id,
                Branch.id != branch_id,
                func.lower(Branch.name) == payload.name.strip().lower(),
            )
            name_res = await db.execute(name_dup_stmt)
            if name_res.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"A branch named '{payload.name.strip()}' already exists.",
                )
            branch.name = payload.name.strip()

        if payload.type is not None:
            branch.type = payload.type
        if payload.phone is not None:
            branch.phone = payload.phone.strip()
        if payload.email is not None:
            branch.email = payload.email.strip() if payload.email else None
        if payload.city is not None:
            branch.city = payload.city.strip()
        if payload.address is not None:
            branch.address = payload.address.strip() if payload.address else None
        if payload.fbr_pos_id is not None:
            branch.fbr_pos_id = payload.fbr_pos_id.strip() if payload.fbr_pos_id else None
        if payload.is_active is not None:
            branch.is_active = payload.is_active

        if payload.is_main is True and not branch.is_main:
            # Setting this branch as main: unset all others
            await db.execute(
                update(Branch)
                .where(Branch.tenant_id == tenant_id, Branch.id != branch_id)
                .values(is_main=False)
            )
            branch.is_main = True

        branch.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(branch)
        return branch

    @staticmethod
    async def delete_branch(db: AsyncSession, tenant_id: int, branch_id: int) -> dict:
        """
        Delete a branch ensuring at least one main branch remains.
        """
        branch = await BranchService.get_branch(db, tenant_id, branch_id)

        # Safety check: Cannot delete the only main branch
        count_stmt = select(func.count(Branch.id)).where(Branch.tenant_id == tenant_id)
        count_res = await db.execute(count_stmt)
        total_branches = count_res.scalar() or 0

        if total_branches <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Your business must have at least one primary shop/branch. You cannot delete your only location.",
            )

        if branch.is_main:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete the primary headquarters branch. Please designate another branch as Main first.",
            )

        await db.delete(branch)
        await db.commit()
        return {"message": f"Branch '{branch.name}' was successfully removed."}

    @staticmethod
    async def set_main_branch(db: AsyncSession, tenant_id: int, branch_id: int) -> Branch:
        """
        Designate a branch as the primary business headquarters.
        """
        branch = await BranchService.get_branch(db, tenant_id, branch_id)

        # Unset all
        await db.execute(
            update(Branch)
            .where(Branch.tenant_id == tenant_id)
            .values(is_main=False)
        )

        branch.is_main = True
        branch.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(branch)
        return branch
