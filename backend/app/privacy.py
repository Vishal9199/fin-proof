"""Deterministic PII redaction for outbound model payloads (F12).

Real financial documents carry account numbers, PAN, IFSC, phone numbers, and
emails. In live mode the document text becomes a third-party API payload, so we
strip identifiers *before* the bytes leave the process. Redaction is pure regex
— deterministic, testable, and applied at exactly one boundary (the worker, just
before a live provider call). Mock mode never sends anything anywhere, so it
reads the raw text.

What survives, deliberately: amounts. Money tokens are short digit runs broken
by commas/decimal points (`12,100.00` → runs of 2/3/2), so the ≥9-digit rule
cannot touch them. The trade-off — a separator-free amount of ₹10 crore+ would
be masked — is acceptable: that document belongs in quarantine for human eyes
anyway.

Pixels cannot be redacted here: images and scanned PDFs are sent to the
configured provider as-is (documented in docs/SECURITY.md). Mock mode keeps
every byte local.
"""
from __future__ import annotations

import re

# Order matters: structured identifiers first, the broad long-digit rule last.
_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")),
    ("pan", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),                 # Indian PAN
    ("ifsc", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),              # bank IFSC
    ("epic", re.compile(r"\b[A-Z]{3}\d{7}\b")),                     # voter id
    ("aadhaar", re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b")),
    ("phone", re.compile(r"\+\d{1,3}[\s-]?\d{4,5}[\s-]?\d{5,6}\b")),
    ("digits", re.compile(r"\d{9,}")),                               # acct/card/ref numbers
)


def _mask(token: str) -> str:
    """Keep the last 4 characters so a human can still cross-reference."""
    tail = token[-4:] if len(token) > 4 else ""
    return "XXXX" + tail


def redact_text(text: str) -> tuple[str, int]:
    """Return (redacted_text, number_of_redactions)."""
    count = 0

    def _sub(m: re.Match) -> str:
        nonlocal count
        count += 1
        return _mask(m.group())

    for _, pattern in _PATTERNS:
        text = pattern.sub(_sub, text)
    return text, count
