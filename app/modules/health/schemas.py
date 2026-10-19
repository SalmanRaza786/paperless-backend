"""Pydantic schemas for the health check feature."""
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(..., description="Service status", example="healthy")
    service: str = Field(..., description="Service name", example="paperless-backend")
