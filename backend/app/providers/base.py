"""Provider contract — one uniform surface for every model vendor.

A provider implements only `complete()`. The two-read self-consistency guardrail
(F1), retry/backoff (F6), and all extraction logic live here and are identical
across Anthropic, Google, and OpenAI. Adding a vendor = one method.
"""
from __future__ import annotations

import asyncio, logging, random, re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Awaitable, Callable, ClassVar

from ..config import get_settings
from .parsing import json_array, json_object, parse_date_any, parse_receipt_text, to_decimal

log = logging.getLogger("ledger.providers")


class TransientProviderError(Exception):
    """Retryable failure (rate limit, 5xx, timeout, network)."""


class ProviderCapabilityError(Exception):
    """Provider cannot perform the operation (e.g. vision on text-only). Never retried."""


@dataclass
class Attachment:
    """Raw binary document part (image or PDF) for a multimodal call."""
    media_type: str   # "image/jpeg" | "image/png" | "image/webp" | "application/pdf"
    data: bytes
    name: str = "document"


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


_RETRYABLE_NAMES = {
    "RateLimitError", "OverloadedError", "APIConnectionError", "APITimeoutError",
    "InternalServerError", "APIStatusError", "APIError", "ServerError",
    "ServiceUnavailable", "DeadlineExceeded", "ResourceExhausted",
}


def _status_of(exc: Exception) -> int | None:
    for attr in ("status_code", "code", "http_status", "status"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    return None


def is_transient_by_name(exc: Exception) -> bool:
    if isinstance(exc, TransientProviderError):
        return True
    status = _status_of(exc)
    retryable_status = status is not None and (status in (408, 409, 429) or status >= 500)
    if type(exc).__name__ in _RETRYABLE_NAMES:
        return retryable_status if status is not None else True
    return retryable_status


async def call_with_retries(
    provider: "LLMProvider", fn: Callable[[], Awaitable[LLMResponse]], *, op: str
) -> LLMResponse:
    """Exponential backoff + jitter on transient failures; re-raises on hard failures."""
    settings = get_settings()
    attempts, base = max(1, settings.ledger_max_retries), settings.ledger_retry_base_delay
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not provider.is_transient(exc) or i == attempts - 1:
                raise
            delay = base * (2 ** i) + random.uniform(0, base)
            log.warning("[%s] transient %s on %s; retry %d/%d in %.2fs",
                        provider.id, type(exc).__name__, op, i + 1, attempts - 1, delay)
            await asyncio.sleep(delay)
    raise last  # pragma: no cover


# ── Prompts ───────────────────────────────────────────────────────────────────
_EXTRACT_PROMPT = (
    "Extract this receipt as JSON with keys merchant (string), "
    "amount (number, the final total paid), date (YYYY-MM-DD). Return ONLY JSON.\n\n"
)
_RECHECK_PROMPT = "Return only the final total amount as a number.\n\n"

_VISUAL_EXTRACT_PROMPT = (
    "You are reading a financial document (photo, screenshot, or PDF). "
    "Classify it and extract as JSON with keys: "
    "kind ('receipt' | 'upi_payment' | 'bank_statement' | 'other'), "
    "merchant (string — the merchant/payee; for UPI payments the recipient), "
    "amount (number, the final total paid), date (YYYY-MM-DD). "
    "If this is NOT a financial transaction document, set kind to 'other'. Return ONLY JSON."
)
_VISUAL_RECHECK_PROMPT = (
    "Return ONLY the final total amount paid in this document, as a plain number. "
    "If no amount is present, return 0."
)
_STATEMENT_PROMPT = (
    "This document is a bank/card statement. Extract EVERY transaction row, across "
    "all pages, as a JSON array of objects with keys: date (YYYY-MM-DD), "
    "description (string), amount (number — the absolute transaction amount). "
    "Exclude opening/closing balance lines. Return ONLY the JSON array."
)
_STATEMENT_RECHECK_PROMPT = (
    "Re-read ONLY the transaction amounts in this bank/card statement, across all "
    "pages, in document order (exclude opening/closing balance lines). "
    "Return ONLY a JSON array of plain numbers."
)


# ── Base class ────────────────────────────────────────────────────────────────
class LLMProvider(ABC):
    """Uniform contract the pipeline depends on — it never imports an SDK directly."""

    id: ClassVar[str] = "base"
    is_mock: ClassVar[bool] = False
    supports_attachments: ClassVar[bool] = False

    @abstractmethod
    async def complete(self, *, model: str, prompt: str,
                       system: str | None = None, max_tokens: int = 512) -> LLMResponse:
        """One prompt → completion + token usage. The only vendor-specific code."""

    async def complete_multimodal(self, *, model: str, prompt: str,
                                  attachments: list[Attachment],
                                  system: str | None = None, max_tokens: int = 1024) -> LLMResponse:
        """Prompt + binary parts → completion. Vendors override; default refuses."""
        raise ProviderCapabilityError(f"provider '{self.id}' cannot accept binary attachments")

    def is_transient(self, exc: Exception) -> bool:
        return is_transient_by_name(exc)

    async def list_models(self) -> list[tuple[str, str]]:
        """Live (model_id, label) list the key can actually use."""
        raise NotImplementedError

    async def extract_receipt(self, text: str, source_type: str,
                              *, fast_model: str, deep_model: str) -> tuple[dict, int, int, str]:
        """Two-read self-consistency extraction: extract → recheck amount → compare."""
        model = fast_model
        r1 = await call_with_retries(
            self, lambda: self.complete(model=model, prompt=_EXTRACT_PROMPT + text, max_tokens=300), op="extract")
        payload = json_object(r1.text)
        first = to_decimal(str(payload.get("amount", "0")))

        r2 = await call_with_retries(
            self, lambda: self.complete(model=model, prompt=_RECHECK_PROMPT + text, max_tokens=20), op="recheck")
        second = to_decimal(re.sub(r"[^\d.]", "", r2.text) or "0")
        confidence = 0.97 if abs(first - second) <= Decimal("0.01") else 0.55

        parsed = {
            "merchant": str(payload.get("merchant", "UNKNOWN")),
            "amount": first,
            "txn_date": date.fromisoformat(payload["date"]) if payload.get("date") else date.today(),
            "confidence": confidence, "method": "self_consistency",
            "total": first, "item_sum": second,
        }
        return parsed, r1.input_tokens + r2.input_tokens, r1.output_tokens + r2.output_tokens, model

    async def extract_receipt_visual(self, attachment: Attachment, source_type: str,
                                     *, fast_model: str, deep_model: str) -> tuple[dict, int, int, str]:
        """Vision two-read extraction + document classification (F1 + F10)."""
        model = fast_model
        r1 = await call_with_retries(
            self, lambda: self.complete_multimodal(
                model=model, prompt=_VISUAL_EXTRACT_PROMPT, attachments=[attachment], max_tokens=300),
            op="extract-visual")
        payload = json_object(r1.text)
        kind = str(payload.get("kind", "receipt")).strip().lower()
        first = to_decimal(str(payload.get("amount", "0")))

        r2 = await call_with_retries(
            self, lambda: self.complete_multimodal(
                model=model, prompt=_VISUAL_RECHECK_PROMPT, attachments=[attachment], max_tokens=20),
            op="recheck-visual")
        second = to_decimal(re.sub(r"[^\d.]", "", r2.text) or "0")
        confidence = 0.0 if kind == "other" else (0.97 if abs(first - second) <= Decimal("0.01") else 0.55)

        parsed = {
            "merchant": str(payload.get("merchant", "UNKNOWN")), "amount": first,
            "txn_date": parse_date_any(str(payload.get("date", ""))) or date.today(),
            "confidence": confidence, "method": "self_consistency",
            "total": first, "item_sum": second, "kind": kind,
        }
        return parsed, r1.input_tokens + r2.input_tokens, r1.output_tokens + r2.output_tokens, model

    async def extract_statement(self, source: "str | Attachment",
                                *, fast_model: str, deep_model: str) -> tuple[list[dict], int, int, str]:
        """Per-row self-consistency on bank statements: row-count mismatch collapses all (F11)."""
        model = deep_model

        def _call(prompt: str, max_tokens: int):
            if isinstance(source, Attachment):
                return self.complete_multimodal(model=model, prompt=prompt,
                                                attachments=[source], max_tokens=max_tokens)
            return self.complete(model=model, prompt=prompt + "\n\n" + source, max_tokens=max_tokens)

        r1 = await call_with_retries(self, lambda: _call(_STATEMENT_PROMPT, 8192), op="statement")
        raw_rows = json_array(r1.text)
        r2 = await call_with_retries(self, lambda: _call(_STATEMENT_RECHECK_PROMPT, 4096), op="statement-recheck")
        try:
            recheck = [to_decimal(str(a)) for a in json_array(r2.text)]
        except ValueError:
            recheck = []

        count_ok = len(recheck) == len(raw_rows)
        rows = []
        for i, raw in enumerate(raw_rows):
            amount = to_decimal(str(raw.get("amount", "0")))
            agree = count_ok and i < len(recheck) and abs(amount - recheck[i]) <= Decimal("0.01")
            rows.append({
                "merchant": str(raw.get("description", "")).strip() or "UNKNOWN",
                "amount": amount,
                "txn_date": parse_date_any(str(raw.get("date", ""))),
                "confidence": 0.96 if agree else 0.55,
                "method": "self_consistency",
            })
        return rows, r1.input_tokens + r2.input_tokens, r1.output_tokens + r2.output_tokens, model


__all__ = [
    "Attachment", "LLMProvider", "LLMResponse", "ProviderCapabilityError",
    "TransientProviderError", "call_with_retries", "is_transient_by_name",
    "parse_receipt_text",
]
