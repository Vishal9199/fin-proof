"""Deterministic cross-source reconciliation.

Matching is intentionally NOT an LLM call. It must be fast, cheap, explainable,
and unit-testable, so it is pure code over RapidFuzz. Two transactions are
candidates for the same real-world purchase when their date is within a small
window and their merchant names are fuzzy-similar. Among candidates:

  * amounts agree                → DUPLICATE (collapse to one)  [F2]
  * amounts disagree             → ANOMALY  (quarantine both)   [F3]

The amount comparison is the whole point: the system surfaces the discrepancy a
human would miss (receipt ₹450 vs statement ₹540) instead of averaging it away.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from itertools import combinations

from rapidfuzz import fuzz

from ..schemas import MatchLink, Transaction

_DATE_WINDOW = timedelta(days=2)
_ENTERPRISE_DATE_WINDOW = timedelta(days=14)

_ENTERPRISE_SOURCES = {"purchase_order", "goods_receipt", "tax_invoice", "bank_feed"}


def _merchant_sim(a: str, b: str) -> float:
    return fuzz.token_set_ratio(a.lower(), b.lower()) / 100.0


def reconcile(txns: list[Transaction], match_threshold: float) -> list[MatchLink]:
    """Find links/duplicates/anomalies among VERIFIED transactions.
    
    Supports both retail/personal cross-referencing and enterprise 3-way matching
    (PO ↔ GRN ↔ Tax Invoice ↔ Bank Settlement Feed).
    """
    links: list[MatchLink] = []
    for a, b in combinations(txns, 2):
        is_enterprise = a.source_type in _ENTERPRISE_SOURCES or b.source_type in _ENTERPRISE_SOURCES
        max_days = _ENTERPRISE_DATE_WINDOW.days if is_enterprise else _DATE_WINDOW.days
        
        # Check reference number match (e.g. PO-8841) or date proximity
        has_ref_match = bool(a.reference_no and b.reference_no and a.reference_no.lower() == b.reference_no.lower())
        
        if not has_ref_match and abs((a.txn_date - b.txn_date).days) > max_days:
            continue

        sim = 1.0 if has_ref_match else _merchant_sim(a.merchant, b.merchant)
        if sim < match_threshold and not has_ref_match:
            continue

        amounts_agree = abs(a.amount - b.amount) <= Decimal("0.01")
        diff = abs(a.amount - b.amount)
        max_amt = max(a.amount, b.amount)
        pct = round(float(diff / max_amt * 100), 2) if max_amt > 0 else 0.0

        if amounts_agree:
            link_kind = "3way_match" if is_enterprise else "duplicate"
            detail = (
                f"3-Way match confirmed across {a.source_type} and {b.source_type}: "
                f"{a.merchant} ₹{a.amount}."
                if is_enterprise else
                f"Same purchase across {a.source_type} and {b.source_type}: "
                f"{a.merchant} ₹{a.amount}. Collapsed to one entry."
            )
            links.append(
                MatchLink(
                    kind=link_kind,
                    txn_ids=[a.id, b.id],
                    score=round(sim, 3),
                    detail=detail,
                    variance_amount=Decimal("0.00"),
                    variance_percent=0.0,
                )
            )
        else:
            link_kind = "quantity_discrepancy" if (a.source_type == "goods_receipt" or b.source_type == "goods_receipt") else "anomaly"
            detail = (
                f"Discrepancy between {a.source_type} and {b.source_type}: "
                f"{a.source_doc} ₹{a.amount} vs {b.source_doc} ₹{b.amount} "
                f"(Δ ₹{diff}, {pct}% variance). Flagged for review."
            )
            links.append(
                MatchLink(
                    kind=link_kind,
                    txn_ids=[a.id, b.id],
                    score=round(sim, 3),
                    detail=detail,
                    variance_amount=diff,
                    variance_percent=pct,
                )
            )
            # Surface explicit WHAT, WHERE, HOW in transaction metadata
            a.discrepancy = {
                "what": f"Amount mismatch: {a.source_type} (₹{a.amount}) vs {b.source_type} (₹{b.amount})",
                "where": f"Quarantine Lane (safety gate: isolated from posted ledger; doc: {a.source_doc})",
                "how_to_resolve": f"Review variance of ₹{diff} ({pct}%). Use AI Suggest to approve, split fee/discount, or reject.",
                "delta": str(diff),
                "variance_percent": pct,
                "counterpart_doc": b.source_doc,
                "counterpart_amount": str(b.amount),
                "counterpart_type": b.source_type,
            }
            b.discrepancy = {
                "what": f"Amount mismatch: {b.source_type} (₹{b.amount}) vs {a.source_type} (₹{a.amount})",
                "where": f"Quarantine Lane (safety gate: isolated from posted ledger; doc: {b.source_doc})",
                "how_to_resolve": f"Review variance of ₹{diff} ({pct}%). Use AI Suggest to approve, split fee/discount, or reject.",
                "delta": str(diff),
                "variance_percent": pct,
                "counterpart_doc": a.source_doc,
                "counterpart_amount": str(a.amount),
                "counterpart_type": a.source_type,
            }
    return links
