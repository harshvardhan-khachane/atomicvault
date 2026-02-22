from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from minio import Minio
from redis import Redis

from atomicvault.cleanup import janitor_loop
from atomicvault import settings
from atomicvault.minio_store import MinioStore
from atomicvault.redis_store import RedisStore
from atomicvault.routes import api_router
from atomicvault.vault import VaultService

logger = logging.getLogger(__name__)


def _ensure_bucket(client: Minio, bucket: str) -> None:
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        logger.info("created missing bucket: %s", bucket)


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=False)
    minio_client = Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_SECURE,
    )

    _ensure_bucket(minio_client, settings.MINIO_BUCKET)

    redis_store = RedisStore(redis_client)
    minio_store = MinioStore(minio_client, settings.MINIO_BUCKET)
    vault = VaultService(
        redis_store,
        minio_store,
        max_file_size=settings.MAX_FILE_SIZE_BYTES,
    )

    app.state.settings = settings
    app.state.vault = vault
    app.state.redis_client = redis_client
    app.state.minio_client = minio_client

    janitor_task = None
    if settings.JANITOR_ENABLED:
        janitor_task = asyncio.create_task(
            janitor_loop(
                redis_store=redis_store,
                minio_store=minio_store,
                older_than_seconds=settings.JANITOR_OLDER_THAN_SECONDS,
                interval_seconds=settings.JANITOR_INTERVAL_SECONDS,
                once=False,
            )
        )
    app.state.janitor_task = janitor_task

    logger.info("AtomicVault started")

    yield

    #Shutdown
    if janitor_task is not None:
        janitor_task.cancel()
        try:
            await janitor_task
        except asyncio.CancelledError:
            pass

    try:
        redis_client.close()
    except Exception:
        logger.warning("redis close failed", exc_info=True)

    logger.info("AtomicVault stopped")


app = FastAPI(title="AtomicVault", lifespan=lifespan)
app.include_router(api_router)
