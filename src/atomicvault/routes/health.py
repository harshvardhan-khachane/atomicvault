from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health_check(request: Request) -> JSONResponse:
    errors: list[str] = []

    # ── Redis ping ────────────────────────────────────────────
    try:
        request.app.state.redis_client.ping()
    except Exception as exc:
        logger.warning("health: redis ping failed: %s", exc)
        errors.append("redis")

    # ── MinIO ping ────────────────────────────────────────────
    try:
        bucket = request.app.state.settings.minio_bucket
        request.app.state.minio_client.bucket_exists(bucket)
    except Exception as exc:
        logger.warning("health: minio ping failed: %s", exc)
        errors.append("minio")

    if errors:
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "detail": f"unhealthy: {', '.join(errors)}",
            },
        )

    return JSONResponse(status_code=200, content={"status": "ok"})
