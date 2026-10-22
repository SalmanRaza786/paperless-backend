from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class PlanBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=100, description="Display name of the plan")
    slug: str = Field(..., min_length=2, max_length=50, description="Unique URL and system slug (e.g. standard)")
    badge_label: Optional[str] = Field(None, max_length=50, description="Badge text (e.g. Most Popular)")
    description: Optional[str] = Field(None, description="Short summary of this plan")
    price_monthly: int = Field(default=0, ge=0, description="Monthly recurring price in PKR")
    price_yearly: int = Field(default=0, ge=0, description="Yearly recurring price per month in PKR")
    trial_days: int = Field(default=14, ge=0, description="Free evaluation days granted on signup")
    features: List[str] = Field(default_factory=list, description="Array of feature bullet strings")
    limits: Dict[str, Any] = Field(default_factory=dict, description="System limits (branches, users, etc.)")
    is_active: bool = Field(default=True, description="Whether plan is visible and selectable")
    is_highlighted: bool = Field(default=False, description="Whether this plan has visual spotlight badge")
    sort_order: int = Field(default=0, description="Sorting sequence on pricing cards (lower first)")


class PlanCreate(PlanBase):
    pass


class PlanUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    slug: Optional[str] = Field(None, min_length=2, max_length=50)
    badge_label: Optional[str] = None
    description: Optional[str] = None
    price_monthly: Optional[int] = Field(None, ge=0)
    price_yearly: Optional[int] = Field(None, ge=0)
    trial_days: Optional[int] = Field(None, ge=0)
    features: Optional[List[str]] = None
    limits: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None
    is_highlighted: Optional[bool] = None
    sort_order: Optional[int] = None


class PlanResponse(PlanBase):
    id: int
    subscriber_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PublicPlanResponse(BaseModel):
    id: int
    name: str
    slug: str
    badge_label: Optional[str] = None
    description: Optional[str] = None
    price_monthly: int
    price_yearly: int
    trial_days: int
    features: List[str]
    is_highlighted: bool
    sort_order: int

    class Config:
        from_attributes = True
