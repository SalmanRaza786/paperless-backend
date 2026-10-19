"""Database connection and session lifecycle management."""
from typing import Generator, Any


def get_db() -> Generator[Any, None, None]:
    """
    Dependency generator for obtaining a database session.
    Yields session and guarantees teardown.
    Configure with your ORM/database driver sessionmaker when added.
    """
    db = None
    try:
        yield db
    finally:
        if db is not None and hasattr(db, "close"):
            db.close()
