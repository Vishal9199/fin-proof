"""The self-verify guardrail — the EXTRACTED → VERIFIED gate.

This is deterministic on purpose. The model proposes a transaction; this code
decides whether it is trustworthy enough to advance. Two guards:

  1. Confidence gate  — every field must clear `τ` (LEDGER_CONFIDENCE_THRESHOLD).
  2. Citation gate    — every record must carry an evidence trail; an uncited
                        claim is treated as ungrounded and quarantined.

Anything that fails is routed to QUARANTINE *with a reason*, never dropped and
never silently posted (docs/ARCHITECTURE.md §8).
"""
from __future__ import annotations

from ..runtime import get_runtime
from ..schemas import Transaction, TxnState


def verify(txn: Transaction) -> Transaction:
    # Threshold comes from the live control plane, so it is tunable from the
    # dashboard without a restart (defaults to the env/boot value).
    tau = get_runtime().confidence_threshold

    # If a worker already quarantined it (e.g. schema drift), respect that.
    if txn.state == TxnState.QUARANTINE:
        return txn

    if not txn.evidence:
        txn.state = TxnState.QUARANTINE
        txn.quarantine_reason = "Uncited claim: no evidence trail backing the extraction."
        return txn

    low = [name for name, c in txn.confidence.items() if c.value < tau]
    if low:
        worst = min(txn.confidence[n].value for n in low)
        txn.state = TxnState.QUARANTINE
        txn.quarantine_reason = (
            f"Low confidence on {', '.join(low)} "
            f"({worst:.0%} < {tau:.0%}) — likely ambiguous/misread value."
        )
        return txn

    txn.state = TxnState.VERIFIED
    return txn
