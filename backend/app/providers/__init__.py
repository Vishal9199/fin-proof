"""Provider registry — the factory the pipeline calls.

`get_provider()` reads the runtime control plane (provider + key chosen on the
dashboard) and returns a ready provider instance. Instances are cached
by (provider_id, api_key) so we reuse the underlying HTTP client across calls but
rebuild instantly when the key/provider changes at runtime — which is what makes
the dashboard's "switch provider" truly plug-and-play.
"""
from __future__ import annotations

from collections import OrderedDict

from .anthropic_provider import AnthropicProvider
from .base import (
    Attachment,
    LLMProvider,
    LLMResponse,
    ProviderCapabilityError,
    TransientProviderError,
    call_with_retries,
)
from .catalog import PROVIDER_IDS, PROVIDER_INFO, ProviderInfo, get_model_pricing
from .google_provider import GoogleProvider
from .mock_provider import MockProvider
from .openai_provider import OpenAIProvider
from .parsing import parse_receipt_text, parse_statement_text

_BUILDERS: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "google": GoogleProvider,
    "openai": OpenAIProvider,
    "mock": MockProvider,
}

# Small bounded cache: provider clients are cheap, but reusing them avoids
# reconstructing an HTTP client (and its connection pool) on every document.
_CACHE: "OrderedDict[tuple[str, str], LLMProvider]" = OrderedDict()
_CACHE_MAX = 16


def build_provider(provider_id: str, api_key: str = "") -> LLMProvider:
    cls = _BUILDERS.get(provider_id, MockProvider)
    cache_key = (provider_id, api_key)
    inst = _CACHE.get(cache_key)
    if inst is None:
        inst = MockProvider() if provider_id == "mock" else cls(api_key)
        _CACHE[cache_key] = inst
        _CACHE.move_to_end(cache_key)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return inst


def get_provider() -> LLMProvider:
    """The provider selected by the runtime config, with key-aware mock fallback."""
    from ..runtime import get_runtime  # local import avoids an import cycle

    rt = get_runtime()
    pid = rt.effective_provider  # already collapses "needs key but none" → "mock"
    return build_provider(pid, rt.api_key_for(pid))


def reset_provider_cache() -> None:
    """Drop cached clients (used by tests, and after a key rotation)."""
    _CACHE.clear()


__all__ = [
    "Attachment", "LLMProvider", "LLMResponse", "ProviderCapabilityError",
    "TransientProviderError", "call_with_retries",
    "PROVIDER_INFO", "PROVIDER_IDS", "ProviderInfo", "get_model_pricing",
    "get_provider", "build_provider", "reset_provider_cache",
    "parse_receipt_text", "parse_statement_text",
]
