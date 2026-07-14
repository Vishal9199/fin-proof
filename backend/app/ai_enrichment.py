"""AI enrichment — merchant normalization, category tagging, narrative summary,
anomaly explanations, and the conversational ledger query.

All functions are provider-agnostic: they call get_runtime() and fall back to
clean deterministic responses in mock mode so the dashboard works without a key.
"""
from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING

from .runtime import get_runtime

if TYPE_CHECKING:
    from .schemas import Transaction

# ── Category taxonomy ─────────────────────────────────────────────────────────
CATEGORIES = [
    "Food & Dining", "Transport", "Shopping", "Entertainment",
    "Healthcare", "Utilities & Bills", "Travel", "Education",
    "Groceries", "Finance & Banking", "Other",
]

# Deterministic keyword map used in mock mode and as a fast pre-filter
_KW: list[tuple[list[str], str]] = [
    (["zomato", "swiggy", "cafe", "restaurant", "pizza", "food", "brew", "coffee", "eat", "bake", "kitchen", "dhaba", "bistro", "hotel"], "Food & Dining"),
    (["ola", "uber", "rapido", "cab", "auto", "metro", "irctc", "train", "bus", "taxi", "fleet"], "Transport"),
    (["amazon", "flipkart", "myntra", "ajio", "nykaa", "meesho", "shop", "mart", "store", "bazaar"], "Shopping"),
    (["netflix", "spotify", "prime", "hotstar", "zee5", "sony", "bookmyshow", "pvr", "cinema", "theatre"], "Entertainment"),
    (["apollo", "medplus", "netmeds", "pharma", "hospital", "clinic", "doctor", "health", "care", "lab"], "Healthcare"),
    (["electricity", "bescom", "tata power", "airtel", "jio", "bsnl", "vodafone", "gas", "water", "municipal", "bill", "recharge"], "Utilities & Bills"),
    (["makemytrip", "goibibo", "cleartrip", "booking", "hotel", "airbnb", "oyo", "flight", "airline"], "Travel"),
    (["udemy", "coursera", "school", "college", "university", "tuition", "education", "book", "institute"], "Education"),
    (["bigbasket", "blinkit", "zepto", "grofer", "dmart", "supermarket", "grocery", "vegetables", "fruits"], "Groceries"),
    (["hdfc", "icici", "sbi", "axis", "kotak", "emi", "loan", "insurance", "mutual fund", "atm", "bank", "finance"], "Finance & Banking"),
]

_NORMALIZE_MAP: dict[str, str] = {
    "amzn": "Amazon", "amazon": "Amazon",
    "zomato": "Zomato", "swiggy": "Swiggy",
    "ola": "Ola Cabs", "uber": "Uber",
    "flipkart": "Flipkart",
    "netflix": "Netflix", "spotify": "Spotify",
    "airtel": "Airtel", "jio": "Jio",
}


def _keyword_category(merchant: str) -> str:
    m = merchant.lower()
    for keywords, cat in _KW:
        if any(k in m for k in keywords):
            return cat
    return "Other"


def _clean_merchant(raw: str) -> str:
    """Deterministic cleanup: strip trailing codes, normalize spacing."""
    cleaned = re.sub(r"\*\w+", "", raw)          # strip *12AB34
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    low = cleaned.lower()
    for k, v in _NORMALIZE_MAP.items():
        if k in low:
            return v
    return cleaned.title()


async def enrich_transaction(txn: "Transaction") -> "Transaction":
    """Normalize merchant name and assign a spending category.

    In mock mode this is fully deterministic (keyword map + cleanup).
    With a live provider the LLM is called once for both tasks.
    """
    rt = get_runtime()
    raw = txn.merchant

    if rt.mock_mode:
        txn.normalized_merchant = _clean_merchant(raw)
        txn.category = _keyword_category(txn.normalized_merchant)
        return txn

    # Live path — one call for both normalization and classification
    try:
        provider = rt.build_provider()
        resp = await provider.complete(
            model=rt.active_fast_model,
            prompt=(
                f"Given the raw merchant string \"{raw}\" from a financial transaction:\n"
                f"1. Return a clean, human-readable merchant name (e.g. 'Amazon', 'Zomato').\n"
                f"2. Classify it into exactly one of: {', '.join(CATEGORIES)}.\n"
                f"Respond ONLY as JSON: {{\"merchant\": \"...\", \"category\": \"...\"}}."
            ),
            max_tokens=60,
        )
        import json as _json
        data = _json.loads(re.search(r"\{.*\}", resp.text, re.DOTALL).group())
        txn.normalized_merchant = str(data.get("merchant", raw)).strip() or raw
        cat = str(data.get("category", "Other")).strip()
        txn.category = cat if cat in CATEGORIES else "Other"
    except Exception:  # noqa: BLE001 — degrade gracefully
        txn.normalized_merchant = _clean_merchant(raw)
        txn.category = _keyword_category(txn.normalized_merchant)
    return txn


async def generate_anomaly_explanation(txn: "Transaction", context: str) -> str:
    """Return a plain-English explanation of why a transaction was quarantined.

    Falls back to the existing quarantine_reason string in mock mode.
    """
    rt = get_runtime()
    if rt.mock_mode or not txn.quarantine_reason:
        return txn.quarantine_reason or "Flagged for manual review."

    try:
        provider = rt.build_provider()
        resp = await provider.complete(
            model=rt.active_fast_model,
            prompt=(
                f"A financial transaction was quarantined. Explain briefly (2-3 sentences) "
                f"why it looks suspicious, using plain English for a non-technical reviewer.\n\n"
                f"Merchant: {txn.merchant}\n"
                f"Amount: ₹{txn.amount}\n"
                f"Date: {txn.txn_date}\n"
                f"System reason: {txn.quarantine_reason}\n"
                f"Context: {context}\n\n"
                f"Write ONLY the explanation, no bullet points."
            ),
            max_tokens=120,
        )
        return resp.text.strip()
    except Exception:  # noqa: BLE001
        return txn.quarantine_reason or "Flagged for manual review."


async def generate_narrative(
    posted: list["Transaction"],
    quarantined: list["Transaction"],
    total_amount: Decimal,
    documents: int,
    categories: dict[str, float],
) -> str:
    """Generate a 3–4 sentence plain-English narrative of the reconciliation run."""
    rt = get_runtime()

    top_cats = sorted(categories.items(), key=lambda x: -x[1])[:3]
    top_str = ", ".join(f"{c} (₹{v:.0f})" for c, v in top_cats)

    if rt.mock_mode:
        quarantine_note = (
            f"{len(quarantined)} transaction{'s' if len(quarantined) != 1 else ''} "
            f"{'were' if len(quarantined) != 1 else 'was'} quarantined for manual review."
            if quarantined else "All transactions passed the confidence gate."
        )
        return (
            f"Reconciled {documents} document{'s' if documents != 1 else ''} totalling ₹{total_amount:.2f} "
            f"across {len(posted)} posted transaction{'s' if len(posted) != 1 else ''}. "
            f"Top spending categories: {top_str or 'N/A'}. "
            f"{quarantine_note} "
            f"Review the quarantine lane for items needing your attention before closing the books."
        )

    try:
        provider = rt.build_provider()
        quarantine_details = "; ".join(
            f"{t.merchant} ₹{t.amount} ({t.quarantine_reason or 'flagged'})" for t in quarantined[:5]
        ) or "none"
        resp = await provider.complete(
            model=rt.active_fast_model,
            prompt=(
                f"Write a concise 3–4 sentence financial reconciliation summary for a CFO. "
                f"Be specific, professional, and actionable. No markdown or bullet points.\n\n"
                f"Documents processed: {documents}\n"
                f"Posted transactions: {len(posted)}\n"
                f"Total posted amount: ₹{total_amount:.2f}\n"
                f"Quarantined: {len(quarantined)}\n"
                f"Top spending categories: {top_str or 'N/A'}\n"
                f"Quarantine details: {quarantine_details}"
            ),
            max_tokens=200,
        )
        return resp.text.strip()
    except Exception:  # noqa: BLE001
        return (
            f"Reconciled {documents} documents · ₹{total_amount:.2f} posted across "
            f"{len(posted)} transactions · {len(quarantined)} quarantined."
        )


def build_categories_summary(transactions: list["Transaction"]) -> dict[str, float]:
    """Aggregate posted transaction amounts by category."""
    totals: dict[str, float] = defaultdict(float)
    for t in transactions:
        cat = t.category or "Other"
        totals[cat] += float(t.amount)
    return dict(sorted(totals.items(), key=lambda x: -x[1]))


async def answer_ledger_query(
    question: str,
    posted: list["Transaction"],
    quarantined: list["Transaction"],
) -> dict:
    """Answer a free-text question about the reconciled ledger, returning text + chart if requested."""
    rt = get_runtime()

    def _txn_lines(txns: list["Transaction"]) -> str:
        return "\n".join(
            f"  - {t.normalized_merchant or t.merchant} | ₹{t.amount} | {t.txn_date} | {t.category or 'Other'}"
            for t in txns
        ) or "  (none)"

    ledger_ctx = (
        f"POSTED ({len(posted)} transactions):\n{_txn_lines(posted)}\n\n"
        f"QUARANTINED ({len(quarantined)} transactions):\n{_txn_lines(quarantined)}"
    )

    if rt.mock_mode:
        q = question.lower()
        total = sum(float(t.amount) for t in posted)
        
        # Specific categories first
        if "food" in q or "dining" in q:
            food = [t for t in posted if t.category == "Food & Dining"]
            s = sum(float(t.amount) for t in food)
            return {
                "answer": f"₹{s:.2f} spent on Food & Dining across {len(food)} transaction(s).",
                "chart": {
                    "type": "bar",
                    "data": [{"label": "Food & Dining", "value": s}]
                }
            }
        if "transport" in q or "ola" in q or "uber" in q or "cab" in q:
            transport = [t for t in posted if t.category == "Transport"]
            s = sum(float(t.amount) for t in transport)
            return {
                "answer": f"₹{s:.2f} spent on Transport across {len(transport)} transaction(s).",
                "chart": {
                    "type": "bar",
                    "data": [{"label": "Transport", "value": s}]
                }
            }
        if "shop" in q or "amazon" in q or "flipkart" in q:
            shop = [t for t in posted if t.category == "Shopping"]
            s = sum(float(t.amount) for t in shop)
            return {
                "answer": f"₹{s:.2f} spent on Shopping across {len(shop)} transaction(s).",
                "chart": {
                    "type": "bar",
                    "data": [{"label": "Shopping", "value": s}]
                }
            }
            
        # General queries
        if "quarantine" in q or "flagged" in q:
            return {
                "answer": f"{len(quarantined)} transaction(s) are quarantined. "
                        + (", ".join(f"{t.merchant} ₹{t.amount}" for t in quarantined[:3]) or "None.")
            }
        if "category" in q or "categories" in q or "breakdown" in q:
            cats = build_categories_summary(posted)
            return {
                "answer": "Spending by category: " + ", ".join(f"{k} ₹{v:.0f}" for k, v in cats.items()),
                "chart": {
                    "type": "bar",
                    "data": [{"label": k, "value": v} for k, v in cats.items()]
                }
            }
            
        # Generic totals last
        if "total" in q or "how much" in q or "spent" in q:
            cats = build_categories_summary(posted)
            return {
                "answer": f"Total posted amount is ₹{total:.2f} across {len(posted)} transactions.",
                "chart": {
                    "type": "bar",
                    "data": [{"label": k, "value": v} for k, v in cats.items()]
                }
            }
            
        return {
            "answer": f"Based on the reconciled ledger with {len(posted)} posted transactions totalling ₹{total:.2f}, I can answer questions about spending, categories, merchants, or flagged items."
        }

    try:
        provider = rt.build_provider()
        prompt = (
            f"Ledger data:\n{ledger_ctx}\n\nQuestion: {question}\n\n"
            f"If the user asks about categories, breakdowns, comparisons, or totals, "
            f"reply with your natural language text answer, AND include a JSON block representing the chart data "
            f"like this at the end of your response:\n"
            f"```chart\n"
            f'{{"type": "bar", "data": [{{"label": "Food & Dining", "value": 450}}, {{"label": "Transport", "value": 150}}]}}\n'
            f"```"
        )
        resp = await provider.complete(
            model=rt.active_fast_model,
            system=(
                "You are a financial assistant. Answer questions about the reconciled ledger "
                "provided. Be concise (2-3 sentences max). Use ₹ for Indian Rupee amounts."
            ),
            prompt=prompt,
            max_tokens=250,
        )
        text = resp.text.strip()
        chart_match = re.search(r"```chart\s*(\{.*\})\s*```", text, re.DOTALL)
        chart_data = None
        if chart_match:
            try:
                import json as _json
                chart_data = _json.loads(chart_match.group(1))
                text = text.replace(chart_match.group(0), "").strip()
            except Exception:
                pass
        
        res = {"answer": text}
        if chart_data:
            res["chart"] = chart_data
        return res
    except Exception as exc:  # noqa: BLE001
        return {"answer": f"Could not process query: {exc}"}


async def generate_quarantine_resolution_suggestion(
    txn: "Transaction",
    posted: list["Transaction"],
    quarantined: list["Transaction"],
) -> dict:
    """Propose AI-assisted resolution options for a quarantined transaction."""
    rt = get_runtime()
    
    # Check if there is a matching transaction or conflict pattern.
    # In mock mode, we build high-quality deterministic responses for the sample data.
    if rt.mock_mode:
        m = txn.merchant.lower()
        if "brew" in m or "brew & co" in m:
            return {
                "explanation": "Amount mismatch between Brew & Co receipt (₹450.00) and bank statement (₹540.00). This is typically caused by an unprinted tips, service charges, or local taxes.",
                "actions": [
                    {"label": "Use Statement Amount (₹540.00)", "action": "override", "amount": 540.00, "merchant": "Brew & Co", "category": "Food & Dining"},
                    {"label": "Use Receipt Amount (₹450.00)", "action": "override", "amount": 450.00, "merchant": "Brew & Co", "category": "Food & Dining"},
                    {"label": "Dismiss Transaction", "action": "reject"}
                ]
            }
        elif "zest" in m or "cafe" in m:
            return {
                "explanation": "This receipt was quarantined due to low OCR confidence when reading the faded page. The extracted merchant is Cafe Zest and amount is ₹360.00.",
                "actions": [
                    {"label": "Approve current details (₹360.00)", "action": "override", "amount": 360.00, "merchant": "Cafe Zest", "category": "Food & Dining"},
                    {"label": "Dismiss Transaction", "action": "reject"}
                ]
            }
        else:
            return {
                "explanation": f"This transaction was flagged due to: {txn.quarantine_reason or 'manual review flag'}.",
                "actions": [
                    {"label": f"Approve as ₹{txn.amount}", "action": "override", "amount": float(txn.amount), "merchant": txn.merchant, "category": txn.category or "Other"},
                    {"label": "Dismiss Transaction", "action": "reject"}
                ]
            }

    try:
        provider = rt.build_provider()
        prompt = (
            f"A financial transaction was quarantined. Analyze it and recommend how a human reviewer should resolve it.\n\n"
            f"Quarantined Transaction:\n"
            f"  ID: {txn.id}\n"
            f"  Merchant: {txn.merchant}\n"
            f"  Amount: ₹{txn.amount}\n"
            f"  Date: {txn.txn_date}\n"
            f"  Quarantine Reason: {txn.quarantine_reason}\n\n"
            f"Posted Transactions Context:\n"
            + "\n".join(f"  - {t.merchant} | ₹{t.amount} | {t.txn_date} | {t.category}" for t in posted[:10])
            + "\n\nOther Quarantined Transactions:\n"
            + "\n".join(f"  - {t.merchant} | ₹{t.amount} | {t.txn_date}" for t in quarantined[:5] if t.id != txn.id)
            + "\n\nProvide a brief explanation of the problem (2 sentences) and exactly 2 or 3 actions a human could take to resolve it.\n"
            "Respond ONLY as JSON matching this schema: \n"
            '{"explanation": "...", "actions": [{"label": "Use ₹XYZ (Statement)", "action": "override", "amount": XYZ, "merchant": "...", "category": "..."}, {"label": "Dismiss Transaction", "action": "reject"}]}'
        )
        resp = await provider.complete(
            model=rt.active_fast_model,
            prompt=prompt,
            max_tokens=250,
        )
        import json as _json
        data = _json.loads(re.search(r"\{.*\}", resp.text, re.DOTALL).group())
        return data
    except Exception as e:
        return {
            "explanation": f"Quarantined: {txn.quarantine_reason or 'No reason provided'}. (AI suggestion failed: {e})",
            "actions": [
                {"label": f"Approve as ₹{txn.amount}", "action": "override", "amount": float(txn.amount), "merchant": txn.merchant, "category": txn.category or "Other"},
                {"label": "Dismiss Transaction", "action": "reject"}
            ]
        }
