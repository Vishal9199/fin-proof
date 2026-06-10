"""Vision / receipt worker — provider-agnostic, for text AND real images.

Text lane  → the configured provider extracts structured fields from the
             document text (PII-redacted first, F12), with the two-read
             self-consistency guardrail (F1). Mock mode runs the same check
             deterministically; a failed live call degrades to that parse (F6).
Image lane → real pixels (JPEG/PNG/WEBP/GIF) go to the provider as native
             vision content blocks via the shared `extract_receipt_visual`,
             which also *classifies* the document: a non-financial image is
             quarantined (F10), never turned into a number. There is no
             deterministic pixel parser, so mock mode — or a failed live call —
             quarantines with an explicit reason (F9). Pixels are never invented.
"""
from __future__ import annotations

import logging
import time
import uuid

from ..config import get_settings
from ..observability import emit_trace, score_faithfulness
from ..privacy import redact_text
from ..providers import Attachment, get_provider, parse_receipt_text
from ..runtime import get_runtime
from ..schemas import ExtractionResult, FieldConfidence, SourceType, Transaction, TxnState
from . import quarantined_txn
from .sniff import media_type_of

log = logging.getLogger("ledger.vision")

# The model's visual classification re-labels the filename heuristic.
_KIND_TO_SOURCE: dict[str, SourceType] = {
    "receipt": "receipt",
    "upi_payment": "upi_screenshot",
}


async def extract_receipt(
    run_id: str, name: str, data: bytes, source_type: SourceType, *, kind: str = "text"
) -> ExtractionResult:
    if kind == "image":
        return await _extract_image(run_id, name, data, source_type)
    return await _extract_text(run_id, name, data, source_type)


# ── Text lane (receipts, UPI text exports, text-layer PDFs) ───────────────────
async def _extract_text(
    run_id: str, name: str, data: bytes, source_type: SourceType
) -> ExtractionResult:
    rt = get_runtime()
    provider = get_provider()
    settings = get_settings()
    started = time.perf_counter()

    text = data.decode("utf-8", errors="ignore")
    tokens_in = tokens_out = 0
    model = "mock"
    degraded_from: str | None = None
    error: str | None = None
    redactions = 0

    # PII leaves the process only redacted; mock mode never sends bytes anywhere.
    outbound = text
    if not provider.is_mock and settings.ledger_redact_pii:
        outbound, redactions = redact_text(text)

    try:
        parsed, tokens_in, tokens_out, model = await provider.extract_receipt(
            outbound, source_type, fast_model=rt.active_fast_model, deep_model=rt.active_deep_model
        )
    except Exception as exc:  # noqa: BLE001 — degrade to the deterministic parse (F6)
        log.warning(
            "provider '%s' extraction failed for %s; degrading to deterministic parse",
            provider.id, name, exc_info=True,
        )
        parsed = parse_receipt_text(text)
        tokens_in = tokens_out = 0
        # Surface the fallback in AgentOps instead of letting a failed live
        # provider masquerade as a clean mock run — that silent-degradation blind
        # spot is exactly what an observability layer is supposed to catch. The
        # model id becomes e.g. "google→mock", which shows in the trace + keeps
        # the cost meter honest (unknown id → $0, since no real tokens were spent).
        degraded_from = provider.id
        error = f"{type(exc).__name__}: {exc}"[:200]
        model = f"{provider.id}→mock"

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

    extra = {"doc": name, "kind": "text", "confidence": parsed["confidence"],
             "amount": str(parsed["amount"])}
    if redactions:
        extra["redacted"] = redactions
    if degraded_from:
        extra.update(degraded=True, degraded_from=degraded_from, error=error)
    return await _finish(run_id, name, source_type, txn, started, model,
                         tokens_in, tokens_out, extra, error)


# ── Image lane (photos / screenshots — real pixels) ───────────────────────────
async def _extract_image(
    run_id: str, name: str, data: bytes, source_type: SourceType
) -> ExtractionResult:
    rt = get_runtime()
    provider = get_provider()
    started = time.perf_counter()
    media_type = media_type_of(data) or "image/jpeg"

    if not provider.supports_attachments:
        reason = (
            "Image needs a vision-capable provider — mock mode reads no pixels "
            "and never fabricates a number. Configure Anthropic / Google / OpenAI "
            "on the dashboard to extract this document."
        )
        txn = quarantined_txn(name, source_type, reason)
        extra = {"doc": name, "kind": "image", "quarantined": True}
        return await _finish(run_id, name, source_type, txn, started, "mock", 0, 0, extra, reason)

    attachment = Attachment(media_type=media_type, data=data, name=name)
    try:
        parsed, tokens_in, tokens_out, model = await provider.extract_receipt_visual(
            attachment, source_type, fast_model=rt.active_fast_model, deep_model=rt.active_deep_model
        )
    except Exception as exc:  # noqa: BLE001 — no deterministic fallback exists for pixels
        log.warning("provider '%s' vision extraction failed for %s; quarantining",
                    provider.id, name, exc_info=True)
        error = f"{type(exc).__name__}: {exc}"[:200]
        reason = (
            f"Vision extraction failed ({type(exc).__name__}) — "
            "quarantined rather than guessed."
        )
        txn = quarantined_txn(name, source_type, reason)
        extra = {"doc": name, "kind": "image", "quarantined": True,
                 "degraded": True, "degraded_from": provider.id, "error": error}
        return await _finish(run_id, name, source_type, txn, started,
                             f"{provider.id}→quarantine", 0, 0, extra, error)

    doc_kind = parsed.get("kind", "receipt")
    if doc_kind == "other":
        reason = "Classified as non-financial — no transaction to post (kind=other)."
        txn = quarantined_txn(
            name, source_type, reason,
            evidence=[f"{name}: model read merchant={parsed['merchant']!r} "
                      f"amount={parsed['amount']} kind=other"],
        )
        extra = {"doc": name, "kind": "image", "doc_kind": doc_kind, "quarantined": True}
        return await _finish(run_id, name, source_type, txn, started, model,
                             tokens_in, tokens_out, extra, None)

    source_type = _KIND_TO_SOURCE.get(doc_kind, source_type)
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
        evidence=[f"{name}: visual two-read total={parsed.get('total')} "
                  f"recheck={parsed.get('item_sum')} kind={doc_kind}"],
    )
    extra = {"doc": name, "kind": "image", "doc_kind": doc_kind,
             "confidence": parsed["confidence"], "amount": str(parsed["amount"])}
    return await _finish(run_id, name, source_type, txn, started, model,
                         tokens_in, tokens_out, extra, None)


# ── Shared trace + result plumbing ────────────────────────────────────────────
async def _finish(
    run_id: str, name: str, source_type: SourceType, txn: Transaction, started: float,
    model: str, tokens_in: int, tokens_out: int, extra: dict, error: str | None,
) -> ExtractionResult:
    latency_ms = int((time.perf_counter() - started) * 1000)
    faithfulness = score_faithfulness(txn)
    await emit_trace(
        run_id, span=f"extract:{name}", model=model, latency_ms=latency_ms,
        tokens_in=tokens_in, tokens_out=tokens_out, faithfulness=faithfulness, extra=extra,
    )
    return ExtractionResult(
        doc_name=name, source_type=source_type, worker="vision-worker",
        transaction=txn, latency_ms=latency_ms, tokens_in=tokens_in,
        tokens_out=tokens_out, model=model, faithfulness=faithfulness, error=error,
    )
