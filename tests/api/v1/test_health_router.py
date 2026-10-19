"""API endpoint tests for the health check route."""
import unittest
import asyncio
from app.modules.health.router import health_check


class TestHealthEndpoint(unittest.TestCase):
    def test_health_endpoint_response(self):
        response = asyncio.run(health_check())
        self.assertEqual(response.status, "healthy")
        self.assertEqual(response.service, "paperless-backend")


if __name__ == "__main__":
    unittest.main()
