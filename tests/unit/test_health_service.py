"""Unit tests for the health feature service."""
import unittest
from app.modules.health.services import HealthService


class TestHealthService(unittest.TestCase):
    def test_health_service_returns_healthy(self):
        result = HealthService.get_health_status()
        self.assertEqual(result.status, "healthy")
        self.assertEqual(result.service, "paperless-backend")


if __name__ == "__main__":
    unittest.main()
