from __future__ import annotations

import logging

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse

from atomicvault.vault import InfraError

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/secrets", status_code=201)
async def upload_secret(
    request: Request,
    file: UploadFile,
    ttl: int = 300,
) -> JSONResponse:
    vault = request.app.state.vault

    size_bytes = file.size
    if size_bytes is None:
        return JSONResponse(
            status_code=400,
            content={"detail": "file size required (content-length header missing)"},
        )

    try:
        receipt = vault.upload(
            file.file, file.filename, size_bytes, ttl=ttl
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"detail": str(exc)},
        )
    except InfraError:
        return JSONResponse(
            status_code=503,
            content={"detail": "service unavailable"},
        )

    return JSONResponse(
        status_code=201,
        content={
            "token": receipt.token,
            "expires_at": receipt.expires_at.isoformat(),
        },
    )
