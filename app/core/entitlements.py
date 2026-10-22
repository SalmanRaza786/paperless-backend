from typing import Dict, Any, Optional, Tuple
from fastapi import HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.plans.models import SubscriptionPlan
from app.modules.auth.models import Tenant, TenantUser


class EntitlementService:
    @staticmethod
    async def get_tenant_plan_and_limits(
        db: AsyncSession,
        tenant: Tenant,
    ) -> Tuple[Optional[SubscriptionPlan], Dict[str, Any]]:
        """Resolve current tenant subscription plan and its functional limits dict."""
        tier_val = tenant.plan_tier.value if hasattr(tenant.plan_tier, "value") else str(tenant.plan_tier)
        stmt = select(SubscriptionPlan).where(SubscriptionPlan.slug == tier_val)
        res = await db.execute(stmt)
        plan = res.scalar_one_or_none()

        if plan and isinstance(plan.limits, dict):
            limits = plan.limits
        else:
            # Fallback baseline limits for evaluation
            limits = {
                "max_branches": 1,
                "max_staff": 2,
                "whatsapp_invoicing": False,
                "fbr_integration": False,
                "multi_godown": False,
                "custom_roles": False,
            }
        return plan, limits

    @staticmethod
    async def check_quota_limit(
        db: AsyncSession,
        tenant: Tenant,
        resource_key: str,
        current_count: int,
        resource_name: str,
    ) -> None:
        """
        Enforce numeric resource count limits (e.g. max_branches, max_staff).
        Limits of -1 or >= 999 are treated as unlimited.
        All validation messages strictly in English.
        """
        plan, limits = await EntitlementService.get_tenant_plan_and_limits(db, tenant)
        plan_name = plan.name if plan else tenant.plan_tier.replace("_", " ").title()

        max_limit = limits.get(resource_key)
        if max_limit is None:
            return

        try:
            max_val = int(max_limit)
        except (ValueError, TypeError):
            return

        # -1 or >= 999 signifies unlimited quota
        if max_val < 0 or max_val >= 999:
            return

        if current_count >= max_val:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{resource_name} limit reached for your '{plan_name}' plan ({current_count}/{max_val}). Please upgrade your subscription to add more {resource_name.lower()}.",
            )

    @staticmethod
    async def check_feature_access(
        db: AsyncSession,
        tenant: Tenant,
        feature_key: str,
        feature_name: str,
    ) -> None:
        """
        Enforce feature flag permissions (e.g. whatsapp_invoicing, fbr_integration, custom_roles).
        All validation messages strictly in English.
        """
        plan, limits = await EntitlementService.get_tenant_plan_and_limits(db, tenant)
        plan_name = plan.name if plan else tenant.plan_tier.replace("_", " ").title()

        is_allowed = bool(limits.get(feature_key, False))
        if not is_allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"'{feature_name}' is not enabled on your current '{plan_name}' plan. Please upgrade your subscription to access this feature.",
            )

    @staticmethod
    async def get_tenant_quota_summary(
        db: AsyncSession,
        tenant: Tenant,
    ) -> Dict[str, Any]:
        """
        Get complete usage vs limits breakdown for tenant dashboard and upgrade checks.
        """
        plan, limits = await EntitlementService.get_tenant_plan_and_limits(db, tenant)
        plan_name = plan.name if plan else tenant.plan_tier.replace("_", " ").title()
        plan_slug = plan.slug if plan else tenant.plan_tier

        # Count staff users
        staff_count_stmt = select(func.count(TenantUser.id)).where(TenantUser.tenant_id == tenant.id)
        staff_count_res = await db.execute(staff_count_stmt)
        used_staff = staff_count_res.scalar() or 0

        # Count active branches
        try:
            from app.modules.branches.models import Branch
            branch_count_stmt = select(func.count(Branch.id)).where(
                Branch.tenant_id == tenant.id,
                Branch.is_active.is_(True),
            )
            branch_count_res = await db.execute(branch_count_stmt)
            used_branches = branch_count_res.scalar() or 0
        except Exception:
            used_branches = 1

        if used_branches == 0:
            used_branches = 1

        max_staff = limits.get("max_staff", 2)
        max_branches = limits.get("max_branches", 1)

        def is_unlimited(val: Any) -> bool:
            try:
                v = int(val)
                return v >= 999 or v < 0
            except Exception:
                return False

        def is_reached(used: int, max_val: Any) -> bool:
            try:
                v = int(max_val)
                if v >= 999 or v < 0:
                    return False
                return used >= v
            except Exception:
                return False

        return {
            "plan_name": plan_name,
            "plan_slug": plan_slug,
            "trial_days": plan.trial_days if plan else 14,
            "limits": limits,
            "usage": {
                "staff": {
                    "used": used_staff,
                    "max": max_staff,
                    "is_unlimited": is_unlimited(max_staff),
                    "is_reached": is_reached(used_staff, max_staff),
                },
                "branches": {
                    "used": used_branches,
                    "max": max_branches,
                    "is_unlimited": is_unlimited(max_branches),
                    "is_reached": is_reached(used_branches, max_branches),
                },
            },

            "features": {
                "whatsapp_invoicing": bool(limits.get("whatsapp_invoicing", False)),
                "fbr_integration": bool(limits.get("fbr_integration", False)),
                "multi_godown": bool(limits.get("multi_godown", False)),
                "custom_roles": bool(limits.get("custom_roles", False)),
            },
        }
