from fastapi import APIRouter

router = APIRouter()


@router.get("/health", summary="Health check endpoint")
async def health_check():
    return {
        "status": "healthy",
        "service": "paperless-backend"
    }
