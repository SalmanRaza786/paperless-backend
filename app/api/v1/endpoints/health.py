"""Backward compatibility re-export for the health endpoint."""
from app.modules.health.router import router

__all__ = ["router"]
