"""Business logic service for health check verification."""
from app.core.config import settings
from app.modules.health.schemas import HealthResponse


class HealthService:
    @staticmethod
    def get_health_status() -> HealthResponse:
        return HealthResponse(
            status="healthy",
            service="paperless-backend"
        )
