"""Application settings — *operational* configuration only.

By design this file holds **no model decisions**. Which provider, which API key,
and which model are chosen entirely from the dashboard at runtime (see
`runtime.py`), never from the environment — the system always boots in
deterministic mock mode and stays there until an operator configures a provider.
What lives here is ops config that legitimately belongs to the deployment:
guardrail thresholds, fan-out concurrency, retry policy, observability, and the
optional admin token that guards the control plane.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_prefix="", extra="ignore"
    )

    # Optional admin token. If set, configuration writes (PUT /config,
    # /config/test, model fetch) require the `X-Admin-Token` header — a
    # lightweight stand-in for the RBAC a real control plane would enforce.
    # Unset → open (zero-config local/demo).
    ledger_admin_token: str = ""

    # Guardrail thresholds (seed the runtime defaults; tunable from the dashboard).
    ledger_confidence_threshold: float = 0.80
    ledger_match_threshold: float = 0.82

    # Fan-out concurrency ceiling (protects the model rate limit).
    ledger_max_concurrency: int = 8

    # Upload guards (F13): per-file size cap and per-run document count cap.
    ledger_max_upload_mb: int = 15
    ledger_max_files: int = 40

    # PDF lane: page ceiling — an over-cap statement quarantines with a reason
    # rather than being silently truncated (a partial ledger is a corrupt ledger).
    ledger_max_pdf_pages: int = 40

    # PII redaction (F12): mask account/PAN/IFSC/phone/email before any document
    # text is sent to a *live* provider. Mock mode never sends bytes anywhere.
    ledger_redact_pii: bool = True

    # Transient-failure handling for live model calls (rate limits, 5xx, timeouts).
    # Retries with exponential backoff + jitter before degrading to the
    # deterministic parser (docs/ARCHITECTURE.md §8, F6).
    ledger_max_retries: int = 3
    ledger_retry_base_delay: float = 0.5

    # Observability (optional; in-process tracing always works without these).
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
