from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


async def janitor_loop(
    *,
    redis_store,
    minio_store,
    older_than_seconds: int,
    interval_seconds: float,
    once: bool = False,
) -> dict[str, int]:
    """Scan MinIO for old files and delete orphans (no Redis reverse key)."""
    try:
        while True:
            stats = _run_scan(redis_store, minio_store, older_than_seconds)

            if once:
                return stats

            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("janitor cancelled, exiting cleanly")
        raise


def _run_scan(redis_store, minio_store, older_than_seconds: int) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(seconds=older_than_seconds)

    candidates = minio_store.list_old_files(threshold)
    scanned = len(candidates)
    deleted = 0

    logger.info("janitor scan start: %d candidates", scanned)

    for file_id in candidates:
        if redis_store.exists_by_file_id(file_id):
            continue

        try:
            minio_store.delete(file_id)
            deleted += 1
        except Exception:
            logger.warning(
                "janitor delete failed file_id=%s", file_id, exc_info=True
            )

    logger.info("janitor scan done: scanned=%d deleted=%d", scanned, deleted)
    return {"scanned": scanned, "deleted": deleted}
