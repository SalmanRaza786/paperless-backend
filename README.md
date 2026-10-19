# Paperless Backend

FastAPI backend for the Paperless project.

## Requirements
- Python 3.12+
- `uv` (recommended) or `pip` / `venv`

## Getting Started

### Using `uv` (Recommended)
```bash
# Run server with auto-reload
uv run uvicorn app.main:app --reload --port 8000
```

### Using standard `venv` & `pip`
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## API Documentation
Once running, visit:
- Swagger UI: `http://localhost:8000/api/v1/docs`
- Health check: `http://localhost:8000/api/v1/health`
