"""Run the pipeline over the golden set and print a gated scorecard.

    python -m evals.run            # standard pile
    python -m evals.run --drift    # include the drifted bank export

Exits non-zero if any release gate fails, so it doubles as a CI regression check.
The evaluation runs the *real* extraction + reconciliation code paths — in mock
mode it is fully deterministic, which is exactly what makes the gates meaningful.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

try:  # Windows consoles default to cp1252; force UTF-8 so ✓ / ₹ render.
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.extraction import UploadedDoc, extract_document  # noqa: E402
from app.graph.reconciliation import run_reconciliation  # noqa: E402

from .dataset import (  # noqa: E402
    DECISION_GOLD,
    EXTRACTION_GOLD,
    GATES,
    LINK_GOLD,
)
from .metrics import PRF, mean, prf, rate  # noqa: E402

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "sample_data"


def _norm(s: str) -> str:
    return s.upper().strip()


def _amt(value) -> str:
    """Canonicalize a money value to a 2dp string so 540.0 == 540.00 in a set."""
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


def _key(merchant: str, amount) -> tuple[str, str]:
    return (_norm(merchant), _amt(amount))


@dataclass
class Scorecard:
    metrics: dict[str, float] = field(default_factory=dict)
    extras: dict[str, object] = field(default_factory=dict)
    gate_results: dict[str, bool] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(self.gate_results.values())


async def evaluate(include_drift: bool = False) -> Scorecard:
    files = sorted(p for p in SAMPLE_DIR.rglob("*") if p.is_file())
    if not include_drift:
        files = [p for p in files if "drifted" not in p.name]

    docs = [UploadedDoc(name=p.name, data=p.read_bytes()) for p in files]
    batches = await asyncio.gather(*(extract_document("eval", d) for d in docs))
    extractions = [r for b in batches for r in b]
    result = await run_reconciliation("eval", extractions, match_threshold=0.82)

    sc = Scorecard()

    # ── 1) Extraction accuracy ─────────────────────────────────────────────
    txns = [e.transaction for e in extractions if e.transaction is not None]
    amount_hits = merchant_hits = date_hits = 0
    for label in EXTRACTION_GOLD:
        match = next(
            (t for t in txns if t.source_doc == label.source_doc and _norm(t.merchant) == _norm(label.merchant)),
            None,
        )
        if match is None:
            continue
        amount_hits += int(match.amount == Decimal(label.amount))
        merchant_hits += int(_norm(match.merchant) == _norm(label.merchant))
        date_hits += int(match.txn_date.isoformat() == label.date)
    n_ext = len(EXTRACTION_GOLD)
    sc.metrics["amount_exact_rate"] = rate(amount_hits, n_ext)
    sc.extras["merchant_match_rate"] = rate(merchant_hits, n_ext)
    sc.extras["date_match_rate"] = rate(date_hits, n_ext)

    # ── 2) Decision metrics (the safety-critical layer) ──────────────────
    pred_q = {_key(t.merchant, t.amount) for t in result.quarantined}
    gold_q = {_key(d.merchant, d.amount) for d in DECISION_GOLD if d.disposition == "QUARANTINE"}
    q: PRF = prf(pred_q, gold_q)
    sc.metrics["quarantine_precision"] = q.precision
    sc.metrics["quarantine_recall"] = q.recall
    sc.extras["quarantine_f1"] = q.f1
    sc.extras["quarantine_confusion"] = {"tp": q.tp, "fp": q.fp, "fn": q.fn}

    pred_p = {_key(t.merchant, t.amount) for t in result.posted}
    gold_p = {_key(d.merchant, d.amount) for d in DECISION_GOLD if d.disposition == "POST"}
    p: PRF = prf(pred_p, gold_p)
    sc.extras["post_precision"] = p.precision
    sc.extras["post_recall"] = p.recall

    # ── 3) Link detection ─────────────────────────────────────────────
    id_to_merchant = {t.id: t.merchant for t in txns}
    pred_links = {
        (l.kind, _norm(id_to_merchant.get(l.txn_ids[0], "")))
        for l in result.links
    }
    gold_links = {(l.kind, _norm(l.merchant)) for l in LINK_GOLD}
    lk: PRF = prf(pred_links, gold_links)
    sc.metrics["link_f1"] = lk.f1
    sc.extras["link_precision"] = lk.precision
    sc.extras["link_recall"] = lk.recall

    # ── 4) Confidence calibration (separation) ──────────────────────────
    posted_conf = [t.min_confidence for t in result.posted]
    lowconf_q = [
        t.min_confidence
        for t in result.quarantined
        if t.quarantine_reason and "confidence" in t.quarantine_reason.lower()
    ]
    separation = mean(posted_conf) - mean(lowconf_q) if lowconf_q else mean(posted_conf)
    sc.metrics["confidence_gate_separation"] = round(separation, 3)
    sc.extras["mean_posted_confidence"] = round(mean(posted_conf), 3)
    sc.extras["mean_lowconf_quarantine_confidence"] = round(mean(lowconf_q), 3) if lowconf_q else None

    # ── Operational aggregates (informational) ──────────────────────────
    sc.extras["documents"] = result.documents
    sc.extras["posted_total"] = str(result.total_posted_amount)
    sc.extras["est_cost_usd"] = round(sum(e.usd_cost for e in extractions), 6)
    sc.extras["total_extract_latency_ms"] = sum(e.latency_ms for e in extractions)
    sc.extras["mean_faithfulness"] = round(mean([e.faithfulness for e in extractions]), 3)
    # Guardrail attribution: which gate caught each quarantine.
    by_guardrail: dict[str, int] = {}
    for t in result.quarantined:
        reason = (t.quarantine_reason or "").lower()
        g = "confidence" if "confidence" in reason else "schema-drift" if "schema" in reason or "contract" in reason else "reconciliation"
        by_guardrail[g] = by_guardrail.get(g, 0) + 1
    sc.extras["quarantine_by_guardrail"] = by_guardrail

    # ── Gates ─────────────────────────────────────────────────────
    for key, threshold in GATES.items():
        value = sc.metrics.get(key, 0.0)
        sc.gate_results[key] = value > threshold if key == "confidence_gate_separation" else value >= threshold

    return sc


def format_scorecard(sc: Scorecard) -> str:
    lines = []
    lines.append("=" * 64)
    lines.append("  FINPROOF — EVAL SCORECARD")
    lines.append("=" * 64)
    lines.append("  Gated metrics")
    for key, threshold in GATES.items():
        value = sc.metrics.get(key, 0.0)
        ok = sc.gate_results.get(key, False)
        cmp = ">" if key == "confidence_gate_separation" else "≥"
        mark = "✓" if ok else "✗"
        lines.append(f"    {mark} {key:<30} {value:>7.3f}   (gate {cmp} {threshold})")
    lines.append("-" * 64)
    lines.append("  Diagnostics")
    for k, v in sc.extras.items():
        lines.append(f"      {k:<34} {v}")
    lines.append("=" * 64)
    verdict = "PASS ✓ — safe to ship" if sc.passed else "FAIL ✗ — a gate regressed"
    lines.append(f"  VERDICT: {verdict}")
    lines.append("=" * 64)
    return "\n".join(lines)


async def _amain(include_drift: bool) -> int:
    sc = await evaluate(include_drift=include_drift)
    print(format_scorecard(sc))
    return 0 if sc.passed else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--drift", action="store_true", help="include the drifted bank CSV")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_amain(args.drift)))
