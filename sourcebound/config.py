"""Runtime configuration, read once from environment variables.

Nothing here is ever sent to the browser except through ``Settings.public_view``,
which deliberately omits secrets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(ValueError):
    """Raised when an environment variable holds an invalid value."""


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}")


@dataclass(frozen=True)
class LLMSettings:
    provider: str = "none"  # "none" or "openai_compatible"
    base_url: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)
    timeout_seconds: int = 45
    max_calls_per_run: int = 4

    @property
    def enabled(self) -> bool:
        return self.provider == "openai_compatible" and bool(self.base_url) and bool(self.model)


@dataclass(frozen=True)
class Settings:
    db_path: Path = Path("data/sourcebound.db")
    host: str = "127.0.0.1"
    port: int = 8000
    max_upload_mb: int = 10
    max_sources_per_workspace: int = 40
    max_source_chars: int = 400_000
    max_run_seconds: int = 90
    max_tool_calls: int = 60
    max_sub_questions: int = 5
    max_retries_per_sub_question: int = 2
    max_concurrent_runs: int = 4
    runs_per_minute: int = 6
    retention_hours: int = 0  # 0 = keep until the user deletes
    allow_url_fetch: bool = True
    llm: LLMSettings = field(default_factory=LLMSettings)

    def public_view(self) -> dict[str, object]:
        """Configuration that is safe to show to any visitor."""
        return {
            "llm_available": self.llm.enabled,
            "llm_model": self.llm.model if self.llm.enabled else None,
            "allow_url_fetch": self.allow_url_fetch,
            "max_upload_mb": self.max_upload_mb,
            "max_sources": self.max_sources_per_workspace,
            "retention_hours": self.retention_hours,
        }


def load_settings() -> Settings:
    provider = os.environ.get("SOURCEBOUND_LLM_PROVIDER", "none").strip().lower() or "none"
    if provider not in {"none", "openai_compatible"}:
        raise ConfigError("SOURCEBOUND_LLM_PROVIDER must be 'none' or 'openai_compatible'")
    base_url = os.environ.get("SOURCEBOUND_LLM_BASE_URL", "").strip().rstrip("/")
    if base_url and not base_url.startswith(("http://", "https://")):
        raise ConfigError("SOURCEBOUND_LLM_BASE_URL must start with http:// or https://")
    llm = LLMSettings(
        provider=provider,
        base_url=base_url,
        model=os.environ.get("SOURCEBOUND_LLM_MODEL", "").strip(),
        api_key=os.environ.get("SOURCEBOUND_LLM_API_KEY", "").strip(),
        timeout_seconds=_int("SOURCEBOUND_LLM_TIMEOUT", 45, 5, 300),
        max_calls_per_run=_int("SOURCEBOUND_LLM_MAX_CALLS", 4, 1, 12),
    )
    if provider == "openai_compatible" and not (llm.base_url and llm.model):
        raise ConfigError(
            "SOURCEBOUND_LLM_PROVIDER=openai_compatible needs SOURCEBOUND_LLM_BASE_URL and "
            "SOURCEBOUND_LLM_MODEL. Leave the provider as 'none' to use the free extractive mode."
        )
    return Settings(
        db_path=Path(os.environ.get("SOURCEBOUND_DB_PATH", "data/sourcebound.db")),
        host=os.environ.get("SOURCEBOUND_HOST", "127.0.0.1"),
        port=_int("SOURCEBOUND_PORT", int(os.environ.get("PORT", "8000") or 8000), 1, 65535),
        max_upload_mb=_int("SOURCEBOUND_MAX_UPLOAD_MB", 10, 1, 50),
        max_sources_per_workspace=_int("SOURCEBOUND_MAX_SOURCES", 40, 1, 500),
        max_run_seconds=_int("SOURCEBOUND_MAX_RUN_SECONDS", 90, 10, 600),
        max_concurrent_runs=_int("SOURCEBOUND_MAX_CONCURRENT_RUNS", 4, 1, 32),
        runs_per_minute=_int("SOURCEBOUND_RUNS_PER_MINUTE", 6, 1, 120),
        retention_hours=_int("SOURCEBOUND_RETENTION_HOURS", 0, 0, 24 * 365),
        allow_url_fetch=_bool("SOURCEBOUND_ALLOW_URL_FETCH", True),
        llm=llm,
    )
