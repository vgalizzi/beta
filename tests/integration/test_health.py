"""/health tests against real Postgres, Valkey and SeaweedFS containers.

The containers start once per module (a few seconds each) and are shared by every test.
Each test points the API at them through environment variables, then runs the real
application, lifespan included, with FastAPI's TestClient.
"""

import time
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from botocore.config import Config
from fastapi.testclient import TestClient
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import HttpWaitStrategy

from beta.api.app import app
from beta.core.settings import get_settings

pytestmark = pytest.mark.integration

# Same images as docker-compose.yml, so the tests exercise the versions that run.
POSTGRES_IMAGE = "postgres:18"
VALKEY_IMAGE = "valkey/valkey:9"
SEAWEEDFS_IMAGE = "chrislusf/seaweedfs:4.48"

STORAGE_PORT = 8333
ACCESS_KEY = "test-access-key"
SECRET_KEY = "test-storage-secret"
BUCKET_NAME = "beta-test"

# The Docker healthcheck gives up after 2 s: /health must answer before that.
HEALTHCHECK_BUDGET_SECONDS = 2.0


@pytest.fixture(scope="module")
def postgres() -> Iterator[PostgresContainer]:
    # driver=None: the container class would otherwise expect psycopg2, which is not installed.
    with PostgresContainer(POSTGRES_IMAGE, driver=None) as container:
        yield container


@pytest.fixture(scope="module")
def broker() -> Iterator[RedisContainer]:
    with RedisContainer(VALKEY_IMAGE) as container:
        yield container


@pytest.fixture(scope="module")
def storage() -> Iterator[DockerContainer]:
    container = (
        DockerContainer(SEAWEEDFS_IMAGE)
        .with_command("mini -dir=/data -master.telemetry=false")
        .with_envs(AWS_ACCESS_KEY_ID=ACCESS_KEY, AWS_SECRET_ACCESS_KEY=SECRET_KEY)
        .with_exposed_ports(STORAGE_PORT)
        .waiting_for(HttpWaitStrategy(STORAGE_PORT, "/healthz"))
    )
    with container:
        s3_client = boto3.client(
            "s3",
            endpoint_url=_storage_url(container),
            aws_access_key_id=ACCESS_KEY,
            aws_secret_access_key=SECRET_KEY,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        s3_client.create_bucket(Bucket=BUCKET_NAME)
        s3_client.close()
        yield container


def _storage_url(container: DockerContainer) -> str:
    host = container.get_container_host_ip()
    return f"http://{host}:{container.get_exposed_port(STORAGE_PORT)}"


@pytest.fixture
def services_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    postgres: PostgresContainer,
    broker: RedisContainer,
    storage: DockerContainer,
) -> None:
    """Point the settings at the containers, ignoring the repository's `.env`."""
    monkeypatch.chdir(tmp_path)
    broker_host = broker.get_container_host_ip()
    env = {
        "POSTGRES_USER": postgres.username,
        "POSTGRES_PASSWORD": postgres.password,
        "POSTGRES_DB": postgres.dbname,
        "POSTGRES_HOST": postgres.get_container_host_ip(),
        "POSTGRES_PORT": str(postgres.get_exposed_port(5432)),
        "BROKER_URL": f"redis://{broker_host}:{broker.get_exposed_port(6379)}/0",
        "STORAGE_URL_INTERNAL": _storage_url(storage),
        "STORAGE_URL_PUBLIC": _storage_url(storage),
        "STORAGE_ACCESS_KEY": ACCESS_KEY,
        "STORAGE_SECRET_KEY": SECRET_KEY,
        "BUCKET_NAME": BUCKET_NAME,
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def client(services_env: None) -> Iterator[TestClient]:
    """Run the application, lifespan included, with settings read from the environment.

    The settings are cached, hence the cache cleared on both sides. Tests that change
    the environment build their own TestClient after the change instead.
    """
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_every_dependency_up_returns_200(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"database": "ok", "broker": "ok", "storage": "ok", "status": "ok"}


def test_missing_bucket_reports_storage_down(
    services_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # head_bucket also proves the bucket exists: the storage answering is not enough.
    monkeypatch.setenv("BUCKET_NAME", "no-such-bucket")
    get_settings.cache_clear()

    with TestClient(app) as client:
        response = client.get("/health")
    get_settings.cache_clear()

    assert response.status_code == 503
    assert response.json() == {
        "database": "ok",
        "broker": "ok",
        "storage": "down",
        "status": "down",
    }


def test_unreachable_broker_reports_broker_down(
    services_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Port 1 is closed: the connection is refused at once (the "stop" case).
    monkeypatch.setenv("BROKER_URL", "redis://127.0.0.1:1/0")
    get_settings.cache_clear()

    with TestClient(app) as client:
        response = client.get("/health")
    get_settings.cache_clear()

    assert response.status_code == 503
    assert response.json()["broker"] == "down"
    assert response.json()["database"] == "ok"


@pytest.mark.parametrize("frozen", ["database", "broker", "storage"])
def test_frozen_dependency_is_reported_within_budget(
    client: TestClient, request: pytest.FixtureRequest, frozen: str
) -> None:
    # The "pause" case: the connection opens, then nothing answers. Only a timeout
    # catches it. The first call fills the database pool, so the frozen call goes
    # through an already open connection, the case that needs command_timeout.
    assert client.get("/health").status_code == 200
    container_fixture = {"database": "postgres", "broker": "broker", "storage": "storage"}
    container = request.getfixturevalue(container_fixture[frozen]).get_wrapped_container()

    container.pause()
    try:
        start = time.perf_counter()
        response = client.get("/health")
        elapsed = time.perf_counter() - start
    finally:
        container.unpause()

    assert response.status_code == 503
    assert response.json()[frozen] == "down"
    assert elapsed < HEALTHCHECK_BUDGET_SECONDS
