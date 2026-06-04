import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_database_url(url: str) -> str:
    """Force the psycopg3 driver on plain Postgres URLs.

    Managed hosts (Railway, Render, Supabase) hand out connection strings as
    ``postgres://`` or ``postgresql://``, but SQLAlchemy needs the explicit
    ``postgresql+psycopg://`` driver this app is built on. Rewriting here means
    the host's variable can be plugged in untouched.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def _parse_origins(value: str | None) -> tuple[str, ...]:
    """Split a comma-separated CORS allowlist; fall back to local dev origins."""
    if not value or not value.strip():
        return ("http://localhost:3000", "http://127.0.0.1:3000")
    return tuple(origin.strip() for origin in value.split(",") if origin.strip())


@dataclass(frozen=True)
class Settings:
    database_url: str
    openai_api_key: str
    slack_webhook_url: str
    use_mock_ai: bool
    allowed_origins: tuple[str, ...]


def get_settings() -> Settings:
    return Settings(
        database_url=_normalize_database_url(os.getenv("DATABASE_URL", "")),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL", ""),
        use_mock_ai=_as_bool(os.getenv("USE_MOCK_AI"), default=True),
        allowed_origins=_parse_origins(os.getenv("ALLOWED_ORIGINS")),
    )


settings = get_settings()
