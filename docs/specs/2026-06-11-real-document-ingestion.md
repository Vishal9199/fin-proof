# Real-Document Ingestion — Design Spec

**Date:** 2026-06-11 · **Status:** Approved · **Scope:** one feature branch

## 1. Problem

The pipeline today is text-first. The "vision-worker" decodes uploaded bytes as
UTF-8 (`vision.py`), so a real JPEG receipt or a bank-statement PDF turns into
binary garbage, gets a low-confidence parse, and is quarantined. Safe — but zero
value extracted. The provider contract (`LLMProvider.complete`) only carries a
text prompt; no vendor path exists for image or PDF content.

A real user pile (the `real_data/` validation set) contains exactly the four
shapes production will see:

| Document | Shape | What it demands |
|---|---|---|
| BOI bank statement, 28 pp | **Scanned PDF, no text layer** | native PDF/vision model input + many-row expansion |
| Lenovo tax invoice, 12 pp | **Born-digital PDF, full text layer** | deterministic text extraction, no model required for the bytes |
| Razorpay payment screenshot | **JPEG image** | true vision extraction (base64 content blocks) |
| Electoral-roll app photo | **JPEG, not financial** | classify as non-financial → quarantine, never hallucinate a number |

## 2. Principles (unchanged)

The LLM proposes; deterministic code disposes. Every new lane keeps the
two-read self-consistency guardrail, evidence trails, the verify gate, and the
"quarantine, never fabricate" rule. Mock mode stays fully offline and
deterministic: anything that *requires* pixels to read is quarantined in mock
mode with an explicit reason — pixels are never invented.

## 3. Components

### 3.1 Content sniffer — `app/extraction/sniff.py`
`sniff_kind(name, data) -> DocKind` where
`DocKind = "csv" | "text" | "pdf" | "image" | "unknown"`.
Decided by magic bytes (`%PDF-`, JPEG `FF D8 FF`, PNG, WEBP/RIFF, GIF), then by
extension/text heuristics for csv/text. Unknown binary → quarantine with reason.
`media_type_of(data)` returns the exact MIME for attachments. Routing no longer
trusts file extensions for binary types.

### 3.2 PII redaction — `app/privacy.py`
`redact_text(text) -> (clean, n_redactions)`; applied to **every outbound live
LLM payload** built from document text (mock mode never leaves the process).
Masks: digit runs ≥ 9 keeping last 4 (account/card/EPIC/IRN numbers), Indian PAN
(`AAAAA9999A`), IFSC (`AAAA0XXXXXX`), emails, +91-style phone groups. Amounts
survive (commas/decimal points break digit runs). Config: `ledger_redact_pii`
(default **true**). Pixels can't be masked — documented in SECURITY.md: images
and scanned PDFs go to the configured provider as-is; mock mode keeps all bytes
local.

### 3.3 Multimodal provider contract — `app/providers/`
* `Attachment(media_type: str, data: bytes)` dataclass (base64 happens inside
  each vendor adapter — formats differ).
* `LLMProvider.supports_attachments: ClassVar[bool]` — `True` for Anthropic,
  Google, OpenAI; `False` for Mock/base.
* New vendor primitive `complete_multimodal(model, prompt, attachments, system,
  max_tokens)` → `LLMResponse`:
  * **Anthropic** — `image` blocks (base64) and `document` blocks for PDFs.
  * **Google** — `types.Part.from_bytes(data, mime_type)` parts (images + PDFs).
  * **OpenAI** — `image_url` data-URI parts for images; `file` content parts
    (`file_data` data-URI) for PDFs.
* Shared high-level methods on the base class (identical for all vendors, like
  today's `extract_receipt`):
  * `extract_receipt_visual(attachment, source_type, fast_model, deep_model)` —
    read 1: structured JSON `{merchant, amount, date, kind}`; read 2:
    amount-only re-read; agreement → 0.97, disagreement → 0.55. `kind ∈
    {receipt, upi_payment, bank_statement, other}`; `other` → flagged
    non-financial (worker quarantines).
  * `extract_statement(source: str | Attachment, fast_model, deep_model)` —
    read 1: JSON array of `{date, description, amount}` rows; read 2:
    independent `{row_count, amount_sum}` re-read; count+sum agreement drives
    row confidence (0.96 agree / 0.55 disagree). Uses the **deep model** and a
    large output budget — a 28-page statement is the expensive, correctness-
    critical path. Every row re-validated deterministically (date parses,
    amount > 0) before becoming a Transaction.

### 3.4 PDF lane — `app/extraction/pdf_ingest.py` (new dep: `pypdf`, BSD, pure-Python)
1. Probe text layer (per page, `pypdf`). Page cap `ledger_max_pdf_pages`
   (default 40) → over-cap quarantines with reason (never silently truncate a
   statement).
2. **Text layer present** → statement heuristic (`statement`-like keywords or ≥ 3
   date+amount rows): statement → `extract_statement` on redacted text (mock:
   deterministic `parse_statement_text` in `parsing.py`); otherwise single
   receipt via the existing text path.
3. **No text layer (scanned)** → live vision-capable provider: send the PDF as a
   native attachment through `extract_statement` (a receipt PDF is just a 1-row
   statement; the model's `kind` labels rows). Mock mode → quarantine: *"Scanned
   PDF — needs a vision-capable provider; mock mode reads no pixels."*
4. Statement rows get `source_type="bank_pdf"` (new `SourceType` member);
   receipt-like PDFs stay `receipt`. The downstream engine is already
   source-agnostic.

### 3.5 Image lane — `vision.py` extension
`image` kind + live provider → `extract_receipt_visual`. `kind == "other"` →
quarantine (non-financial). Mock mode or live failure after retries →
quarantined stub transaction (amount 0, confidence 0, reason + `degraded_from`
trace extra) — same observable-degradation pattern as F6, but **never** a
fabricated parse, because there is no deterministic pixel parser. Text files
keep today's path (now redacted before live calls).

### 3.6 Upload guards — `main.py`
`ledger_max_upload_mb` (default 15) per file and `ledger_max_files` (default 40)
per run; violations → structured 413/400 naming the offending file. Empty files
rejected.

### 3.7 Frontend
Dropzone hint + `accept` attribute updated (images/PDFs). No logic change — the
dashboard already renders whatever `source_type`/events the backend emits.

## 4. Failure modes added

| # | Failure | Guardrail |
|---|---|---|
| F9 | Scanned/image doc in mock mode (no pixels readable) | quarantine with explicit reason; never fabricate |
| F10 | Non-financial document uploaded | model `kind=other` → quarantine "non-financial" |
| F11 | Statement row drift (model miscounts/missums rows) | count+sum second read collapses row confidence → verify gate quarantines |
| F12 | PII in outbound prompts | deterministic redaction before every live text payload |
| F13 | Oversized/unknown uploads | size/count/type guards → structured 4xx, quarantine for unknown binary |

## 5. Testing

* Unit: sniffer magic bytes; redaction (amounts survive, PAN/IFSC/acct masked);
  statement text parser; PDF text-layer probe (generated via `pypdf.PdfWriter`);
  page-cap and empty-PDF behavior.
* Provider contract: fake clients assert exact vendor payload shapes (image
  block / document block / Part / image_url / file part) — same pattern as the
  existing response-shape tests; statement count/sum agreement & disagreement;
  `kind=other` flag.
* Worker/e2e: image in mock mode → quarantined not fabricated; scanned PDF in
  mock mode → quarantined with reason; text-layer statement PDF → rows posted
  deterministically; oversized upload → 413; full `/reconcile` run with a mixed
  real-shape pile completes and streams events.
* Eval gates unchanged (golden set untouched) and must stay green.

## 6. Out of scope

OCR engines (tesseract et al.) — native model vision replaces them; local
image redaction; multi-currency; persisting uploads (stay in-memory).
