"""Settings tests: validation, secret masking, immutability, caching.

No service is needed: everything goes through environment variables, set and removed
with `monkeypatch`, which restores the initial state after each test.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from beta.core.settings import get_settings

# A complete, valid environment. Each test starts from it and changes a single thing,
# so that a failure points to a single cause.
VALID_ENV = {
    "STORAGE_URL_INTERNAL": "http://storage:8333",
    "STORAGE_URL_PUBLIC": "http://localhost:8333",
    "STORAGE_ACCESS_KEY": "test-access-key",
    "STORAGE_SECRET_KEY": "test-storage-secret",
    "POSTGRES_USER": "beta",
    "POSTGRES_PASSWORD": "test-db-password",
    "POSTGRES_DB": "beta",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5433",
    "BROKER_URL": "redis://localhost:6379/0",
    "BUCKET_NAME": "beta-test",
}


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Isolate each test from the machine running it.

    - The working directory becomes an empty temporary directory: `env_file=".env"` is
      relative to the working directory, so the repository's `.env` is no longer found.
    - The `get_settings` cache is cleared before and after: otherwise a test would receive
      the object built by the previous one, and results would depend on test order.
    """
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def valid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the valid environment. `setenv` also overrides anything the shell exported."""
    for name, value in VALID_ENV.items():
        monkeypatch.setenv(name, value)


@pytest.mark.usefixtures("valid_env")
def test_valid_environment_is_converted_to_typed_fields() -> None:
    settings = get_settings()

    # Environment variables are always strings: the class must convert them.
    assert settings.postgres_port == 5433
    assert isinstance(settings.storage_secret_key, SecretStr)
    assert settings.storage_secret_key.get_secret_value() == "test-storage-secret"


@pytest.mark.usefixtures("valid_env")
@pytest.mark.parametrize("missing", ["BUCKET_NAME", "POSTGRES_PASSWORD", "BROKER_URL"])
def test_missing_required_variable_is_rejected(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError) as exc_info:
        get_settings()

    # A single variable is missing: the error must point to that field, and that field only.
    # Variable-to-field matching is case-insensitive; "loc" holds the field name.
    errors = exc_info.value.errors()
    assert [error["loc"] for error in errors] == [(missing.lower(),)]
    assert errors[0]["type"] == "missing"


@pytest.mark.usefixtures("valid_env")
@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("BROKER_URL", "http://localhost:6379/0"),  # RedisDsn rejects any other scheme
        ("STORAGE_URL_INTERNAL", "not a url"),  # AnyUrl rejects anything that is not a URL
        ("POSTGRES_PORT", "abc"),  # int rejects a non-numeric string
    ],
)
def test_malformed_value_is_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError) as exc_info:
        get_settings()

    assert [error["loc"] for error in exc_info.value.errors()] == [(name.lower(),)]


@pytest.mark.usefixtures("valid_env")
def test_validation_error_does_not_leak_input_values(monkeypatch: pytest.MonkeyPatch) -> None:
    # By default Pydantic copies the faulty value into the message, and for a missing field
    # the whole dict of values read, secrets included. hide_input_in_errors prevents it.
    monkeypatch.setenv("POSTGRES_PORT", "port-secret-123")
    monkeypatch.delenv("BUCKET_NAME")

    with pytest.raises(ValidationError) as exc_info:
        get_settings()

    message = str(exc_info.value)
    assert "port-secret-123" not in message
    assert "test-db-password" not in message
    assert "test-storage-secret" not in message


@pytest.mark.usefixtures("valid_env")
def test_secrets_are_masked_in_repr() -> None:
    # str() and repr() are what ends up in a print, a log line or a traceback.
    rendered = f"{get_settings()!s} {get_settings()!r}"

    assert "test-db-password" not in rendered
    assert "test-storage-secret" not in rendered


@pytest.mark.usefixtures("valid_env")
def test_settings_are_immutable() -> None:
    # The cached instance is shared by the whole process: a mutation would leak everywhere.
    settings = get_settings()

    with pytest.raises(ValidationError) as exc_info:
        # mypy already rejects this write statically (frozen model, through the Pydantic
        # plugin); the ignore lets the test check the runtime guard as well.
        settings.bucket_name = "another-bucket"  # type: ignore[misc]

    assert exc_info.value.errors()[0]["type"] == "frozen_instance"
    assert get_settings().bucket_name == "beta-test"


@pytest.mark.usefixtures("valid_env")
def test_database_url_is_built_from_components() -> None:
    url = get_settings().database_url

    assert str(url) == "postgresql+asyncpg://beta:test-db-password@localhost:5433/beta"


@pytest.mark.usefixtures("valid_env")
def test_database_url_encodes_special_characters_in_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # "@", ":" and "/" have a meaning in a URL. Unencoded, they break its parsing and
    # PostgresDsn.build raises "invalid port number". This test guards the quote() call.
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss:w/rd")

    url = get_settings().database_url

    assert str(url) == "postgresql+asyncpg://beta:p%40ss%3Aw%2Frd@localhost:5433/beta"


@pytest.mark.usefixtures("valid_env")
def test_get_settings_returns_the_same_instance() -> None:
    # lru_cache: a single object per process, built on the first call.
    assert get_settings() is get_settings()
