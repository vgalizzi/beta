from functools import lru_cache
from urllib.parse import quote

from pydantic import AnyUrl, PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", hide_input_in_errors=True, frozen=True
    )
    storage_url_internal: AnyUrl
    storage_url_public: AnyUrl
    storage_access_key: str
    storage_secret_key: SecretStr
    postgres_user: str
    postgres_password: SecretStr
    postgres_db: str
    postgres_host: str
    postgres_port: int
    broker_url: RedisDsn
    bucket_name: str

    @property
    def database_url(self) -> PostgresDsn:
        # Built on demand so the password never appears in the settings repr.
        # build() does not percent-encode: "@", ":" or "/" in a password would break the URL.
        return PostgresDsn.build(
            scheme="postgresql+asyncpg",
            username=quote(self.postgres_user, safe=""),
            password=quote(self.postgres_password.get_secret_value(), safe=""),
            host=self.postgres_host,
            port=self.postgres_port,
            path=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    # Fields are read from the environment, not passed here. mypy knows it through the
    # pydantic.mypy plugin; pyright has no plugin system and reports missing arguments.
    return Settings()  # pyright: ignore[reportCallIssue]
