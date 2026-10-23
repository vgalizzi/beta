import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config
from fastapi import FastAPI, Request, Response
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from beta.api.schemas import DependencyState, HealthStatus
from beta.core.settings import get_settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
# Named after the module ("beta.api.app"), so log lines show where they come from.
logger = logging.getLogger(__name__)

# Upper bound for each dependency check, enforced here rather than trusted to the clients:
# a client timeout only covers what the client chose to cover. botocore's connect_timeout,
# for instance, does not include name resolution, which took 8 s for a stopped container.
# Kept under the 2 s the Docker healthcheck waits for, so it sees the 503 and not a timeout.
CHECK_TIMEOUT_SECONDS = 1.5


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    app.state.broker_client = Redis.from_url(
        str(settings.broker_url), socket_connect_timeout=1, socket_timeout=1
    )
    app.state.db_engine = create_async_engine(
        str(settings.database_url),
        pool_pre_ping=True,
        connect_args={"timeout": 1, "command_timeout": 1},
    )

    app.state.storage_client = boto3.client(
        "s3",
        endpoint_url=str(settings.storage_url_internal),
        aws_access_key_id=settings.storage_access_key,
        aws_secret_access_key=settings.storage_secret_key.get_secret_value(),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"total_max_attempts": 1},
            connect_timeout=1,
            read_timeout=1,
        ),
    )
    app.state.bucket_name = settings.bucket_name

    logger.info(msg="Settings loaded")
    yield
    await app.state.broker_client.aclose()
    await app.state.db_engine.dispose()
    app.state.storage_client.close()
    logger.info(msg="Clean app exit")


app = FastAPI(title="Beta", lifespan=lifespan)


async def broker_health_check(broker_client: Redis) -> DependencyState:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            await broker_client.ping()
        return "ok"
    except Exception:
        # The cause stays in the server logs, never in the response body (ADR 0004).
        # exc_info=True appends the traceback to the log line.
        logger.warning("Broker health check failed", exc_info=True)
        return "down"


async def database_health_check(db_engine: AsyncEngine) -> DependencyState:
    try:
        async with (
            asyncio.timeout(CHECK_TIMEOUT_SECONDS),
            db_engine.connect() as conn,
        ):
            await conn.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        logger.warning("Database health check failed", exc_info=True)
        return "down"


async def storage_health_check(storage_client: "S3Client", bucket_name: str) -> DependencyState:
    try:
        # On timeout, the await is abandoned but the thread runs until boto3 gives up:
        # a thread cannot be interrupted from outside. One probe every 10 s leaves at
        # most one such thread in the default pool.
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            await asyncio.to_thread(storage_client.head_bucket, Bucket=bucket_name)
        return "ok"
    except Exception:
        logger.warning("Storage health check failed", exc_info=True)
        return "down"


@app.get("/health")
async def health_check(request: Request, response: Response) -> HealthStatus:
    broker_status, storage_status, database_status = await asyncio.gather(
        broker_health_check(request.app.state.broker_client),
        storage_health_check(request.app.state.storage_client, request.app.state.bucket_name),
        database_health_check(request.app.state.db_engine),
    )
    health_status = HealthStatus(
        broker=broker_status, storage=storage_status, database=database_status
    )
    if health_status.status == "down":
        response.status_code = 503
    return health_status
