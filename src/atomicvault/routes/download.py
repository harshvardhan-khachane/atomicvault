from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from atomicvault.models import DownloadReason

logger = logging.getLogger(__name__)

router = APIRouter()

_REASON_TO_RESPONSE: dict[DownloadReason, tuple[int, str]] = {
    DownloadReason.NOT_FOUND: (404, "not found or expired"),
    DownloadReason.ALREADY_TAKEN: (410, "already downloaded"),
}


@router.get("/secrets/{token}")
async def download_secret(request: Request, token: str):
    vault = request.app.state.vault

    result, stream = vault.try_download(token)

    if not result.got_it:
        status, detail = _REASON_TO_RESPONSE.get(
            result.reason, (500, "unexpected error")
        )
        return JSONResponse(status_code=status, content={"detail": detail})


    return StreamingResponse(
        content=stream,
        media_type="application/octet-stream",
    )
