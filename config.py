"""Application configuration for the Oracle Fusion procurement agent."""

from __future__ import annotations

from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    """Validated application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    fusion_base_url: str = Field(..., alias="FUSION_BASE_URL")
    fusion_username: str = Field(..., alias="FUSION_USERNAME")
    fusion_password: str = Field(..., alias="FUSION_PASSWORD")
    fusion_rest_version: str = Field("11.13.18.05", alias="FUSION_REST_VERSION")
    fusion_bu_name: str | None = Field(None, alias="FUSION_BU_NAME")
    fusion_currency: str = Field("USD", alias="FUSION_CURRENCY")
    fusion_requester_email: str | None = Field(None, alias="FUSION_REQUESTER_EMAIL")
    dry_run: bool = Field(False, alias="DRY_RUN")
    openai_model: str = Field("gpt-4o", alias="OPENAI_MODEL")

    @property
    def fusion_api_base(self) -> str:
        """Return the Oracle Fusion REST base URL for the configured version."""

        return (
            f"{self.fusion_base_url.rstrip('/')}/"
            f"fscmRestApi/resources/{self.fusion_rest_version}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached and validated application settings."""

    try:
        return Settings()
    except ValidationError as exc:
        missing_fields = sorted(
            {
                ".".join(str(part) for part in error["loc"])
                for error in exc.errors()
                if error.get("type") == "missing"
            }
        )
        if missing_fields:
            joined = ", ".join(missing_fields)
            raise RuntimeError(
                f"Missing required environment variables: {joined}. "
                "Copy .env.example to .env and provide values."
            ) from exc
        raise RuntimeError(f"Invalid configuration: {exc}") from exc
