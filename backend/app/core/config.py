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
    # With the fine-tuned provider selected: when its server is not reachable
    # (checked before each generation, cached briefly), draft with the mock
    # generator instead of failing. Such drafts are recorded as mock.
    lora_fallback_to_mock: bool = False
    # Tool selection is separate from drafting: the evaluated LoRA adapter
    # was not trained for tool calling. Real planning is an explicit opt-in.
    agent_provider: str = "mock"
    agent_model: str = "gpt-4o-mini"
    # Phase 12: operator sessions (docs/engineering-log/phase12-release-handoff.md).
    session_cookie_secure: bool = True
    session_idle_minutes: int = 60
    session_absolute_hours: int = 12
    password_hash_n: int = 2**15  # scrypt cost for NEW hashes; stored per hash
    # Self-service sign-up with an emailed verification code, and one-click
    # guest access. Both are off unless explicitly enabled.
    self_signup_enabled: bool = False
    self_signup_role: str = "operator"
    signup_allowed_email_domains: tuple[str, ...] = ()
    guest_access_enabled: bool = False
    guest_session_hours: int = 2
    # Verification email. With no SMTP host, a mock sender prints the code to
    # the API log (local development only; nothing is sent).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    email_from: str = ""

    @property
    def guest_safe(self) -> bool:
        """True when no action can send a real Slack message or call a paid,
        per-request AI API: drafts come from the mock generator or the
        self-hosted fine-tuned model, planning is mocked, and no Slack webhook
        is configured."""
        drafting_is_safe = self.use_mock_ai or self.ai_provider == "qwen3-4b-lora-v1"
        planning_is_safe = self.use_mock_ai or self.agent_provider == "mock"
        return drafting_is_safe and planning_is_safe and not self.slack_webhook_url


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
        lora_fallback_to_mock=_as_bool(os.getenv("LORA_FALLBACK_TO_MOCK"), default=False),
        agent_provider=(os.getenv("AGENT_PROVIDER") or "mock").strip().lower(),
        agent_model=(os.getenv("AGENT_MODEL") or "gpt-4o-mini").strip(),
        session_cookie_secure=_as_bool(os.getenv("SESSION_COOKIE_SECURE"), default=True),
        session_idle_minutes=int(os.getenv("SESSION_IDLE_MINUTES") or 60),
        session_absolute_hours=int(os.getenv("SESSION_ABSOLUTE_HOURS") or 12),
        password_hash_n=int(os.getenv("PASSWORD_HASH_N") or 2**15),
        self_signup_enabled=_as_bool(os.getenv("SELF_SIGNUP_ENABLED"), default=False),
        self_signup_role=(os.getenv("SELF_SIGNUP_ROLE") or "operator").strip().lower(),
        signup_allowed_email_domains=tuple(
            d.strip().lower().lstrip("@") for d in (os.getenv("SIGNUP_ALLOWED_EMAIL_DOMAINS") or "").split(",") if d.strip()
        ),
        guest_access_enabled=_as_bool(os.getenv("GUEST_ACCESS_ENABLED"), default=False),
        guest_session_hours=int(os.getenv("GUEST_SESSION_HOURS") or 2),
        smtp_host=os.getenv("SMTP_HOST", "").strip(),
        smtp_port=int(os.getenv("SMTP_PORT") or 587),
        smtp_username=os.getenv("SMTP_USERNAME", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        smtp_starttls=_as_bool(os.getenv("SMTP_STARTTLS"), default=True),
        email_from=os.getenv("EMAIL_FROM", "").strip(),
    )


settings = get_settings()
