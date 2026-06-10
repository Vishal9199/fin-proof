"""PDF worker — text-layer fast path, statement expansion, native vision lane.

Real PDFs come in two shapes, and the worker proves which one it has before
deciding anything:

  * **Born-digital** (a text layer exists) — extraction is deterministic and
    free: a statement-shaped document expands into many rows (mock mode parses
    them with `parse_statement_text`; live mode adds the model's per-row
    self-consistency pass), and a receipt-shaped one rides the existing text
    path. PII is redacted before any text leaves the process (F12).
  * **Scanned** (no text layer — e.g. a digitally-signed bank statement that is
    pure page images) — only a vision-capable live provider can read it. The
    whole PDF goes to the model as a native attachment with the per-row
    amount re-read guardrail (F11). In mock mode, or if the provider fails,
    the document is QUARANTINED with an explicit reason (F9) — pixels are
    never invented.

Page cap: an over-cap PDF is refused with a reason rather than silently
truncated — a partial statement is a corrupt ledger, not a smaller one.
"""
from __future__ import annotations

import io
import logging
import time
import uuid
from decimal import Decimal

from pypdf import PdfReader

from ..config import get_settings
from ..observability import emit_trace, score_faithfulness
from ..privacy import redact_text
from ..providers import Attachment, get_provider, parse_statement_text
from ..providers.parsing import parse_date_any
from ..runtime import get_runtime
from ..schemas import ExtractionResult, FieldConfidence, SourceType, Transaction, TxnState
from . import quarantined_txn

log = logging.getLogger("ledger.pdf")

# Below this many text-layer chars the PDF is treated as scanned. A true scan
# extracts ~0 chars (incl. the real-world BOI test statement); even a minimal
# born-digital receipt clears 30. Tiny scanner watermarks stay below it.
_MIN_TEXT_CHARS = 30
_STATEMENT_NAME_HINTS = ("statement", "stmt", "passbook")
_STATEMENT_TEXT_HINTS = (
    "statement of account", "account statement", "opening balance",
    "closing balance", "narration", "withdrawal", "deposit", "account number",
)


def read_text_layer(data: bytes) -> tuple[str, int]:
    """(extracted text, page count). Raises on a structurally unreadable PDF."""
    reader = PdfReader(io.BytesIO(data))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    return text.strip(), len(reader.pages)


def looks_like_statement(name: str, text: str) -> bool:
    lower = name.lower()
    if any(h in lower for h in _STATEMENT_NAME_HINTS):
        return True
    head = text[:4000].lower()
    if any(h in head for h in _STATEMENT_TEXT_HINTS):
        return True
    return len(parse_statement_text(text)) >= 3


async def extract_pdf(run_id: str, name: str, data: bytes) -> list[ExtractionResult]:
    rt = get_runtime()
    provider = get_provider()
    settings = get_settings()
    started = time.perf_counter()

    try:
        text, pages = read_text_layer(data)
    except Exception as exc:  # noqa: BLE001 — corrupt/encrypted → quarantine, not crash
        reason = f"Unreadable PDF ({type(exc).__name__}) — the file structure could not be parsed."
        return await _quarantine_doc(run_id, name, started, reason, kind="pdf", error=str(exc)[:200])

    if pages > settings.ledger_max_pdf_pages:
        reason = (
            f"PDF has {pages} pages — over the {settings.ledger_max_pdf_pages}-page cap. "
            "Refused rather than silently truncated; raise LEDGER_MAX_PDF_PAGES to process it."
        )
        return await _quarantine_doc(run_id, name, started, reason, kind="pdf", pages=pages)

    if len(text) >= _MIN_TEXT_CHARS:
        if looks_like_statement(name, text):
            return await _statement_from_text(run_id, name, text, started, pages)
        # Receipt/invoice-shaped: ride the existing text path on the extracted layer.
        from .vision import extract_receipt

        result = await extract_receipt(run_id, name, text.encode("utf-8"), "receipt")
        result.worker = "pdf-worker"
        return [result]

    return await _statement_from_scan(run_id, name, data, started, pages, rt, provider)


# ── Born-digital statements ────────────────────────────────────────────────────
async def _statement_from_text(
    run_id: str, name: str, text: str, started: float, pages: int
) -> list[ExtractionResult]:
    rt = get_runtime()
    provider = get_provider()
    settings = get_settings()

    tokens_in = tokens_out = 0
    model = "deterministic"
    error: str | None = None
    redactions = 0

    if provider.is_mock:
        rows = _normalize_deterministic_rows(parse_statement_text(text))
    else:
        outbound = text
        if settings.ledger_redact_pii:
            outbound, redactions = redact_text(text)
        try:
            rows, tokens_in, tokens_out, model = await provider.extract_statement(
                outbound, fast_model=rt.active_fast_model, deep_model=rt.active_deep_model
            )
        except Exception as exc:  # noqa: BLE001 — degrade to the deterministic parse (F6)
            log.warning("provider '%s' statement extraction failed for %s; degrading",
                        provider.id, name, exc_info=True)
            rows = _normalize_deterministic_rows(parse_statement_text(text))
            error = f"{type(exc).__name__}: {exc}"[:200]
            model = f"{provider.id}→mock"

    if not rows:
        reason = "Statement-shaped PDF, but no transaction rows could be recognized."
        return await _quarantine_doc(run_id, name, started, reason, kind="pdf-text",
                                     pages=pages, error=error)

    txns = [_txn_from_row(name, row, i) for i, row in enumerate(rows, start=1)]
    return await _finalize(run_id, name, txns, started, model, tokens_in, tokens_out,
                           kind="pdf-text", pages=pages, redactions=redactions, error=error)


# ── Scanned statements (native PDF vision) ────────────────────────────────────
async def _statement_from_scan(
    run_id: str, name: str, data: bytes, started: float, pages: int, rt, provider
) -> list[ExtractionResult]:
    if not provider.supports_attachments:
        reason = (
            "Scanned PDF (no text layer) — needs a vision-capable provider. "
            "Mock mode reads no pixels and never fabricates a number; configure "
            "Anthropic / Google / OpenAI on the dashboard to extract this document."
        )
        return await _quarantine_doc(run_id, name, started, reason, kind="pdf-scan", pages=pages)

    attachment = Attachment(media_type="application/pdf", data=data, name=name)
    try:
        rows, tokens_in, tokens_out, model = await provider.extract_statement(
            attachment, fast_model=rt.active_fast_model, deep_model=rt.active_deep_model
        )
    except Exception as exc:  # noqa: BLE001 — no deterministic fallback exists for pixels
        log.warning("provider '%s' scanned-PDF extraction failed for %s; quarantining",
                    provider.id, name, exc_info=True)
        reason = (
            f"Scanned-PDF extraction failed ({type(exc).__name__}) — "
            "quarantined rather than guessed."
        )
        return await _quarantine_doc(run_id, name, started, reason, kind="pdf-scan",
                                     pages=pages, error=f"{exc}"[:200],
                                     degraded_from=provider.id)

    if not rows:
        reason = "The vision model found no transaction rows in this scanned PDF."
        return await _quarantine_doc(run_id, name, started, reason, kind="pdf-scan", pages=pages)

    # 1–2 rows is receipt-shaped; a real statement expands into many.
    source_type: SourceType = "bank_pdf" if len(rows) >= 2 else "receipt"
    txns = [_txn_from_row(name, row, i, source_type=source_type)
            for i, row in enumerate(rows, start=1)]
    return await _finalize(run_id, name, txns, started, model, tokens_in, tokens_out,
                           kind="pdf-scan", pages=pages)


# ── Row → Transaction (the deterministic value contract) ──────────────────────
def _normalize_deterministic_rows(raw: list[dict]) -> list[dict]:
    """parse_statement_text rows → the provider row shape, with schema confidence
    (deterministic parse of a text layer is as trustworthy as the CSV path)."""
    return [
        {
            "merchant": r["description"],
            "amount": r["amount"],
            "txn_date": parse_date_any(r["date"]),
            "confidence": 0.95,
            "method": "schema",
        }
        for r in raw
    ]


def _txn_from_row(
    name: str, row: dict, index: int, source_type: SourceType = "bank_pdf"
) -> Transaction:
    amount: Decimal = row["amount"]
    txn_date = row["txn_date"]
    if amount <= 0 or txn_date is None:
        reason = f"Row {index} failed the value contract (amount/date unparseable)."
        return quarantined_txn(name, source_type, reason,
                               evidence=[f"{name} · row {index}: raw={row['merchant']!r}"])
    return Transaction(
        id=f"txn_{uuid.uuid4().hex[:8]}",
        source_doc=name,
        source_type=source_type,
        merchant=row["merchant"],
        amount=amount,
        txn_date=txn_date,
        state=TxnState.EXTRACTED,
        confidence={
            "amount": FieldConfidence(value=row["confidence"], method=row["method"]),
            "merchant": FieldConfidence(value=0.9, method="schema"),
            "txn_date": FieldConfidence(value=0.9, method="schema"),
        },
        evidence=[f"{name} · row {index}: {txn_date} {row['merchant']} ₹{amount}"],
    )


# ── Shared finalize / quarantine plumbing ─────────────────────────────────────
async def _finalize(
    run_id: str, name: str, txns: list[Transaction], started: float, model: str,
    tokens_in: int, tokens_out: int, *, kind: str, pages: int,
    redactions: int = 0, error: str | None = None,
) -> list[ExtractionResult]:
    latency_ms = int((time.perf_counter() - started) * 1000)
    extra = {"doc": name, "kind": kind, "pages": pages, "rows": len(txns)}
    if redactions:
        extra["redacted"] = redactions
    if error:
        extra.update(degraded=True, error=error)
    await emit_trace(
        run_id, span=f"extract:{name}", model=model, latency_ms=latency_ms,
        tokens_in=tokens_in, tokens_out=tokens_out, faithfulness=1.0, extra=extra,
    )
    return [
        ExtractionResult(
            doc_name=name, source_type=t.source_type, worker="pdf-worker",
            transaction=t, latency_ms=latency_ms, model=model,
            # Whole-document token usage rides the first row only, so any
            # aggregation over results never double-counts the model call.
            tokens_in=tokens_in if i == 0 else 0,
            tokens_out=tokens_out if i == 0 else 0,
            faithfulness=score_faithfulness(t), error=error,
        )
        for i, t in enumerate(txns)
    ]


async def _quarantine_doc(
    run_id: str, name: str, started: float, reason: str, *, kind: str,
    pages: int = 0, error: str | None = None, degraded_from: str | None = None,
) -> list[ExtractionResult]:
    txn = quarantined_txn(name, "receipt", reason)
    latency_ms = int((time.perf_counter() - started) * 1000)
    extra = {"doc": name, "kind": kind, "pages": pages, "quarantined": True}
    if degraded_from:
        extra.update(degraded=True, degraded_from=degraded_from)
    if error:
        extra["error"] = error
    await emit_trace(
        run_id, span=f"extract:{name}", model="deterministic", latency_ms=latency_ms,
        faithfulness=0.0, extra=extra,
    )
    return [
        ExtractionResult(
            doc_name=name, source_type="receipt", worker="pdf-worker",
            transaction=txn, latency_ms=latency_ms, model="deterministic",
            faithfulness=score_faithfulness(txn), error=error or reason,
        )
    ]
