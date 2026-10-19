"""Domain feature modules package.

Each domain feature module follows a clean modular architecture:
- router.py: FastAPI endpoint definitions
- schemas.py: Pydantic request/response validation schemas
- services.py: Business logic implementation
- models.py: Domain/database entities (when applicable)
- repositories.py: Data access layer (when applicable)
"""
