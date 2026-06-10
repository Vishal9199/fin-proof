"""Content-based document-type detection.

Routing decisions are made from the *bytes*, never from the filename alone — a
real pile is full of `WhatsApp Image … .jpeg` receipts and `.PDF` statements,
and an extension is user-controlled input. Magic bytes decide binary types;
text/CSV fall back to cheap heuristics. Anything unrecognizable is routed to
quarantine (F13), never guessed at.
"""
from __future__ import annotations

from typing import Literal, Optional

DocKind = Literal["csv", "text", "pdf", "image", "unknown"]

_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def media_type_of(data: bytes) -> Optional[str]:
    """Exact MIME type for binary payloads we can hand to a vision model."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    for magic, media_type in _IMAGE_MAGIC:
        if data.startswith(magic):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def sniff_kind(name: str, data: bytes) -> DocKind:
    """Classify an upload by content: pdf / image / csv / text / unknown."""
    media_type = media_type_of(data)
    if media_type == "application/pdf":
        return "pdf"
    if media_type is not None:
        return "image"

    # Not a known binary signature — decide text vs unrecognized binary.
    sample = data[:4096]
    if not sample.strip():
        return "unknown"
    if b"\x00" in sample:
        return "unknown"
    # Control characters (outside \t \n \r) are a strong binary signal.
    ctrl = sum(1 for b in sample if b < 9 or 13 < b < 32)
    if ctrl / len(sample) > 0.05:
        return "unknown"

    if name.lower().endswith(".csv"):
        return "csv"
    return "text"
