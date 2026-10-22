import re
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Tuple
from fastapi import HTTPException, status
from sqlalchemy import select, func, or_, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.modules.auth.models import (
    Tenant,
    User,
    Role,
    TenantUser,
    AuthAuditLog,
    TenantStatus,
    SubscriptionTier,
    OTPChannel,
)
from app.modules.admin.schemas import (
    CreateTenantRequest,
    CreateTenantResponse,
    TenantListItemResponse,
    TenantOwnerSummary,
    PaginatedTenantsResponse,
    UpdateTenantStatusRequest,
)
from app.modules.team.services import TeamService


def slugify(text: str) -> str:
    """Generate a clean URL-friendly slug from a business name."""
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    s = re.sub(r"^-+|-+$", "", s)
    return s[:45] if s else "business"


class AdminTenantService:
    @staticmethod
    async def create_tenant(
        db: AsyncSession,
        payload: CreateTenantRequest,
        admin_user: User,
    ) -> CreateTenantResponse:
        """
        Assisted Onboarding: Super Admin creates a new Business (Tenant)
        and provisions its Primary Owner with immediate access.
        """
        now = datetime.now(timezone.utc)

        # 1. Generate Unique Slug
        base_slug = slugify(payload.name)
        slug = base_slug
        counter = 1

        while True:
            stmt = select(Tenant).where(Tenant.slug == slug)
            res = await db.execute(stmt)
            if not res.scalar_one_or_none():
                break
            counter += 1
            slug = f"{base_slug}-{counter}"

        # 2. Find or Create Owner User
        owner_phone = payload.owner_phone.strip()
        owner_email = payload.owner_email.strip().lower() if payload.owner_email else None

        # Check existing user
        stmt_u = select(User).where(
            or_(
                User.phone == owner_phone,
                User.email == owner_email if owner_email else False,
            )
        )
        res_u = await db.execute(stmt_u)
        owner_user = res_u.scalar_one_or_none()

        is_new_user = False
        if not owner_user:
            is_new_user = True
            owner_user = User(
                full_name=payload.owner_full_name.strip(),
                phone=owner_phone,
                email=owner_email,
                password_hash=hash_password(payload.owner_password),
                is_phone_verified=True,  # Pre-verified by Super Admin
                is_email_verified=True if owner_email else False,
                is_active=True,
                is_superadmin=False,
                preferred_otp_channel=OTPChannel.PHONE_SMS,
                created_at=now,
            )
            db.add(owner_user)
            await db.flush()

        # 3. Create Tenant
        subscription_ends_at = now + timedelta(days=payload.subscription_days)
        tenant_status = (
            TenantStatus.TRIAL
            if payload.plan_tier == SubscriptionTier.FREE_TRIAL
            else TenantStatus.ACTIVE
        )

        tenant = Tenant(
            slug=slug,
            name=payload.name.strip(),
            legal_name=payload.legal_name.strip() if payload.legal_name else None,
            ntn=payload.ntn.strip() if payload.ntn else None,
            strn=payload.strn.strip() if payload.strn else None,
            phone=payload.phone.strip(),
            email=payload.email.strip().lower() if payload.email else None,
            city=payload.city.strip(),
            address=payload.address.strip() if payload.address else None,
            currency="PKR",
            status=tenant_status,
            plan_tier=payload.plan_tier,
            trial_ends_at=now + timedelta(days=14),
            subscription_ends_at=subscription_ends_at,
            is_read_only=False,
            created_at=now,
        )
        db.add(tenant)
        await db.flush()

        # Update owner default tenant if not set
        if not owner_user.default_tenant_id:
            owner_user.default_tenant_id = tenant.id

        # 4. Provision Dedicated Tenant Roles and assign owner to Admin role
        admin_role = await TeamService.seed_tenant_default_roles(db, tenant.id)

        # 5. Create TenantUser Membership
        membership = TenantUser(
            tenant_id=tenant.id,
            user_id=owner_user.id,
            role_id=admin_role.id,
            is_owner=True,
            is_active=True,
            max_discount_pct=100.0,
            joined_at=now,
        )
        db.add(membership)

        # 6. Audit Log
        audit = AuthAuditLog(
            user_id=admin_user.id,
            tenant_id=tenant.id,
            event="TENANT_CREATED_BY_SUPERADMIN",
            metadata_={
                "tenant_name": tenant.name,
                "plan_tier": tenant.plan_tier,
                "owner_id": owner_user.id,
                "owner_phone": owner_user.phone,
                "owner_email": owner_user.email,
                "is_new_user": is_new_user,
            },
        )
        db.add(audit)

        await db.commit()
        await db.refresh(tenant)

        owner_summary = TenantOwnerSummary(
            id=owner_user.id,
            full_name=owner_user.full_name,
            phone=owner_user.phone,
            email=owner_user.email,
        )

        item_response = TenantListItemResponse(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            city=tenant.city,
            phone=tenant.phone,
            email=tenant.email,
            status=tenant.status,
            plan_tier=tenant.plan_tier,
            trial_ends_at=tenant.trial_ends_at,
            subscription_ends_at=tenant.subscription_ends_at,
            is_read_only=tenant.is_read_only,
            owner=owner_summary,
            total_users=1,
            created_at=tenant.created_at,
        )

        return CreateTenantResponse(
            tenant=item_response,
            owner_credentials={
                "identifier": owner_user.phone or owner_user.email,
                "password": payload.owner_password,
                "full_name": owner_user.full_name,
                "is_new_account": is_new_user,
            },
            message=f"Business '{tenant.name}' provisioned successfully with Owner '{owner_user.full_name}'.",
        )

    @staticmethod
    async def list_tenants(
        db: AsyncSession,
        search: Optional[str] = None,
        status_filter: Optional[TenantStatus] = None,
        plan_filter: Optional[SubscriptionTier] = None,
        page: int = 1,
        size: int = 20,
    ) -> PaginatedTenantsResponse:
        """Query all registered tenant businesses with search, filters and pagination."""
        offset = (page - 1) * size

        # Base query
        query = select(Tenant).options(
            selectinload(Tenant.memberships).joinedload(TenantUser.user)
        )

        # Filters
        if search:
            s_term = f"%{search.strip()}%"
            query = query.where(
                or_(
                    Tenant.name.ilike(s_term),
                    Tenant.slug.ilike(s_term),
                    Tenant.city.ilike(s_term),
                    Tenant.phone.ilike(s_term),
                    Tenant.email.ilike(s_term),
                )
            )

        if status_filter:
            query = query.where(Tenant.status == status_filter)

        if plan_filter:
            query = query.where(Tenant.plan_tier == plan_filter)

        # Count total
        subq = query.order_by(None).subquery()
        count_query = select(func.count()).select_from(subq)
        total = (await db.execute(count_query)).scalar_one()

        # Order and paginate
        query = query.order_by(desc(Tenant.created_at)).offset(offset).limit(size)
        results = (await db.execute(query)).scalars().all()

        items = []
        for t in results:
            owner_user = None
            total_users = len(t.memberships)
            for m in t.memberships:
                if m.is_owner and m.user:
                    owner_user = TenantOwnerSummary(
                        id=m.user.id,
                        full_name=m.user.full_name,
                        phone=m.user.phone,
                        email=m.user.email,
                    )
                    break

            items.append(
                TenantListItemResponse(
                    id=t.id,
                    name=t.name,
                    slug=t.slug,
                    city=t.city,
                    phone=t.phone,
                    email=t.email,
                    status=t.status,
                    plan_tier=t.plan_tier,
                    trial_ends_at=t.trial_ends_at,
                    subscription_ends_at=t.subscription_ends_at,
                    is_read_only=t.is_read_only,
                    owner=owner_user,
                    total_users=total_users,
                    created_at=t.created_at,
                )
            )

        pages = (total + size - 1) // size if total > 0 else 1

        return PaginatedTenantsResponse(
            items=items,
            total=total,
            page=page,
            size=size,
            pages=pages,
        )

    @staticmethod
    async def update_tenant_status(
        db: AsyncSession,
        tenant_id: int,
        payload: UpdateTenantStatusRequest,
        admin_user: User,
    ) -> TenantListItemResponse:
        """Update tenant lifecycle status, read-only mode, or extend subscription."""
        stmt = (
            select(Tenant)
            .where(Tenant.id == tenant_id)
            .options(selectinload(Tenant.memberships).joinedload(TenantUser.user))
        )
        res = await db.execute(stmt)
        tenant = res.scalar_one_or_none()

        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Business with ID {tenant_id} not found.",
            )

        now = datetime.now(timezone.utc)
        changes = {}

        if payload.status:
            changes["old_status"] = tenant.status
            changes["new_status"] = payload.status
            tenant.status = payload.status

        if payload.is_read_only is not None:
            changes["is_read_only"] = payload.is_read_only
            tenant.is_read_only = payload.is_read_only

        if payload.extend_days:
            base_date = tenant.subscription_ends_at or now
            tenant.subscription_ends_at = base_date + timedelta(days=payload.extend_days)
            changes["extended_days"] = payload.extend_days
            changes["new_subscription_ends_at"] = str(tenant.subscription_ends_at)

        tenant.updated_at = now

        audit = AuthAuditLog(
            user_id=admin_user.id,
            tenant_id=tenant.id,
            event="TENANT_STATUS_UPDATED",
            metadata_=changes,
        )
        db.add(audit)
        await db.commit()
        await db.refresh(tenant)

        owner_user = None
        for m in tenant.memberships:
            if m.is_owner and m.user:
                owner_user = TenantOwnerSummary(
                    id=m.user.id,
                    full_name=m.user.full_name,
                    phone=m.user.phone,
                    email=m.user.email,
                )
                break

        return TenantListItemResponse(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            city=tenant.city,
            phone=tenant.phone,
            email=tenant.email,
            status=tenant.status,
            plan_tier=tenant.plan_tier,
            trial_ends_at=tenant.trial_ends_at,
            subscription_ends_at=tenant.subscription_ends_at,
            is_read_only=tenant.is_read_only,
            owner=owner_user,
            total_users=len(tenant.memberships),
            created_at=tenant.created_at,
        )
