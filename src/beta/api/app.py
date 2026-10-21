from fastapi import FastAPI

from beta.api.schemas import HealthStatus

app = FastAPI(title="Beta")


@app.get("/health")
def health_check() -> HealthStatus:
    return HealthStatus(status="ok")
