"""Vision / receipt worker.

Live mode  → Claude extracts structured fields from the image or OCR'd text, and
             the amount is extracted a *second* time; agreement drives confidence
             (this is the self-consistency guardrail, F1 in ARCHITECTURE.md).
Mock mode  → the same self-consistency check is implemented deterministically:
             the TOTAL line is compared against the summed line-items. A receipt
             whose items don't add up to its total (a smudged/faded scan) yields
             low confidence and is quarantined — no API key required.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from ..config import get_settings
from ..observability import emit_trace, score_faithfulness
from ..schemas import ExtractionResult, FieldConfidence, SourceType, Transaction, TxnState

log = logging.getLogger("ledger.vision")

# Exception class names treated as transient (retryable). Matched by name so we
# don't hard-depend on the anthropic SDK's exception hierarchy at import time.
_RETRYABLE = {
    "RateLimitError", "OverloadedError", "APIConnectionError",
    "APITimeoutError", "InternalServerError", "APIStatusError",
}


def _is_retryable(exc: Exception) -> bool:
    name = type(exc).__name__
    if name not in _RETRYABLE:
        return False
    status = getattr(exc, "status_code", None)
    if name == "APIStatusError" and status is not None:
        return status in (408, 409, 429) or status >= 500
    return True


async def _create_with_retries(client, **kwargs):
    """One model call with exponential backoff + jitter on transient failures.

    Re-raises the last error once attempts are exhausted (or on a non-transient
    error) so the caller can degrade to the deterministic parser — we retry the
    blip, but we never hang the pipeline on a hard failure (ARCHITECTURE.md F4)."""
    settings = get_settings()
    attempts = max(1, settings.ledger_max_retries)
    base = settings.ledger_retry_base_delay
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not _is_retryable(exc) or i == attempts - 1:
                raise
            delay = base * (2 ** i) + random.uniform(0, base)
            log.warning("transient model error (%s); retry %d/%d in %.2fs",
                        type(exc).__name__, i + 1, attempts - 1, delay)
            await asyncio.sleep(delay)
    raise last  # pragma: no cover — loop always returns or raises above

_AMOUNT_RE = re.compile(r"₹?\s*([\d,]+\.\d{2})")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_TOTAL_RE = re.compile(r"(?:total|grand total|amount paid)\s*:?\s*₹?\s*([\d,]+\.\d{2})", re.I)
_LINE_ITEM_RE = re.compile(r"\S.*?\s{2,}₹?\s*([\d,]+\.\d{2})\s*$")


def _to_decimal(raw: str) -> Decimal:
    try:
        return Decimal(raw.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return Decimal("0")


def _parse_receipt_text(text: str) -> dict:
    """Deterministic structured parse + self-consistency confidence."""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    merchant = lines[0].strip() if lines else "UNKNOWN"

    date_match = _DATE_RE.search(text)
    txn_date = date.fromisoformat(date_match.group(1)) if date_match else date.today()

    total_match = _TOTAL_RE.search(text)
    total = _to_decimal(total_match.group(1)) if total_match else Decimal("0")

    # Second, independent read: sum the line items (excludes the TOTAL line).
    item_sum = Decimal("0")
    for ln in lines:
        if _TOTAL_RE.search(ln):
            continue
        m = _LINE_ITEM_RE.search(ln)
        if m:
            item_sum += _to_decimal(m.group(1))

    # Self-consistency: do the two independent reads agree?
    if total == 0 and item_sum > 0:
        amount, confidence, method = item_sum, 0.78, "self_consistency"
    elif item_sum == 0:  # single-amount doc (e.g. UPI), nothing to cross-check
        single = _AMOUNT_RE.search(text)
        amount = total or (_to_decimal(single.group(1)) if single else Decimal("0"))
        confidence, method = 0.93, "ocr_agreement"
    elif abs(total - item_sum) <= Decimal("0.01"):
        amount, confidence, method = total, 0.97, "self_consistency"
    else:  # items don't reconcile to the total → ambiguous read, do not trust
        amount, confidence, method = total, 0.55, "self_consistency"

    return {
        "merchant": merchant,
        "amount": amount,
        "txn_date": txn_date,
        "confidence": confidence,
        "method": method,
        "item_sum": item_sum,
        "total": total,
    }


async def extract_receipt(
    run_id: str, name: str, data: bytes, source_type: SourceType
) -> ExtractionResult:
    settings = get_settings()
    started = time.perf_counter()

    text = data.decode("utf-8", errors="ignore")
    tokens_in = tokens_out = 0
    model = "mock"

    if not settings.mock_mode:
        try:
            parsed, tokens_in, tokens_out, model = await _extract_live(text, source_type)
        except Exception:  # noqa: BLE001 — degrade to deterministic parse (F6)
            parsed = _parse_receipt_text(text)
    else:
        parsed = _parse_receipt_text(text)

    txn = Transaction(
        id=f"txn_{uuid.uuid4().hex[:8]}",
        source_doc=name,
        source_type=source_type,
        merchant=parsed["merchant"],
        amount=parsed["amount"],
        txn_date=parsed["txn_date"],
        state=TxnState.EXTRACTED,
        confidence={
            "amount": FieldConfidence(value=parsed["confidence"], method=parsed["method"]),
            "merchant": FieldConfidence(value=0.95, method="schema"),
            "txn_date": FieldConfidence(value=0.95, method="schema"),
        },
        evidence=[f"{name}: total={parsed.get('total')} item_sum={parsed.get('item_sum')}"],
    )

    latency_ms = int((time.perf_counter() - started) * 1000)
    faithfulness = score_faithfulness(txn)
    await emit_trace(
        run_id,
        span=f"extract:{name}",
        model=model,
        latency_ms=latency_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        faithfulness=faithfulness,
        extra={"doc": name, "confidence": parsed["confidence"], "amount": str(parsed["amount"])},
    )

    return ExtractionResult(
        doc_name=name,
        source_type=source_type,
        worker="vision-worker",
        transaction=txn,
        latency_ms=latency_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        model=model,
        faithfulness=faithfulness,
    )


async def _extract_live(text: str, source_type: SourceType) -> tuple[dict, int, int, str]:
    """Live extraction via Claude with two independent amount reads (self-consistency).

    Routed to the fast model by default; the deep model is reserved for the
    ambiguous-confidence path (ARCHITECTURE.md §7, two-tier routing).
    """
    from anthropic import AsyncAnthropic

    settings = get_settings()
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    model = settings.ledger_model_fast

    prompt = (
        "Extract this receipt as JSON with keys merchant (string), "
        "amount (number, the final total paid), date (YYYY-MM-DD). "
        "Return ONLY JSON.\n\n" + text
    )
    resp = await _create_with_retries(
        client,
        model=model,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    import json

    raw = resp.content[0].text
    payload = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])

    first = _to_decimal(str(payload["amount"]))
    # Second independent read for self-consistency.
    resp2 = await _create_with_retries(
        client,
        model=model,
        max_tokens=20,
        messages=[{"role": "user", "content": "Return only the final total amount as a number.\n\n" + text}],
    )
    second = _to_decimal(re.sub(r"[^\d.]", "", resp2.content[0].text) or "0")
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
    tin = resp.usage.input_tokens + resp2.usage.input_tokens
    tout = resp.usage.output_tokens + resp2.usage.output_tokens
    return parsed, tin, tout, model
