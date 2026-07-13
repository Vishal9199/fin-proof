"""The runtime control plane — the mutable, dashboard-driven configuration.

The model stack is decided **entirely from the dashboard**, never from the
environment. The system always **boots in deterministic mock mode** and stays
there until an operator selects a provider, supplies a key, and picks a model.
There is deliberately no env-based provider/key/model inference: that keeps the
default safe and predictable (an ambient API key in the shell can never silently
change what the agent does) and makes the dashboard the single source of truth.

`RuntimeConfig` is the live picture: selected provider, per-provider keys and
models, plus guardrail thresholds — all changeable at runtime (`PUT /config`)
with no restart. Two non-negotiables:
  * Secrets never round-trip back out — `public_snapshot()` reports only
    *whether* a key is configured, never the key itself.
  * No key (or an unknown provider) collapses to mock, so a misconfiguration
    degrades to the deterministic demo instead of erroring.

Production note: this is a process-local store (perfect for a single API task or
the demo). To run multiple replicas, back `RuntimeConfig` with a shared store
(Redis / Postgres / SSM Parameter Store) so every replica sees the same config —
the call sites (`get_runtime()`) do not change.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from .config import get_settings
from .providers.catalog import PROVIDER_IDS, PROVIDER_INFO


@dataclass
class RuntimeConfig:
    provider: str
    api_keys: dict[str, str]          # provider_id -> secret (never serialized out)
    fast_models: dict[str, str]       # provider_id -> model id
    deep_models: dict[str, str]       # provider_id -> model id
    confidence_threshold: float
    match_threshold: float
    max_concurrency: int

    # ── Effective view (what the pipeline actually uses) ─────────────────────
    @property
    def effective_provider(self) -> str:
        """The provider in force, collapsing 'needs a key but none set' → mock,
        so the system degrades to the deterministic demo instead of erroring."""
        info = PROVIDER_INFO.get(self.provider)
        if info is None:
            return "mock"
        if info.requires_key and not (self.api_keys.get(self.provider) or "").strip():
            return "mock"
        return self.provider

    @property
    def mock_mode(self) -> bool:
        return self.effective_provider == "mock"

    def api_key_for(self, provider_id: str) -> str:
        return (self.api_keys.get(provider_id) or "").strip()

    @property
    def active_fast_model(self) -> str:
        p = self.effective_provider
        return self.fast_models.get(p) or PROVIDER_INFO[p].default_fast

    @property
    def active_deep_model(self) -> str:
        p = self.effective_provider
        return self.deep_models.get(p) or PROVIDER_INFO[p].default_deep

    @property
    def provider_label(self) -> str:
        return PROVIDER_INFO[self.effective_provider].label

    def key_configured(self, provider_id: str) -> bool:
        info = PROVIDER_INFO.get(provider_id)
        if info is None or not info.requires_key:
            return True
        return bool(self.api_key_for(provider_id))

    def build_provider(self):
        """Return the active LLMProvider instance (useful for ad-hoc calls)."""
        from .providers import build_provider as _build
        p = self.effective_provider
        return _build(p, self.api_key_for(p))


    # ── Secret-safe serialization for the API ────────────────────────────────
    def public_snapshot(self) -> dict:
        """Everything the dashboard needs — and not one secret."""
        return {
            "provider": self.provider,
            "effective_provider": self.effective_provider,
            "provider_label": self.provider_label,
            "mock_mode": self.mock_mode,
            "fast_model": self.active_fast_model,
            "deep_model": self.active_deep_model,
            "models": {
                p: {"fast": self.fast_models.get(p), "deep": self.deep_models.get(p)}
                for p in PROVIDER_IDS
            },
            "keys_configured": {p: self.key_configured(p) for p in PROVIDER_IDS},
            "confidence_threshold": self.confidence_threshold,
            "match_threshold": self.match_threshold,
            "max_concurrency": self.max_concurrency,
        }


# ── Singleton + bootstrap ────────────────────────────────────────────────────
_lock = threading.RLock()
_runtime: RuntimeConfig | None = None


def _bootstrap() -> RuntimeConfig:
    """Always boot to mock with no credentials — the dashboard decides the rest.

    Model fields are seeded from the catalog only as a convenience for the UI's
    initial dropdown state; the authoritative model list is fetched live from the
    provider once a key is supplied."""
    s = get_settings()
    return RuntimeConfig(
        provider="mock",
        api_keys={p: "" for p in PROVIDER_IDS},
        fast_models={p: info.default_fast for p, info in PROVIDER_INFO.items()},
        deep_models={p: info.default_deep for p, info in PROVIDER_INFO.items()},
        confidence_threshold=s.ledger_confidence_threshold,
        match_threshold=s.ledger_match_threshold,
        max_concurrency=s.ledger_max_concurrency,
    )


def get_runtime() -> RuntimeConfig:
    global _runtime
    if _runtime is None:
        with _lock:
            if _runtime is None:
                _runtime = _bootstrap()
    return _runtime


class ConfigError(ValueError):
    """Invalid configuration update (surfaced to the API as a 400)."""


def update_runtime(
    *,
    provider: str | None = None,
    api_key: str | None = None,
    fast_model: str | None = None,
    deep_model: str | None = None,
    confidence_threshold: float | None = None,
    match_threshold: float | None = None,
    max_concurrency: int | None = None,
) -> RuntimeConfig:
    """Apply a partial configuration change atomically and return the new config.

    A blank `api_key` is treated as "leave unchanged" so saving the form without
    re-typing a secret never wipes it. Model/key edits target the provider being
    set (if `provider` is included) or the currently selected one.
    """
    from .providers import reset_provider_cache

    rt = get_runtime()
    with _lock:
        if provider is not None:
            pid = provider.strip().lower()
            if pid not in PROVIDER_INFO:
                raise ConfigError(f"Unknown provider '{provider}'.")
            rt.provider = pid

        target = (provider or rt.provider).strip().lower()

        if api_key is not None and api_key.strip() and target != "mock":
            rt.api_keys[target] = api_key.strip()

        if fast_model is not None and fast_model.strip():
            rt.fast_models[target] = fast_model.strip()
        if deep_model is not None and deep_model.strip():
            rt.deep_models[target] = deep_model.strip()

        if confidence_threshold is not None:
            rt.confidence_threshold = _clamp01(confidence_threshold, "confidence_threshold")
        if match_threshold is not None:
            rt.match_threshold = _clamp01(match_threshold, "match_threshold")
        if max_concurrency is not None:
            if int(max_concurrency) < 1:
                raise ConfigError("max_concurrency must be >= 1.")
            rt.max_concurrency = int(max_concurrency)

    # A changed key/provider must not be served by a stale cached client.
    reset_provider_cache()
    return rt


def _clamp01(value: float, name: str) -> float:
    v = float(value)
    if not 0.0 <= v <= 1.0:
        raise ConfigError(f"{name} must be between 0 and 1.")
    return v


def reset_runtime() -> None:
    """Re-bootstrap to the default mock state (used by tests for isolation)."""
    global _runtime
    with _lock:
        _runtime = None
    from .providers import reset_provider_cache

    reset_provider_cache()
