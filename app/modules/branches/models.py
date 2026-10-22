from datetime import datetime
from typing import Optional
import enum
from sqlalchemy import (
    BigInteger,
    String,
    Boolean,
    DateTime,
    Text,
    ForeignKey,
    Enum as SQLEnum,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base_class import Base


class BranchType(str, enum.Enum):
    RETAIL_SHOP = "retail_shop"
    WAREHOUSE_GODOWN = "warehouse_godown"
    WHOLESALE_DEPOT = "wholesale_depot"


class Branch(Base):
    __tablename__ = "branches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    type: Mapped[BranchType] = mapped_column(
        SQLEnum(BranchType, name="branch_type"), default=BranchType.RETAIL_SHOP, nullable=False
    )
    phone: Mapped[str] = mapped_column(String(25), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    city: Mapped[str] = mapped_column(String(50), nullable=False)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_main: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fbr_pos_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    tenant = relationship("Tenant", backref="branches")

    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_tenant_branch_code"),
        UniqueConstraint("tenant_id", "name", name="uq_tenant_branch_name"),
    )
