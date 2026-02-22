from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from minio import Minio
from redis import Redis

from atomicvault.cleanup import janitor_loop
from atomicvault.config import Settings
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
    settings = Settings()

    redis_client = Redis.from_url(settings.redis_url, decode_responses=False)
    minio_client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )

    _ensure_bucket(minio_client, settings.minio_bucket)

    redis_store = RedisStore(redis_client)
    minio_store = MinioStore(minio_client, settings.minio_bucket)
    vault = VaultService(
        redis_store,
        minio_store,
        max_file_size=settings.max_file_size_bytes,
    )

    app.state.settings = settings
    app.state.vault = vault
    app.state.redis_client = redis_client
    app.state.minio_client = minio_client

    janitor_task = None
    if settings.janitor_enabled:
        janitor_task = asyncio.create_task(
            janitor_loop(
                redis_store=redis_store,
                minio_store=minio_store,
                older_than_seconds=settings.janitor_older_than_seconds,
                interval_seconds=settings.janitor_interval_seconds,
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
