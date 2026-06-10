"""The provider contract — one uniform surface for every model vendor.

Design: a provider only has to implement a single low-level primitive,
`complete()` (prompt → text + token usage). The *high-level* extraction logic —
the two-read self-consistency guardrail (F1) and retry/backoff on transient
failures (F6) — lives here in the base class and is therefore **identical across
Anthropic, Google, and OpenAI**. Adding a vendor means writing one method.

`MockProvider` overrides `extract_receipt()` with the deterministic parser, so
"mock" is a true first-class provider, not a special case scattered through the
pipeline.

    The LLM proposes (`complete`); deterministic code disposes
    (self-consistency, confidence, the verify gate downstream).
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Awaitable, Callable, ClassVar

from ..config import get_settings
from .parsing import json_array, json_object, parse_date_any, parse_receipt_text, to_decimal

log = logging.getLogger("ledger.providers")


class TransientProviderError(Exception):
    """Raised/flagged for retryable failures (rate limit, 5xx, timeout, network)."""


class ProviderCapabilityError(Exception):
    """The selected provider cannot perform the requested operation (e.g. binary
    attachments on a text-only provider). Never retried — the caller routes the
    document to quarantine with an explicit reason instead."""


@dataclass
class Attachment:
    """A binary document part (image or PDF) for a multimodal model call.

    Carries raw bytes + MIME type; base64 encoding happens inside each vendor
    adapter because every vendor wants a different envelope."""

    media_type: str  # "image/jpeg" | "image/png" | "image/webp" | "image/gif" | "application/pdf"
    data: bytes
    name: str = "document"


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


# Exception *class names* treated as transient across SDKs. Matched by name so we
# don't hard-depend on any vendor's exception hierarchy at import time (and so the
# system imports cleanly even when a vendor SDK isn't installed).
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
    """Best-effort, SDK-agnostic transient classification."""
    if isinstance(exc, TransientProviderError):
        return True
    name = type(exc).__name__
    status = _status_of(exc)
    if name in _RETRYABLE_NAMES:
        # For generic status-bearing errors, only retry the transient codes.
        if status is not None:
            return status in (408, 409, 429) or status >= 500
        return True
    if status is not None:
        return status in (408, 409, 429) or status >= 500
    return False


async def call_with_retries(
    provider: "LLMProvider", fn: Callable[[], Awaitable[LLMResponse]], *, op: str
) -> LLMResponse:
    """Run one model call with exponential backoff + jitter on transient failures.

    Re-raises the last error once attempts are exhausted (or immediately on a
    non-transient error) so the caller can degrade to the deterministic parser —
    we retry the blip, but never hang the pipeline on a hard failure."""
    settings = get_settings()
    attempts = max(1, settings.ledger_max_retries)
    base = settings.ledger_retry_base_delay
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not provider.is_transient(exc) or i == attempts - 1:
                raise
            delay = base * (2 ** i) + random.uniform(0, base)
            log.warning(
                "[%s] transient error on %s (%s); retry %d/%d in %.2fs",
                provider.id, op, type(exc).__name__, i + 1, attempts - 1, delay,
            )
            await asyncio.sleep(delay)
    raise last  # pragma: no cover — loop always returns or raises above


_EXTRACT_PROMPT = (
    "Extract this receipt as JSON with keys merchant (string), "
    "amount (number, the final total paid), date (YYYY-MM-DD). "
    "Return ONLY JSON.\n\n"
)
_RECHECK_PROMPT = "Return only the final total amount as a number.\n\n"

_VISUAL_EXTRACT_PROMPT = (
    "You are reading a financial document (photo, screenshot, or PDF). "
    "Classify it and extract as JSON with keys: "
    "kind ('receipt' | 'upi_payment' | 'bank_statement' | 'other'), "
    "merchant (string — the merchant/payee; for UPI payments the recipient), "
    "amount (number, the final total paid), date (YYYY-MM-DD). "
    "If this is NOT a financial transaction document, set kind to 'other'. "
    "Return ONLY JSON."
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


class LLMProvider(ABC):
    """Uniform contract the whole pipeline depends on (it never imports an SDK)."""

    id: ClassVar[str] = "base"
    is_mock: ClassVar[bool] = False
    # Whether the vendor can accept binary parts (images / PDFs). Text-only and
    # mock providers leave this False; callers quarantine instead of calling.
    supports_attachments: ClassVar[bool] = False

    @abstractmethod
    async def complete(
        self, *, model: str, prompt: str, system: str | None = None, max_tokens: int = 512
    ) -> LLMResponse:
        """One prompt → completion, with token usage. The only vendor-specific code."""

    async def complete_multimodal(
        self,
        *,
        model: str,
        prompt: str,
        attachments: list[Attachment],
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Prompt + binary parts → completion. Vendors that support vision/PDF
        input override this; the default refuses loudly (and non-transiently)."""
        raise ProviderCapabilityError(
            f"provider '{self.id}' cannot accept binary attachments"
        )

    def is_transient(self, exc: Exception) -> bool:
        """Whether a failure should be retried. Vendors may override for precision."""
        return is_transient_by_name(exc)

    async def list_models(self) -> list[tuple[str, str]]:
        """Live `(model_id, label)` list the supplied key can actually use.

        This is what powers the dashboard's model dropdown — the catalog is only a
        fallback. Live providers query their vendor's models endpoint; mock returns
        its single deterministic model."""
        raise NotImplementedError

    async def extract_receipt(
        self, text: str, source_type: str, *, fast_model: str, deep_model: str
    ) -> tuple[dict, int, int, str]:
        """Live extraction with the two-read self-consistency guardrail.

        Identical for every live vendor: extract the structured fields, then
        independently re-read just the amount; agreement drives confidence. A
        disagreement (the classic silent digit misread) collapses confidence so
        the downstream verify gate quarantines it. Returns the same dict shape
        as the deterministic parser, plus token usage and the model used.
        """
        model = fast_model
        r1 = await call_with_retries(
            self, lambda: self.complete(model=model, prompt=_EXTRACT_PROMPT + text, max_tokens=300),
            op="extract",
        )
        payload = json_object(r1.text)
        first = to_decimal(str(payload.get("amount", "0")))

        r2 = await call_with_retries(
            self, lambda: self.complete(model=model, prompt=_RECHECK_PROMPT + text, max_tokens=20),
            op="recheck",
        )
        second = to_decimal(re.sub(r"[^\d.]", "", r2.text) or "0")
        confidence = 0.97 if abs(first - second) <= Decimal("0.01") else 0.55

        parsed = {
            "merchant": str(payload.get("merchant", "UNKNOWN")),
            "amount": first,
            "txn_date": date.fromisoformat(payload["date"]) if payload.get("date") else date.today(),
            "confidence": confidence,
            "method": "self_consistency",
            "total": first,
            "item_sum": second,
        }
        return parsed, r1.input_tokens + r2.input_tokens, r1.output_tokens + r2.output_tokens, model

    async def extract_receipt_visual(
        self, attachment: Attachment, source_type: str, *, fast_model: str, deep_model: str
    ) -> tuple[dict, int, int, str]:
        """Vision extraction of a single receipt/screenshot — the same two-read
        self-consistency guardrail as the text path (F1), plus document
        classification: a non-financial image comes back `kind="other"` with
        zero confidence, so the caller quarantines instead of posting a
        hallucinated number (F10). Identical for every vision-capable vendor."""
        model = fast_model
        r1 = await call_with_retries(
            self,
            lambda: self.complete_multimodal(
                model=model, prompt=_VISUAL_EXTRACT_PROMPT, attachments=[attachment], max_tokens=300
            ),
            op="extract-visual",
        )
        payload = json_object(r1.text)
        kind = str(payload.get("kind", "receipt")).strip().lower()
        first = to_decimal(str(payload.get("amount", "0")))

        r2 = await call_with_retries(
            self,
            lambda: self.complete_multimodal(
                model=model, prompt=_VISUAL_RECHECK_PROMPT, attachments=[attachment], max_tokens=20
            ),
            op="recheck-visual",
        )
        second = to_decimal(re.sub(r"[^\d.]", "", r2.text) or "0")
        confidence = 0.97 if abs(first - second) <= Decimal("0.01") else 0.55
        if kind == "other":
            confidence = 0.0  # not a transaction document — never postable

        txn_date = parse_date_any(str(payload.get("date", ""))) or date.today()
        parsed = {
            "merchant": str(payload.get("merchant", "UNKNOWN")),
            "amount": first,
            "txn_date": txn_date,
            "confidence": confidence,
            "method": "self_consistency",
            "total": first,
            "item_sum": second,
            "kind": kind,
        }
        return parsed, r1.input_tokens + r2.input_tokens, r1.output_tokens + r2.output_tokens, model

    async def extract_statement(
        self, source: "str | Attachment", *, fast_model: str, deep_model: str
    ) -> tuple[list[dict], int, int, str]:
        """Many-row statement extraction with a per-row self-consistency guardrail.

        Read 1 extracts every row; read 2 independently re-reads just the amount
        column. Deterministic code (never the model) compares them element-wise:
        agreeing rows get high confidence, disagreeing rows collapse to 0.55 and
        are quarantined individually by the verify gate; a row-count mismatch
        collapses every row (F11). Uses the deep model — a 28-page statement is
        the expensive, correctness-critical path. Each returned row:
        {merchant, amount, txn_date (date|None), confidence, method}."""
        model = deep_model

        def _call(prompt: str, max_tokens: int):
            if isinstance(source, Attachment):
                return self.complete_multimodal(
                    model=model, prompt=prompt, attachments=[source], max_tokens=max_tokens
                )
            return self.complete(model=model, prompt=prompt + "\n\n" + source, max_tokens=max_tokens)

        r1 = await call_with_retries(self, lambda: _call(_STATEMENT_PROMPT, 8192), op="statement")
        raw_rows = json_array(r1.text)
        r2 = await call_with_retries(
            self, lambda: _call(_STATEMENT_RECHECK_PROMPT, 4096), op="statement-recheck"
        )
        try:
            recheck_amounts = [to_decimal(str(a)) for a in json_array(r2.text)]
        except ValueError:
            recheck_amounts = []

        count_ok = len(recheck_amounts) == len(raw_rows)
        rows: list[dict] = []
        for i, raw in enumerate(raw_rows):
            amount = to_decimal(str(raw.get("amount", "0")))
            agree = (
                count_ok
                and i < len(recheck_amounts)
                and abs(amount - recheck_amounts[i]) <= Decimal("0.01")
            )
            rows.append(
                {
                    "merchant": str(raw.get("description", "")).strip() or "UNKNOWN",
                    "amount": amount,
                    "txn_date": parse_date_any(str(raw.get("date", ""))),
                    "confidence": 0.96 if agree else 0.55,
                    "method": "self_consistency",
                }
            )
        tokens_in = r1.input_tokens + r2.input_tokens
        tokens_out = r1.output_tokens + r2.output_tokens
        return rows, tokens_in, tokens_out, model


# Re-exported so MockProvider and the degradation path share one parser.
__all__ = [
    "Attachment", "LLMProvider", "LLMResponse", "ProviderCapabilityError",
    "TransientProviderError", "call_with_retries", "is_transient_by_name",
    "parse_receipt_text",
]
