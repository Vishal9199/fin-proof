"""Provider-agnostic text parsing helpers.

This is the deterministic core that powers two things at once:

  * **Mock mode** — the receipt's TOTAL line is cross-checked against the summed
    line-items. A scan whose items don't reconcile to its total yields low
    confidence and is quarantined, with *no API key of any provider required*.
  * **Graceful degradation (F6)** — when a live provider call fails, the worker
    falls back to exactly this parse, so a run always completes.

Keeping it here (pure stdlib, no app imports) lets every provider — and the
fallback path — share one implementation with zero risk of circular imports.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_AMOUNT_RE = re.compile(r"₹?\s*([\d,]+\.\d{2})")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_TOTAL_RE = re.compile(r"(?:total|grand total|amount paid)\s*:?\s*₹?\s*([\d,]+\.\d{2})", re.I)
_LINE_ITEM_RE = re.compile(r"\S.*?\s{2,}₹?\s*([\d,]+\.\d{2})\s*$")

# A statement row: a leading date, a description, and a trailing money column.
_STMT_ROW_RE = re.compile(
    r"^\s*(?P<date>\d{4}-\d{2}-\d{2}|\d{2}[/-]\d{2}[/-]\d{4}|\d{2}\s[A-Za-z]{3}\s\d{4})"
    r"\s+(?P<desc>.+?)\s{2,}₹?\s*(?P<amount>[\d,]+\.\d{2})\s*(?:CR|DR)?\s*$",
    re.I,
)

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d %b %Y")


def to_decimal(raw) -> Decimal:
    """Parse a money token to Decimal, tolerating commas / ₹ / stray whitespace."""
    try:
        return Decimal(str(raw).replace(",", "").replace("₹", "").strip())
    except (InvalidOperation, AttributeError):
        return Decimal("0")


def json_object(raw: str) -> dict:
    """Extract the first JSON object from a model's (possibly chatty) reply."""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in model response")
    return json.loads(raw[start : end + 1])


def json_array(raw: str) -> list:
    """Extract a JSON array from a model reply, tolerating an object wrapper
    (some models return `{"rows": [...]}` despite being asked for a bare array)."""
    start, end = raw.find("["), raw.rfind("]")
    if start != -1 and end > start:
        try:
            value = json.loads(raw[start : end + 1])
            if isinstance(value, list):
                return value
        except json.JSONDecodeError:
            pass
    obj = json_object(raw)
    for value in obj.values():
        if isinstance(value, list):
            return value
    raise ValueError("no JSON array found in model response")


def parse_date_any(raw: str) -> date | None:
    """Parse the date formats Indian bank documents actually use; None if unparseable
    (an unparseable date must quarantine the row, never silently become 'today')."""
    raw = str(raw).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_statement_text(text: str) -> list[dict]:
    """Deterministic statement-row extraction from a text-layer statement.

    Powers mock mode and the degradation path for statement PDFs, mirroring what
    `parse_receipt_text` does for receipts. Each row: {date, description, amount}.
    """
    rows: list[dict] = []
    for line in text.splitlines():
        m = _STMT_ROW_RE.match(line.rstrip())
        if not m:
            continue
        rows.append(
            {
                "date": m.group("date"),
                "description": m.group("desc").strip(),
                "amount": to_decimal(m.group("amount")),
            }
        )
    return rows


def parse_receipt_text(text: str) -> dict:
    """Deterministic structured parse + self-consistency confidence.

    Returns the same dict shape every provider produces, so the vision worker is
    agnostic to whether the extraction came from a model or from this fallback.
    """
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    merchant = lines[0].strip() if lines else "UNKNOWN"

    date_match = _DATE_RE.search(text)
    txn_date = date.fromisoformat(date_match.group(1)) if date_match else date.today()

    total_match = _TOTAL_RE.search(text)
    total = to_decimal(total_match.group(1)) if total_match else Decimal("0")

    # Second, independent read: sum the line items (excludes the TOTAL line).
    item_sum = Decimal("0")
    for ln in lines:
        if _TOTAL_RE.search(ln):
            continue
        m = _LINE_ITEM_RE.search(ln)
        if m:
            item_sum += to_decimal(m.group(1))

    # Self-consistency: do the two independent reads agree?
    if total == 0 and item_sum > 0:
        amount, confidence, method = item_sum, 0.78, "self_consistency"
    elif item_sum == 0:  # single-amount doc (e.g. UPI), nothing to cross-check
        single = _AMOUNT_RE.search(text)
        amount = total or (to_decimal(single.group(1)) if single else Decimal("0"))
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
