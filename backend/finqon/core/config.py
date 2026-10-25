"""Application settings.

Every setting comes from the environment (``FINQON_`` prefix) or the repository
``.env`` file. Nothing is hard-coded and nothing secret has a default.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = REPO_ROOT / ".env"


class ConfigurationError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FINQON_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Databases (Neon direct/session connections, one role each).
    api_database_url: SecretStr = SecretStr("")
    reader_database_url: SecretStr = SecretStr("")
    loader_database_url: SecretStr = SecretStr("")
    # Owner connection, used only by Alembic and role bootstrap. Never by the API.
    admin_database_url: SecretStr = SecretStr("")
    # Password for the two seed users created by dataset/load_ledger.py.
    seed_password: SecretStr = SecretStr("")

    # Auth.
    jwt_secret: SecretStr = SecretStr("")
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 14
    jwt_issuer: str = "finqon.ai"
    auth_rate_limit_attempts: int = 10
    auth_rate_limit_window_seconds: int = 60

    # LLM gateway (OpenAI-compatible endpoint).
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "gemini-flash-latest"
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 4
    # Optional separate judge for evaluation rubrics (use another model family). Empty means
    # the agent's own model judges.
    judge_base_url: str = ""
    judge_api_key: SecretStr = SecretStr("")
    judge_model: str = ""
    # Embeddings for semantic search (same gateway; must be 768-dimensional).
    embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768
    # Free tiers meter per input text (Gemini: 100/min). Raise on a paid plan; 0 disables pacing.
    embedding_requests_per_minute: int = 90

    # API.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
    timezone: str = "Asia/Kolkata"
    log_level: str = "INFO"

    # Agent caps (see docs/architecture.md).
    agent_max_model_steps: int = 6
    agent_max_sql_executions: int = 3
    agent_max_repairs: int = 2
    agent_deadline_seconds: float = 30.0
    agent_history_turns: int = 4

    # Query executor.
    sql_statement_timeout: str = "5s"
    sql_max_rows: int = 500
    reader_pool_max_size: int = 5

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def missing_runtime_settings(self) -> list[str]:
        """Names (never values) of settings the running API cannot start without."""
        required = {
            "FINQON_API_DATABASE_URL": self.api_database_url,
            "FINQON_READER_DATABASE_URL": self.reader_database_url,
            "FINQON_JWT_SECRET": self.jwt_secret,
            "FINQON_LLM_API_KEY": self.llm_api_key,
        }
        return [name for name, secret in required.items() if not secret.get_secret_value()]

    def require_runtime_settings(self, *, require_llm: bool = True) -> None:
        missing = self.missing_runtime_settings()
        if not require_llm:
            missing = [name for name in missing if name != "FINQON_LLM_API_KEY"]
        if missing:
            raise ConfigurationError(
                "Missing required settings: "
                + ", ".join(missing)
                + f". Provide them in {ENV_FILE} (see .env.example)."
            )
        if len(self.jwt_secret.get_secret_value()) < 32:
            raise ConfigurationError("FINQON_JWT_SECRET must be at least 32 characters.")


@lru_cache
def get_settings() -> Settings:
    return Settings()
