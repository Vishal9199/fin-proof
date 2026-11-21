"""Vendor Memory & Active Learning Store (Institutional Memory).

When a human reviewer resolves an exception (e.g. splitting an unrecorded ₹90
platform fee or issuing a short-shipment debit note), this module captures the
decision into an institutional rule store. Future transactions from the same
vendor retrieve this rule as few-shot in-context memory, allowing the system
to propose high-confidence, verified resolutions automatically.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

log = logging.getLogger("ledger.memory")

_CACHE_DIR = Path(tempfile.gettempdir()) / "finproof_run_cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_RULES_FILE = _CACHE_DIR / "vendor_rules.json"


class VendorRule(BaseModel):
    rule_id: str
    vendor_pattern: str       # Substring or regex to match vendor
    vendor_name: str          # Canonical vendor display name
    trigger_cause: str        # e.g. "ERR_UNRECORDED_FEE", "ERR_QTY_SHORT_SHIPMENT"
    resolution_action: Literal["split_fee", "debit_note", "override", "reject"]
    default_fee_label: Optional[str] = None
    default_category: Optional[str] = None
    typical_delta: Optional[float] = None
    gl_account_code: str = "6150"
    gl_account_name: str = "Operating Variance / Surcharges"
    confidence: float = Field(default=0.98, ge=0.0, le=1.0)
    times_applied: int = 1
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_applied: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_run_id: Optional[str] = None
    learned_from: str = "HITL Manual Intervention"


# Institutional Seed Rules for standard real-world baseline
_SEED_RULES: list[dict] = [
    {
        "rule_id": "VR-SEED-001",
        "vendor_pattern": "brew",
        "vendor_name": "Brew & Co",
        "trigger_cause": "ERR_UNRECORDED_FEE",
        "resolution_action": "split_fee",
        "default_fee_label": "Service Fee / Gratuity",
        "default_category": "Food & Dining",
        "typical_delta": 90.0,
        "gl_account_code": "6150",
        "gl_account_name": "Delivery & Platform Surcharges",
        "confidence": 0.99,
        "times_applied": 14,
        "learned_from": "Institutional AP Standard Procedure",
    },
    {
        "rule_id": "VR-SEED-002",
        "vendor_pattern": "nova hardware",
        "vendor_name": "Nova Hardware Solutions Pvt Ltd",
        "trigger_cause": "ERR_QTY_SHORT_SHIPMENT",
        "resolution_action": "debit_note",
        "default_fee_label": "Vendor Shortage Debit Note",
        "default_category": "IT Equipment",
        "typical_delta": 200600.0,
        "gl_account_code": "2015",
        "gl_account_name": "Vendor Shortage Debit Notes Receivable",
        "confidence": 0.99,
        "times_applied": 8,
        "learned_from": "Enterprise 3-Way Match Contract PO-8841",
    },
    {
        "rule_id": "VR-SEED-003",
        "vendor_pattern": "swiggy",
        "vendor_name": "Swiggy",
        "trigger_cause": "ERR_UNRECORDED_FEE",
        "resolution_action": "split_fee",
        "default_fee_label": "Platform Fee & GST",
        "default_category": "Food & Dining",
        "typical_delta": 25.0,
        "gl_account_code": "6150",
        "gl_account_name": "Platform & Convenience Fees",
        "confidence": 0.96,
        "times_applied": 22,
        "learned_from": "Corporate Expense Audit Policy",
    },
]


def load_vendor_rules() -> list[VendorRule]:
    """Load vendor memory from disk, initializing with seed rules if absent."""
    if not _RULES_FILE.is_file():
        rules = [VendorRule.model_validate(r) for r in _SEED_RULES]
        save_vendor_rules(rules)
        return rules
    try:
        raw = json.loads(_RULES_FILE.read_text(encoding="utf-8"))
        return [VendorRule.model_validate(r) for r in raw]
    except Exception as e:
        log.warning("failed to load vendor rules from %s: %s; using seeds", _RULES_FILE, e)
        return [VendorRule.model_validate(r) for r in _SEED_RULES]


def save_vendor_rules(rules: list[VendorRule]) -> None:
    try:
        data = [r.model_dump(mode="json") for r in rules]
        _RULES_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("failed to persist vendor rules: %s", e)


def find_vendor_rule(merchant: str, root_cause: Optional[str] = None) -> Optional[VendorRule]:
    """Search for a learned rule matching the vendor and optional root cause."""
    rules = load_vendor_rules()
    low_m = merchant.lower().strip()
    for rule in rules:
        if rule.vendor_pattern.lower() in low_m:
            if root_cause and rule.trigger_cause and rule.trigger_cause != root_cause:
                continue
            return rule
    return None


def learn_from_resolution(
    merchant: str,
    action: str,
    amount: float,
    fee_amount: Optional[float] = None,
    fee_label: Optional[str] = None,
    category: Optional[str] = None,
    root_cause_code: Optional[str] = None,
    run_id: Optional[str] = None,
) -> VendorRule:
    """Record or update a learned vendor rule based on a human reviewer resolution."""
    rules = load_vendor_rules()
    low_m = merchant.lower().strip()

    # Look for existing rule to increment
    existing = next((r for r in rules if r.vendor_pattern.lower() in low_m or low_m in r.vendor_pattern.lower()), None)
    now_iso = datetime.now(timezone.utc).isoformat()

    if existing:
        existing.times_applied += 1
        existing.last_applied = now_iso
        existing.confidence = min(0.99, existing.confidence + 0.01)
        if fee_label:
            existing.default_fee_label = fee_label
        if fee_amount:
            existing.typical_delta = float(fee_amount)
        if category:
            existing.default_category = category
        save_vendor_rules(rules)
        return existing

    # Create new rule
    rule_num = len(rules) + 1
    new_rule = VendorRule(
        rule_id=f"VR-2026-{rule_num:03d}",
        vendor_pattern=merchant.strip()[:30].lower(),
        vendor_name=merchant.strip(),
        trigger_cause=root_cause_code or "ERR_PRICE_VARIANCE",
        resolution_action=action if action in ("split_fee", "debit_note", "override", "reject") else "override",
        default_fee_label=fee_label or ("Surcharge / Fee" if action == "split_fee" else None),
        default_category=category or "Operational Expense",
        typical_delta=float(fee_amount) if fee_amount else None,
        confidence=0.92,
        times_applied=1,
        created_at=now_iso,
        last_applied=now_iso,
        source_run_id=run_id,
        learned_from=f"HITL Manual Intervention (Run {run_id or 'Manual'})",
    )
    rules.append(new_rule)
    save_vendor_rules(rules)
    return new_rule
