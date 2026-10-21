from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from beta.api.schemas import HealthStatus
from beta.core.settings import get_settings


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    get_settings()
    print("Settings loaded")
    yield
    print("Clean exit")


app = FastAPI(title="Beta", lifespan=lifespan)


@app.get("/health")
def health_check() -> HealthStatus:
    return HealthStatus(status="ok")
