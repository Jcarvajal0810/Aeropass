"""Application settings, read from the environment (pydantic-settings)."""

from functools import cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    aeropass_adapters: Literal["real", "fake"] = "real"

    # Neon Postgres
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/aeropass"
    database_url_direct: str | None = None

    # Vercel Blob
    blob_read_write_token: str = ""
    blob_timeout_seconds: float = Field(default=3.0, gt=0)

    # Upstash Redis
    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = ""

    # Upstash QStash
    qstash_token: str = ""
    qstash_current_signing_key: str = ""
    qstash_next_signing_key: str = ""
    qstash_events_url_group: str = "aeropass-eventos"
    qstash_timeout_seconds: float = Field(default=2.0, gt=0)
    # Public URL of this deployment (e.g. https://aeropass.vercel.app); enables the URL claim
    # check of QStash signatures and is used by the schedule-setup tool.
    public_base_url: str = ""

    # Clerk
    clerk_secret_key: str = ""
    clerk_authorized_parties: str = ""

    # QR signing (Ed25519)
    qr_signing_private_key: str = ""
    qr_signing_kid: str = "dev"
    qr_ttl_seconds: int = 45

    # Biometric provider
    biometric_provider: Literal["mock", "vision"] = "mock"
    biometric_liveness_threshold: float = 0.80
    biometric_match_threshold: float = 0.80
    biometric_timeout_seconds: float = Field(default=4.0, gt=0)
    vision_provider_url: str = ""
    vision_provider_api_key: str = ""

    # Sentry (spec 002). An empty DSN turns observability off.
    sentry_dsn: str = ""
    sentry_environment: str = "dev"
    sentry_traces_sample_rate: float = Field(default=1.0, ge=0, le=1)
    # Injected by Vercel on every deployment; used as the Sentry release.
    vercel_git_commit_sha: str = ""

    @field_validator("qr_ttl_seconds")
    @classmethod
    def _ttl_in_range(cls, v: int) -> int:
        if not 30 <= v <= 60:
            raise ValueError("QR_TTL_SECONDS must be between 30 and 60")
        return v

    @field_validator("biometric_liveness_threshold", "biometric_match_threshold")
    @classmethod
    def _threshold_in_range(cls, v: float) -> float:
        if not 0 <= v <= 1:
            raise ValueError("biometric thresholds must be between 0 and 1")
        return v

    @property
    def migrations_url(self) -> str:
        return self.database_url_direct or self.database_url

    @property
    def authorized_parties(self) -> list[str]:
        return [p.strip() for p in self.clerk_authorized_parties.split(",") if p.strip()]


@cache
def get_settings() -> Settings:
    return Settings()
