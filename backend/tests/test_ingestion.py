"""Real-document ingestion: sniffing, redaction, statement parsing, the PDF
lane, and the image lane.

These prove the production rules from docs/specs/2026-06-11-real-document-
ingestion.md: routing is decided by bytes (not filenames), PII never leaves the
process un-redacted, scanned/image documents are NEVER fabricated in mock mode
(quarantined with a reason instead), and a text-layer statement PDF expands into
many deterministic rows.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.extraction.sniff import media_type_of, sniff_kind
from app.privacy import redact_text
from app.providers.parsing import json_array, parse_date_any, parse_statement_text

# ── Tiny real binary signatures ────────────────────────────────────────────────
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WEBP = b"RIFF\x10\x00\x00\x00WEBP" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
PDF = b"%PDF-1.7\n%binary\n" + b"\x00" * 32


# ── Content sniffing: bytes decide, not filenames ─────────────────────────────
def test_sniff_binary_types_by_magic_bytes():
    assert sniff_kind("WhatsApp Image 2026-06-04.jpeg", JPEG) == "image"
    assert sniff_kind("screenshot.png", PNG) == "image"
    assert sniff_kind("pic.webp", WEBP) == "image"
    assert sniff_kind("anim.gif", GIF) == "image"
    assert sniff_kind("STATEMENT.PDF", PDF) == "pdf"


def test_sniff_ignores_lying_extensions():
    # A JPEG renamed to .txt is still an image; a PDF named .csv is still a PDF.
    assert sniff_kind("receipt.txt", JPEG) == "image"
    assert sniff_kind("export.csv", PDF) == "pdf"


def test_sniff_text_and_csv():
    assert sniff_kind("brew_co_receipt.txt", b"BREW & CO\nTOTAL  450.00\n") == "text"
    assert sniff_kind("bank_statement.csv", b"date,description,amount\n2026-05-30,X,1.00\n") == "csv"


def test_sniff_unknown_binary_and_empty():
    assert sniff_kind("blob.bin", b"\x00\x01\x02\x03" * 64) == "unknown"
    assert sniff_kind("empty.txt", b"   ") == "unknown"


def test_media_type_of():
    assert media_type_of(JPEG) == "image/jpeg"
    assert media_type_of(PDF) == "application/pdf"
    assert media_type_of(b"plain text") is None


# ── PII redaction (F12): identifiers out, amounts intact ──────────────────────
def test_redaction_masks_identifiers_keeps_amounts():
    text = (
        "Account No: 12345678901  IFSC: BKID0001234\n"
        "PAN: AABCI1234H  email: someone@example.com\n"
        "TOTAL   ₹12,100.00\n"
        "UPI payment of 450.00 on 2026-05-30\n"
    )
    clean, n = redact_text(text)
    assert "12345678901" not in clean
    assert "BKID0001234" not in clean
    assert "AABCI1234H" not in clean
    assert "someone@example.com" not in clean
    assert n == 4
    # Money and dates survive untouched.
    assert "₹12,100.00" in clean
    assert "450.00" in clean
    assert "2026-05-30" in clean


def test_redaction_masks_epic_aadhaar_phone_keeping_tail():
    clean, n = redact_text("EPIC AMB7801848 · Aadhaar 1234 5678 9012 · +91 98501 25277")
    assert "AMB7801848" not in clean and "XXXX1848" in clean
    assert "1234 5678 9012" not in clean
    assert "98501 25277" not in clean
    assert n == 3


def test_redaction_is_noop_on_clean_receipts():
    text = "BREW & CO\n2026-05-30\nLatte   450.00\nTOTAL   450.00\n"
    clean, n = redact_text(text)
    assert clean == text and n == 0


# ── Deterministic statement-row parsing ───────────────────────────────────────
STATEMENT_TEXT = """\
BANK OF EXAMPLE — STATEMENT OF ACCOUNT
Account Number: XXXX4321

Date        Narration                        Amount
2026-05-27  METRO CARD RECHARGE              200.00
2026-05-28  STELLAR MART                   1,200.00
29/05/2026  SWIGGY ORDER 8841                320.00 DR
Closing balance                            9,999.00
"""


def test_parse_statement_text_extracts_rows():
    rows = parse_statement_text(STATEMENT_TEXT)
    assert [r["description"] for r in rows] == [
        "METRO CARD RECHARGE", "STELLAR MART", "SWIGGY ORDER 8841",
    ]
    assert rows[1]["amount"] == Decimal("1200.00")
    assert parse_date_any(rows[2]["date"]) == date(2026, 5, 29)


def test_parse_statement_text_ignores_prose():
    assert parse_statement_text("Dear customer,\nyour statement is attached.\n") == []


def test_parse_date_any_rejects_garbage():
    assert parse_date_any("not-a-date") is None
    assert parse_date_any("2026-05-30") == date(2026, 5, 30)
    assert parse_date_any("30 May 2026") == date(2026, 5, 30)


# ── json_array tolerance ──────────────────────────────────────────────────────
def test_json_array_plain_and_wrapped():
    assert json_array('here you go: [{"a": 1}] done') == [{"a": 1}]
    assert json_array('{"rows": [{"a": 1}, {"a": 2}]}') == [{"a": 1}, {"a": 2}]


def test_json_array_rejects_no_array():
    import pytest

    with pytest.raises(ValueError):
        json_array('{"not": "an array"}')


# ══════════════════════════════════════════════════════════════════════════════
# PDF fixtures: a hand-built born-digital PDF (uncompressed text layer pypdf can
# read) and a pypdf-generated blank PDF (the "scanned" shape: pages, no text).
# ══════════════════════════════════════════════════════════════════════════════
def make_text_pdf(lines: list[str]) -> bytes:
    def esc(s: str) -> str:
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    content = (
        "BT /F1 12 Tf 50 750 Td "
        + " ".join(f"({esc(ln)}) Tj 0 -16 Td" for ln in lines)
        + " ET"
    )
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n".encode("latin-1")
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(out)


def make_blank_pdf(pages: int = 1) -> bytes:
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


STATEMENT_PDF_LINES = [
    "BANK OF EXAMPLE - STATEMENT OF ACCOUNT",
    "Account Number: 12345678901",
    "Date        Narration                        Amount",
    "2026-05-27  METRO CARD RECHARGE              200.00",
    "2026-05-28  STELLAR MART                   1,200.00",
    "2026-05-29  SWIGGY ORDER 8841                320.00",
]

RECEIPT_PDF_LINES = [
    "BREW & CO",
    "2026-05-30",
    "Latte              450.00",
    "TOTAL   450.00",
]


# ── PDF lane: born-digital statements expand deterministically ────────────────
async def test_text_layer_statement_pdf_expands_rows_in_mock_mode():
    from app.extraction.pdf_ingest import extract_pdf

    results = await extract_pdf("run_t", "boi_statement.pdf", make_text_pdf(STATEMENT_PDF_LINES))
    assert len(results) == 3
    assert all(r.worker == "pdf-worker" and r.source_type == "bank_pdf" for r in results)
    txns = [r.transaction for r in results]
    assert [t.merchant for t in txns] == ["METRO CARD RECHARGE", "STELLAR MART", "SWIGGY ORDER 8841"]
    assert txns[1].amount == Decimal("1200.00")
    assert all(t.state.value == "EXTRACTED" for t in txns)
    assert all(t.min_confidence >= 0.9 for t in txns)


async def test_text_layer_receipt_pdf_rides_text_path():
    from app.extraction.pdf_ingest import extract_pdf

    results = await extract_pdf("run_t", "invoice.pdf", make_text_pdf(RECEIPT_PDF_LINES))
    assert len(results) == 1
    r = results[0]
    assert r.worker == "pdf-worker" and r.source_type == "receipt"
    assert r.transaction.amount == Decimal("450.00")
    assert r.transaction.merchant == "BREW & CO"


# ── PDF lane: scans never fabricate (F9) ──────────────────────────────────────
async def test_scanned_pdf_in_mock_mode_quarantines_with_reason():
    from app.extraction.pdf_ingest import extract_pdf

    results = await extract_pdf("run_t", "BOI_Signed_Statement.pdf", make_blank_pdf(3))
    assert len(results) == 1
    txn = results[0].transaction
    assert txn.state.value == "QUARANTINE"
    assert txn.amount == Decimal("0")
    assert "vision-capable" in txn.quarantine_reason


async def test_pdf_over_page_cap_is_refused_not_truncated(monkeypatch):
    from app import config
    from app.extraction import pdf_ingest

    capped = config.get_settings().model_copy(update={"ledger_max_pdf_pages": 2})
    monkeypatch.setattr(pdf_ingest, "get_settings", lambda: capped)
    results = await pdf_ingest.extract_pdf("run_t", "huge.pdf", make_blank_pdf(3))
    txn = results[0].transaction
    assert txn.state.value == "QUARANTINE"
    assert "3 pages" in txn.quarantine_reason and "cap" in txn.quarantine_reason


async def test_corrupt_pdf_quarantines():
    from app.extraction.pdf_ingest import extract_pdf

    results = await extract_pdf("run_t", "broken.pdf", b"%PDF-1.4\ngarbage")
    assert results[0].transaction.state.value == "QUARANTINE"
    assert "Unreadable PDF" in results[0].transaction.quarantine_reason


# ── Image lane: mock mode + classification + failure (F9/F10) ─────────────────
async def test_image_in_mock_mode_quarantines_never_fabricates():
    from app.extraction.vision import extract_receipt

    res = await extract_receipt("run_t", "WhatsApp Image.jpeg", JPEG, "receipt", kind="image")
    txn = res.transaction
    assert txn.state.value == "QUARANTINE"
    assert txn.amount == Decimal("0")
    assert "vision-capable" in txn.quarantine_reason


class FakeVisionProvider:
    """Vision-capable fake: first call returns the structured read, second the
    amount-only recheck (same contract as a live vendor)."""

    from app.providers.base import LLMProvider as _Base

    def __new__(cls, first: str, second: str):
        from app.providers.base import LLMProvider, LLMResponse

        class _Impl(LLMProvider):
            id = "fake"
            supports_attachments = True

            def __init__(self):
                self.calls = 0
                self.seen_attachments = []

            async def complete(self, *, model, prompt, system=None, max_tokens=512):
                raise AssertionError("image lane must use complete_multimodal")

            async def complete_multimodal(
                self, *, model, prompt, attachments, system=None, max_tokens=1024
            ):
                self.calls += 1
                self.seen_attachments.append(attachments)
                text = first if self.calls == 1 else second
                return LLMResponse(text=text, input_tokens=10, output_tokens=5, model=model)

        return _Impl()


async def test_image_two_read_agreement_posts_and_relabels_source(monkeypatch):
    from app.extraction import vision

    fake = FakeVisionProvider(
        '{"kind": "upi_payment", "merchant": "IIT MADRAS", "amount": 12100.00, "date": "2026-06-04"}',
        "12100.00",
    )
    monkeypatch.setattr(vision, "get_provider", lambda: fake)
    res = await vision.extract_receipt("run_t", "WhatsApp Image.jpeg", JPEG, "receipt", kind="image")
    txn = res.transaction
    assert txn.state.value == "EXTRACTED"
    assert txn.amount == Decimal("12100.00")
    assert txn.source_type == "upi_screenshot"      # model re-labeled the filename guess
    assert txn.confidence["amount"].value == 0.97
    # The provider received real pixels, not decoded text.
    assert fake.seen_attachments[0][0].media_type == "image/jpeg"


async def test_image_two_read_disagreement_collapses_confidence(monkeypatch):
    from app.extraction import vision

    fake = FakeVisionProvider(
        '{"kind": "receipt", "merchant": "CAFE", "amount": 450.00, "date": "2026-05-30"}',
        "480.00",
    )
    monkeypatch.setattr(vision, "get_provider", lambda: fake)
    res = await vision.extract_receipt("run_t", "r.jpg", JPEG, "receipt", kind="image")
    assert res.transaction.confidence["amount"].value == 0.55


async def test_nonfinancial_image_is_quarantined_not_posted(monkeypatch):
    from app.extraction import vision

    fake = FakeVisionProvider(
        '{"kind": "other", "merchant": "Voter Helpline", "amount": 0, "date": ""}', "0",
    )
    monkeypatch.setattr(vision, "get_provider", lambda: fake)
    res = await vision.extract_receipt("run_t", "electoral_app.jpeg", JPEG, "receipt", kind="image")
    txn = res.transaction
    assert txn.state.value == "QUARANTINE"
    assert "non-financial" in txn.quarantine_reason


async def test_image_live_failure_quarantines_visibly(monkeypatch):
    from app.extraction import vision
    from app.providers.base import LLMProvider

    class Failing(LLMProvider):
        id = "google"
        supports_attachments = True

        async def complete(self, **kw):
            raise RuntimeError("unused")

        async def extract_receipt_visual(self, *a, **kw):
            raise ValueError("model returned no JSON")

        def is_transient(self, exc):
            return False

    monkeypatch.setattr(vision, "get_provider", lambda: Failing())
    res = await vision.extract_receipt("run_t", "r.jpg", JPEG, "receipt", kind="image")
    txn = res.transaction
    assert txn.state.value == "QUARANTINE"           # F9: no deterministic pixel parse
    assert res.model == "google→quarantine"          # the degradation is visible
    assert res.error and "no JSON" in res.error


# ── Statement extraction: per-row self-consistency (F11) ──────────────────────
class FakeStatementProvider:
    def __new__(cls, first: str, second: str):
        from app.providers.base import LLMProvider, LLMResponse

        class _Impl(LLMProvider):
            id = "fake"
            supports_attachments = True

            def __init__(self):
                self.calls = 0

            async def complete(self, *, model, prompt, system=None, max_tokens=512):
                self.calls += 1
                text = first if self.calls == 1 else second
                return LLMResponse(text=text, input_tokens=10, output_tokens=5, model=model)

            async def complete_multimodal(self, *, model, prompt, attachments,
                                          system=None, max_tokens=1024):
                return await self.complete(model=model, prompt=prompt, max_tokens=max_tokens)

        return _Impl()


ROWS_JSON = (
    '[{"date": "2026-05-27", "description": "METRO", "amount": 200.00},'
    ' {"date": "2026-05-28", "description": "STELLAR", "amount": 1200.00}]'
)


async def test_statement_rows_agree_high_confidence():
    p = FakeStatementProvider(ROWS_JSON, "[200.00, 1200.00]")
    rows, tin, tout, model = await p.extract_statement("text", fast_model="f", deep_model="d")
    assert [r["confidence"] for r in rows] == [0.96, 0.96]
    assert rows[0]["txn_date"] is not None
    assert model == "d"                              # statements use the deep model
    assert (tin, tout) == (20, 10)


async def test_statement_amount_disagreement_collapses_that_row():
    p = FakeStatementProvider(ROWS_JSON, "[200.00, 1250.00]")   # second amount misread
    rows, *_ = await p.extract_statement("text", fast_model="f", deep_model="d")
    assert rows[0]["confidence"] == 0.96
    assert rows[1]["confidence"] == 0.55             # quarantined by the verify gate


async def test_statement_count_mismatch_collapses_every_row():
    p = FakeStatementProvider(ROWS_JSON, "[200.00]")            # model lost a row
    rows, *_ = await p.extract_statement("text", fast_model="f", deep_model="d")
    assert all(r["confidence"] == 0.55 for r in rows)


# ── Vendor payload shapes (fake clients — no SDK, no network) ─────────────────
class _Boxes:
    def __init__(self, **kw):
        self.__dict__.update(kw)


async def test_anthropic_multimodal_builds_image_and_document_blocks():
    import base64

    from app.providers.anthropic_provider import AnthropicProvider
    from app.providers.base import Attachment

    seen = {}

    class FakeMessages:
        async def create(self, **kw):
            seen.update(kw)
            return _Boxes(content=[_Boxes(text="ok")],
                          usage=_Boxes(input_tokens=1, output_tokens=1))

    p = AnthropicProvider(api_key="x")
    p._client = _Boxes(messages=FakeMessages())
    await p.complete_multimodal(
        model="claude-haiku-4-5", prompt="read this",
        attachments=[Attachment("image/jpeg", b"JPG"), Attachment("application/pdf", b"PDF")],
    )
    content = seen["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/jpeg"
    assert content[0]["source"]["data"] == base64.b64encode(b"JPG").decode()
    assert content[1]["type"] == "document"          # PDFs ride native document blocks
    assert content[2] == {"type": "text", "text": "read this"}


async def test_openai_multimodal_builds_image_url_and_file_parts():
    from app.providers.base import Attachment
    from app.providers.openai_provider import OpenAIProvider

    seen = {}

    class FakeCompletions:
        async def create(self, **kw):
            seen.update(kw)
            return _Boxes(choices=[_Boxes(message=_Boxes(content="ok"))],
                          usage=_Boxes(prompt_tokens=1, completion_tokens=1))

    p = OpenAIProvider(api_key="x")
    p._client = _Boxes(chat=_Boxes(completions=FakeCompletions()))
    await p.complete_multimodal(
        model="gpt-4o", prompt="read this",
        attachments=[Attachment("image/png", b"PNG"),
                     Attachment("application/pdf", b"PDF", name="s.pdf")],
    )
    parts = seen["messages"][0]["content"]
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert parts[1]["type"] == "file"
    assert parts[1]["file"]["filename"] == "s.pdf"
    assert parts[1]["file"]["file_data"].startswith("data:application/pdf;base64,")
    assert parts[2] == {"type": "text", "text": "read this"}


async def test_google_multimodal_sends_inline_parts():
    import pytest

    pytest.importorskip("google.genai")
    from app.providers.base import Attachment
    from app.providers.google_provider import GoogleProvider

    seen = {}

    class FakeModels:
        async def generate_content(self, **kw):
            seen.update(kw)
            return _Boxes(text="ok", usage_metadata=_Boxes(
                prompt_token_count=1, candidates_token_count=1, thoughts_token_count=0))

    p = GoogleProvider(api_key="x")
    p._client = _Boxes(aio=_Boxes(models=FakeModels()))
    await p.complete_multimodal(
        model="gemini-2.5-flash", prompt="read this",
        attachments=[Attachment("application/pdf", b"PDF")],
    )
    contents = seen["contents"]
    assert contents[-1] == "read this"
    assert contents[0].inline_data.mime_type == "application/pdf"


async def test_mock_provider_refuses_attachments():
    import pytest

    from app.providers.base import Attachment, ProviderCapabilityError
    from app.providers.mock_provider import MockProvider

    p = MockProvider()
    assert p.supports_attachments is False
    with pytest.raises(ProviderCapabilityError):
        await p.complete_multimodal(model="mock", prompt="x",
                                    attachments=[Attachment("image/jpeg", b"J")])


# ── Router: content decides, unknown quarantines ──────────────────────────────
async def test_router_quarantines_unknown_binary():
    from app.extraction import UploadedDoc, extract_document

    results = await extract_document("run_t", UploadedDoc("blob.bin", b"\x00\x01" * 64))
    assert results[0].worker == "router"
    assert results[0].transaction.state.value == "QUARANTINE"


async def test_router_routes_jpeg_named_txt_to_image_lane():
    from app.extraction import UploadedDoc, extract_document

    # A JPEG with a lying .txt extension must hit the image lane (mock → quarantine
    # with the vision reason), not be decoded as garbage text.
    results = await extract_document("run_t", UploadedDoc("receipt.txt", JPEG))
    assert "vision-capable" in results[0].transaction.quarantine_reason


# ── API guards (F13) ──────────────────────────────────────────────────────────
async def test_upload_size_and_count_guards(monkeypatch):
    import httpx
    from httpx import ASGITransport

    from app import main as app_main

    tiny = app_main.get_settings().model_copy(
        update={"ledger_max_upload_mb": 1, "ledger_max_files": 2}
    )
    monkeypatch.setattr(app_main, "get_settings", lambda: tiny)
    transport = ASGITransport(app=app_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        big = b"x" * (1024 * 1024 + 1)
        r = await c.post("/reconcile", files=[("files", ("big.txt", big))])
        assert r.status_code == 413

        r = await c.post("/reconcile", files=[("files", (f"f{i}.txt", b"hi")) for i in range(3)])
        assert r.status_code == 400 and "Too many" in r.json()["detail"]

        r = await c.post("/reconcile", files=[("files", ("empty.txt", b""))])
        assert r.status_code == 400


# ── End-to-end: a real-world-shaped pile in mock mode ─────────────────────────
async def test_e2e_real_world_pile_mock_mode():
    """The real_data/ shapes: a scanned statement PDF, a born-digital statement
    PDF, a JPEG screenshot, and a text receipt — the run must complete, post the
    deterministic rows, and quarantine (not fabricate) everything pixel-based."""
    import httpx
    from httpx import ASGITransport

    from app.main import app

    files = [
        ("files", ("boi_scanned_statement.pdf", make_blank_pdf(2))),
        ("files", ("digital_statement.pdf", make_text_pdf(STATEMENT_PDF_LINES))),
        ("files", ("WhatsApp Image 2026-06-04.jpeg", JPEG)),
        ("files", ("brew_receipt.txt", b"BREW & CO\n2026-05-30\nTOTAL   450.00\n")),
    ]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        run_id = (await c.post("/reconcile", files=files)).json()["run_id"]
        async with c.stream("GET", f"/events/{run_id}") as stream:
            async for line in stream.aiter_lines():
                if "run.completed" in line:
                    break
        data = (await c.get(f"/runs/{run_id}")).json()

    # 3 statement rows + 1 text receipt posted; scan + image quarantined w/ reasons.
    assert len(data["posted"]) == 4
    assert len(data["quarantined"]) == 2
    reasons = " | ".join(t["quarantine_reason"] for t in data["quarantined"])
    assert "vision-capable" in reasons
    assert data["documents"] == 4
