from typing import List, Dict, Any
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.admin.router import require_superadmin
from app.modules.plans.schemas import (
    PlanCreate,
    PlanUpdate,
    PlanResponse,
    PublicPlanResponse,
)
from app.modules.plans import services

# Public router
public_router = APIRouter(prefix="/plans", tags=["Subscription Plans (Public)"])

# Super Admin router
admin_router = APIRouter(prefix="/admin/plans", tags=["Subscription Plans Management (Super Admin)"])


@public_router.get("", response_model=List[PublicPlanResponse], summary="Get Active Plans for Website")
async def get_public_plans(db: AsyncSession = Depends(get_db)):
    """Fetch active subscription plans sorted for public pricing cards and signup flow."""
    return await services.get_public_active_plans(db)


@admin_router.get("", response_model=List[PlanResponse], summary="List All Plans with Subscriber Counts")
async def list_admin_plans(
    admin: User = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve all subscription plans (active + inactive) with real-time tenant subscriber counts."""
    return await services.list_admin_plans(db)


@admin_router.post("", response_model=PlanResponse, status_code=status.HTTP_201_CREATED, summary="Create New Plan")
async def create_plan(
    payload: PlanCreate,
    admin: User = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new subscription plan."""
    return await services.create_plan(db, payload)


@admin_router.get("/{plan_id}", response_model=PlanResponse, summary="Get Single Plan Detail")
async def get_plan(
    plan_id: int,
    admin: User = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Fetch single plan by ID."""
    plan = await services.get_plan_by_id(db, plan_id)
    return PlanResponse.from_orm(plan)


@admin_router.put("/{plan_id}", response_model=PlanResponse, summary="Update Plan")
async def update_plan(
    plan_id: int,
    payload: PlanUpdate,
    admin: User = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Update subscription plan attributes, pricing, features, or visibility."""
    return await services.update_plan(db, plan_id, payload)


@admin_router.delete("/{plan_id}", response_model=Dict[str, Any], summary="Delete or Archive Plan")
async def delete_plan(
    plan_id: int,
    admin: User = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Delete plan if no tenants attached, or archive (deactivate) if active subscribers exist."""
    return await services.delete_plan(db, plan_id)
