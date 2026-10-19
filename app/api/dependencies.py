"""Shared API dependencies across route handlers."""
from typing import Generator, Any
from fastapi import Depends
from app.db.session import get_db


def get_database_session() -> Generator[Any, None, None]:
    """Dependency wrapper for database session."""
    yield from get_db()
