"""AgentOps: the layer that turns autonomous behavior into something you can
inspect, score, and trust.

Every node in the pipeline emits a trace span carrying latency, token usage,
USD cost, the model used, and a *faithfulness* score. Faithfulness here is
computed deterministically (does the extracted value have backing evidence?) —
we do not ask another LLM to grade the first, because that just stacks
uncertainty. Spans stream to the dashboard's AgentOps tab and, if configured,
persist to Langfuse for historical dashboards and regression datasets.
"""
from __future__ import annotations

from .config import get_settings
from .events import bus
from .schemas import Transaction

# Approximate public pricing (USD per 1M tokens). Used for the live cost meter;
# kept as a table so model routing decisions are auditable.
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (15.0, 75.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "mock": (0.0, 0.0),
}


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = _PRICING.get(model, (0.0, 0.0))
    return round((tokens_in * price_in + tokens_out * price_out) / 1_000_000, 6)


def score_faithfulness(txn: Transaction | None) -> float:
    """Deterministic faithfulness: fraction of core claims backed by evidence.

    A confidently-extracted value with no evidence trail is the dangerous case —
    it scores low and (via the guardrail) gets stripped/quarantined.
    """
    if txn is None:
        return 0.0
    has_evidence = 1.0 if txn.evidence else 0.0
    grounded = sum(
        1 for f in ("merchant", "amount", "txn_date") if f in txn.confidence
    ) / 3.0
    return round(0.5 * has_evidence + 0.5 * grounded, 3)


async def emit_trace(
    run_id: str,
    *,
    span: str,
    model: str,
    latency_ms: int,
    tokens_in: int = 0,
    tokens_out: int = 0,
    faithfulness: float = 1.0,
    extra: dict | None = None,
) -> dict:
    """Build a trace span, push it to the live dashboard, and (optionally) Langfuse."""
    trace = {
        "span": span,
        "model": model,
        "latency_ms": latency_ms,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "usd_cost": estimate_cost(model, tokens_in, tokens_out),
        "faithfulness": faithfulness,
        **(extra or {}),
    }
    await bus.publish(run_id, "trace", trace)
    _maybe_langfuse(run_id, trace)
    return trace


def _maybe_langfuse(run_id: str, trace: dict) -> None:
    settings = get_settings()
    if not settings.langfuse_enabled:
        return
    try:  # best-effort; never let telemetry break the run
        from langfuse import Langfuse  # type: ignore

        lf = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        lf.trace(name=trace["span"], metadata={"run_id": run_id, **trace})
    except Exception:  # noqa: BLE001 — telemetry is non-critical
        pass
