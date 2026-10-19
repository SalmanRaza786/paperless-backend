"""Database base models and interfaces."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class BaseEntitySchema(BaseModel):
    """Base schema for database entities with timestamp tracking."""
    id: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        from_attributes = True
