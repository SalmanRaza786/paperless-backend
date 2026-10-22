from datetime import datetime
from typing import List, Dict, Any, Optional
from fastapi import HTTPException, status
from sqlalchemy import select, func, or_, cast, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.plans.models import SubscriptionPlan
from app.modules.plans.schemas import PlanCreate, PlanUpdate, PlanResponse, PublicPlanResponse
from app.modules.auth.models import Tenant


DEFAULT_PLANS = [
    {
        "name": "Free Trial",
        "slug": "free_trial",
        "badge_label": "14-Day Evaluation",
        "description": "Full-feature evaluation for any Pakistani business.",
        "price_monthly": 0,
        "price_yearly": 0,
        "trial_days": 14,
        "is_highlighted": False,
        "sort_order": 1,
        "features": [
            "1 Shop / Branch location",
            "2 Staff user accounts",
            "Complete Thermal POS & barcode scanning",
            "Real-time Inventory tracking",
            "Customer & Supplier Khata ledger",
            "No credit card required",
        ],
        "limits": {"max_branches": 1, "max_staff": 2, "fbr_integration": False, "whatsapp_invoicing": False},
    },
    {
        "name": "Basic Retail",
        "slug": "basic",
        "badge_label": "Small Retailers",
        "description": "For single retail shops, marts, and pharmacies.",
        "price_monthly": 3500,
        "price_yearly": 2800,
        "trial_days": 14,
        "is_highlighted": False,
        "sort_order": 2,
        "features": [
            "1 Shop / Branch location",
            "3 Staff user accounts (Cashier, Admin)",
            "Unlimited sales invoices & products",
            "WhatsApp digital receipt delivery",
            "Standard expense & cash register tracking",
            "Standard customer khata balance",
            "Email & WhatsApp support",
        ],
        "limits": {"max_branches": 1, "max_staff": 3, "fbr_integration": False, "whatsapp_invoicing": True},
    },
    {
        "name": "Standard Wholesale",
        "slug": "standard",
        "badge_label": "Most Popular",
        "description": "For wholesale dealers, stockists, and distributors.",
        "price_monthly": 7500,
        "price_yearly": 6000,
        "trial_days": 14,
        "is_highlighted": True,
        "sort_order": 3,
        "features": [
            "2 Warehouses / Godowns included",
            "10 Staff user accounts with RBAC roles",
            "Multi-tier wholesale & retail pricing lists",
            "Salesman order booking & route dispatch",
            "Complete khata with 30/60/90 days aging analysis",
            "Batch, serial number & expiry date tracking",
            "Inter-warehouse stock transfer slips",
            "Priority phone & WhatsApp support",
        ],
        "limits": {"max_branches": 2, "max_staff": 10, "fbr_integration": False, "whatsapp_invoicing": True},
    },
    {
        "name": "Enterprise",
        "slug": "enterprise",
        "badge_label": "Large Chains",
        "description": "For retail chains, supermarkets, and large stockists.",
        "price_monthly": 15000,
        "price_yearly": 12000,
        "trial_days": 14,
        "is_highlighted": False,
        "sort_order": 4,
        "features": [
            "Unlimited branch locations & warehouses",
            "Unlimited staff accounts & custom roles",
            "FBR POS Digital Tax Invoicing integration",
            "Custom API integrations & ERP data sync",
            "Dedicated account manager & 24/7 SLA",
            "Automated Daily Cloud Backups & audit logs",
        ],
        "limits": {"max_branches": 999, "max_staff": 999, "fbr_integration": True, "whatsapp_invoicing": True},
    },
]


async def seed_default_plans_if_needed(db: AsyncSession) -> None:
    """Ensure baseline plans are seeded into subscription_plans table."""
    existing_res = await db.execute(select(SubscriptionPlan.slug))
    existing_slugs = set(existing_res.scalars().all())

    added = False
    for p in DEFAULT_PLANS:
        if p["slug"] not in existing_slugs:
            plan = SubscriptionPlan(
                name=p["name"],
                slug=p["slug"],
                badge_label=p.get("badge_label"),
                description=p.get("description"),
                price_monthly=p["price_monthly"],
                price_yearly=p["price_yearly"],
                trial_days=p.get("trial_days", 14),
                features=p["features"],
                limits=p.get("limits", {}),
                is_active=True,
                is_highlighted=p.get("is_highlighted", False),
                sort_order=p.get("sort_order", 0),
            )
            db.add(plan)
            added = True
    if added:
        await db.commit()


async def get_tenant_subscriber_counts(db: AsyncSession) -> Dict[str, int]:
    """Retrieve subscriber count mapped by plan slug."""
    tenant_counts_stmt = select(Tenant.plan_tier, func.count(Tenant.id)).group_by(Tenant.plan_tier)
    t_res = await db.execute(tenant_counts_stmt)
    return {row[0].value if hasattr(row[0], "value") else str(row[0]): row[1] for row in t_res.all()}


async def get_public_active_plans(db: AsyncSession) -> List[PublicPlanResponse]:
    """Fetch active plans sorted for public pricing and registration pages."""
    await seed_default_plans_if_needed(db)
    stmt = (
        select(SubscriptionPlan)
        .where(SubscriptionPlan.is_active == True)
        .order_by(SubscriptionPlan.sort_order.asc(), SubscriptionPlan.id.asc())
    )
    res = await db.execute(stmt)
    plans = res.scalars().all()
    return [PublicPlanResponse.from_orm(p) for p in plans]


async def list_admin_plans(db: AsyncSession) -> List[PlanResponse]:
    """List all plans with real-time active subscriber tenant counts."""
    await seed_default_plans_if_needed(db)
    stmt = select(SubscriptionPlan).order_by(SubscriptionPlan.sort_order.asc(), SubscriptionPlan.id.asc())
    res = await db.execute(stmt)
    plans = res.scalars().all()

    counts_map = await get_tenant_subscriber_counts(db)

    output = []
    for p in plans:
        sub_count = counts_map.get(p.slug, 0)
        resp = PlanResponse.from_orm(p)
        resp.subscriber_count = sub_count
        output.append(resp)
    return output


async def get_plan_by_id(db: AsyncSession, plan_id: int) -> SubscriptionPlan:
    """Get single plan by ID or 404."""
    stmt = select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id)
    res = await db.execute(stmt)
    plan = res.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return plan


async def create_plan(db: AsyncSession, payload: PlanCreate) -> PlanResponse:
    """Create a new subscription plan with slug uniqueness check."""
    clean_slug = payload.slug.strip().lower().replace(" ", "_")
    existing_stmt = select(SubscriptionPlan).where(SubscriptionPlan.slug == clean_slug)
    existing_res = await db.execute(existing_stmt)
    if existing_res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Plan slug '{clean_slug}' is already in use. Please choose a different code.",
        )

    new_plan = SubscriptionPlan(
        name=payload.name.strip(),
        slug=clean_slug,
        badge_label=payload.badge_label.strip() if payload.badge_label else None,
        description=payload.description.strip() if payload.description else None,
        price_monthly=payload.price_monthly,
        price_yearly=payload.price_yearly,
        trial_days=payload.trial_days,
        features=payload.features,
        limits=payload.limits or {},
        is_active=payload.is_active,
        is_highlighted=payload.is_highlighted,
        sort_order=payload.sort_order,
    )
    db.add(new_plan)
    await db.commit()
    await db.refresh(new_plan)

    resp = PlanResponse.from_orm(new_plan)
    resp.subscriber_count = 0
    return resp


async def update_plan(db: AsyncSession, plan_id: int, payload: PlanUpdate) -> PlanResponse:
    """Update existing subscription plan."""
    plan = await get_plan_by_id(db, plan_id)

    if payload.slug is not None:
        clean_slug = payload.slug.strip().lower().replace(" ", "_")
        if clean_slug != plan.slug:
            existing = await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.slug == clean_slug))
            if existing.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Plan slug '{clean_slug}' is already taken.",
                )
            plan.slug = clean_slug

    if payload.name is not None:
        plan.name = payload.name.strip()
    if payload.badge_label is not None:
        plan.badge_label = payload.badge_label.strip() if payload.badge_label else None
    if payload.description is not None:
        plan.description = payload.description.strip() if payload.description else None
    if payload.price_monthly is not None:
        plan.price_monthly = payload.price_monthly
    if payload.price_yearly is not None:
        plan.price_yearly = payload.price_yearly
    if payload.trial_days is not None:
        plan.trial_days = payload.trial_days
    if payload.features is not None:
        plan.features = payload.features
    if payload.limits is not None:
        plan.limits = payload.limits
    if payload.is_active is not None:
        plan.is_active = payload.is_active
    if payload.is_highlighted is not None:
        plan.is_highlighted = payload.is_highlighted
    if payload.sort_order is not None:
        plan.sort_order = payload.sort_order

    plan.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(plan)

    counts_map = await get_tenant_subscriber_counts(db)
    resp = PlanResponse.from_orm(plan)
    resp.subscriber_count = counts_map.get(plan.slug, 0)
    return resp


async def delete_plan(db: AsyncSession, plan_id: int) -> Dict[str, Any]:
    """Safe delete: if plan has active businesses, archive it; else delete permanently."""
    plan = await get_plan_by_id(db, plan_id)

    counts_map = await get_tenant_subscriber_counts(db)
    sub_count = counts_map.get(plan.slug, 0)

    if sub_count > 0:
        plan.is_active = False
        plan.updated_at = datetime.utcnow()
        await db.commit()
        return {
            "success": True,
            "action": "archived",
            "message": f"Plan '{plan.name}' has {sub_count} active business tenant(s). It has been safely deactivated and hidden from public pricing.",
            "is_active": False,
        }
    else:
        await db.delete(plan)
        await db.commit()
        return {
            "success": True,
            "action": "deleted",
            "message": f"Plan '{plan.name}' was permanently deleted.",
        }

