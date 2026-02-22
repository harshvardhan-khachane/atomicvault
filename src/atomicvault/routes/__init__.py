from fastapi import APIRouter

from atomicvault.routes.download import router as download_router
from atomicvault.routes.health import router as health_router
from atomicvault.routes.upload import router as upload_router

api_router = APIRouter()
api_router.include_router(upload_router)
api_router.include_router(download_router)
api_router.include_router(health_router)
