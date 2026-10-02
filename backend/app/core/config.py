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
    # Phase 10: which real provider USE_MOCK_AI=false selects. "openai" keeps
    # the pre-Phase-10 behaviour; "qwen3-4b-lora-v1" is the Phase 8 adapter
    # behind an OpenAI-compatible inference server (app/ai/lora_client.py).
    ai_provider: str = "openai"
    lora_inference_base_url: str = ""
    lora_inference_api_key: str = ""
    lora_served_model: str = "qwen3-4b-lora-v1"
    lora_timeout_seconds: float = 120.0
    # Phase 12: operator sessions (docs/engineering-log/phase12-release-handoff.md).
    session_cookie_secure: bool = True
    session_idle_minutes: int = 60
    session_absolute_hours: int = 12
    password_hash_n: int = 2**15  # scrypt cost for NEW hashes; stored per hash


def get_settings() -> Settings:
    return Settings(
        database_url=_normalize_database_url(os.getenv("DATABASE_URL", "")),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL", ""),
        use_mock_ai=_as_bool(os.getenv("USE_MOCK_AI"), default=True),
        allowed_origins=_parse_origins(os.getenv("ALLOWED_ORIGINS")),
        ai_provider=(os.getenv("AI_PROVIDER") or "openai").strip().lower(),
        lora_inference_base_url=os.getenv("LORA_INFERENCE_BASE_URL", "").strip(),
        lora_inference_api_key=os.getenv("LORA_INFERENCE_API_KEY", ""),
        lora_served_model=(os.getenv("LORA_SERVED_MODEL") or "qwen3-4b-lora-v1").strip(),
        lora_timeout_seconds=float(os.getenv("LORA_TIMEOUT_SECONDS") or 120),
        session_cookie_secure=_as_bool(os.getenv("SESSION_COOKIE_SECURE"), default=True),
        session_idle_minutes=int(os.getenv("SESSION_IDLE_MINUTES") or 60),
        session_absolute_hours=int(os.getenv("SESSION_ABSOLUTE_HOURS") or 12),
        password_hash_n=int(os.getenv("PASSWORD_HASH_N") or 2**15),
    )


settings = get_settings()
