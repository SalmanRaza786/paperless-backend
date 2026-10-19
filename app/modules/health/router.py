"""API routes for health status and monitoring."""
from fastapi import APIRouter
from app.modules.health.schemas import HealthResponse
from app.modules.health.services import HealthService

router = APIRouter()


@router.get("/health", response_model=HealthResponse, summary="Health check endpoint")
async def health_check() -> HealthResponse:
    return HealthService.get_health_status()
