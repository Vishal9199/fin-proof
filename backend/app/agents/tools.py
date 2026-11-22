"""Domain-specific mathematical and statutory tools for financial AI agents.

Every tool operates with exact Decimal precision (zero float imprecision)
and encodes statutory regulatory standards (e.g., Indian Income Tax Act 1961,
CGST Act 2017 Sec 34/51, SOX 404 mathematical invariance).
"""
from __future__ import annotations

import datetime
from decimal import Decimal, ROUND_HALF_UP
import itertools
from typing import Any, Optional


def solve_subset_sum(
    target_amount: Decimal | str | float,
    open_items: list[dict[str, Any]],
    tolerance: Decimal | str | float = Decimal("0.01"),
    max_items: int = 15,
) -> dict[str, Any]:
    """Solve the multi-invoice consolidated batch settlement problem (N-to-1).

    Identifies which subset of open invoices/credits sum up exactly to a consolidated
    bank wire or ledger payment within the specified tolerance.

    Parameters:
        target_amount: The target lump-sum settlement amount (Decimal).
        open_items: List of dicts representing candidates, each with keys
                    {"id": str, "amount": Decimal|float|str, "reference": str}.
        tolerance: Acceptable variance delta (default 0.01).
        max_items: Maximum subset search candidates to bound computation.
    """
    target = Decimal(str(target_amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    tol = Decimal(str(tolerance)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    parsed_items = []
    for item in open_items[:max_items]:
        try:
            amt = Decimal(str(item.get("amount", 0))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            parsed_items.append({
                "id": str(item.get("id", "")),
                "reference": str(item.get("reference", item.get("id", ""))),
                "amount": amt,
                "raw": item,
            })
        except Exception:
            continue

    n = len(parsed_items)
    best_match = None
    min_diff = None

    # Search combinations of sizes 1 through n
    for r in range(1, n + 1):
        for combo in itertools.combinations(parsed_items, r):
            combo_sum = sum(x["amount"] for x in combo)
            diff = abs(combo_sum - target)
            if diff <= tol:
                return {
                    "matched": True,
                    "target_amount": str(target),
                    "matched_sum": str(combo_sum),
                    "residual_delta": str(combo_sum - target),
                    "matched_count": len(combo),
                    "matched_items": [
                        {"id": x["id"], "reference": x["reference"], "amount": str(x["amount"])}
                        for x in combo
                    ],
                    "matched_ids": [x["id"] for x in combo],
                    "algorithm": "Exact Combinatorial Subset-Sum (NP-Complete bounded)",
                }
            if min_diff is None or diff < min_diff:
                min_diff = diff
                best_match = combo

    return {
        "matched": False,
        "target_amount": str(target),
        "closest_sum": str(sum(x["amount"] for x in best_match)) if best_match else "0.00",
        "closest_delta": str(min_diff) if min_diff is not None else str(target),
        "tested_candidates": n,
        "algorithm": "Exact Combinatorial Subset-Sum (No exact subset matched)",
    }


def calculate_tax_withholding(
    gross_amount: Decimal | str | float,
    section: str = "194C",
    is_corporate: bool = True,
    pan_available: bool = True,
) -> dict[str, Any]:
    """Calculate statutory Tax Deducted at Source (TDS) under Income Tax Act / GST Act.

    Standard Indian Statutory Sections:
      - 194C: Contractor payments (1% Individual/HUF, 2% Corporate/Firm)
      - 194J: Professional & Technical fees (10% Professional, 2% Technical)
      - 194I: Rent (10% Land/Building, 2% Plant/Machinery)
      - 194Q: Purchase of Goods (0.1% on amounts exceeding ₹50 Lakhs)
      - GST_TDS: Section 51 CGST Act (2% total: 1% CGST + 1% SGST, or 2% IGST)
      - 206AA: Higher rate for non-furnishing of PAN (minimum 20%)
    """
    gross = Decimal(str(gross_amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sec_upper = section.upper().strip()

    if not pan_available:
        rate = Decimal("0.20")
        applied_sec = "Section 206AA (Higher TDS: No PAN)"
    elif sec_upper in ("194C", "SEC_194C", "CONTRACTOR"):
        rate = Decimal("0.02") if is_corporate else Decimal("0.01")
        applied_sec = "Section 194C (Payments to Contractors/Sub-contractors)"
    elif sec_upper in ("194J", "SEC_194J", "PROFESSIONAL"):
        rate = Decimal("0.10")
        applied_sec = "Section 194J (Fees for Professional Services)"
    elif sec_upper in ("194J_TECH", "TECHNICAL"):
        rate = Decimal("0.02")
        applied_sec = "Section 194J (Fees for Technical Services)"
    elif sec_upper in ("194I", "RENT"):
        rate = Decimal("0.10")
        applied_sec = "Section 194I (Rent on Land, Building or Furniture)"
    elif sec_upper in ("194Q", "GOODS_PURCHASE"):
        rate = Decimal("0.001")
        applied_sec = "Section 194Q (Deduction of Tax at Source on Payment of Goods)"
    elif sec_upper in ("GST_TDS", "SEC_51", "GST_SEC_51"):
        rate = Decimal("0.02")
        applied_sec = "CGST Act 2017 Section 51 (Tax Deduction at Source)"
    else:
        # Default fallback to 2% corporate contractor
        rate = Decimal("0.02")
        applied_sec = f"Statutory Rate for {section}"

    tds_amount = (gross * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    net_payable = (gross - tds_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "gross_amount": str(gross),
        "section": applied_sec,
        "rate_percent": float(rate * 100),
        "tds_withheld": str(tds_amount),
        "net_payable": str(net_payable),
        "journal_hint": {
            "dr_expense_or_vendor": str(gross),
            "cr_tds_payable": str(tds_amount),
            "cr_bank_disbursement": str(net_payable),
        },
        "statutory_compliance_note": (
            f"Tax deduction of ₹{tds_amount} ({float(rate * 100)}%) under {applied_sec}. "
            f"Deposit required by the 7th of subsequent month per statutory timelines."
        ),
    }


def evaluate_contract_terms(
    invoice_amount: Decimal | str | float,
    invoice_date: datetime.date | str,
    payment_date: datetime.date | str,
    discount_term: str = "2/10 net 30",
) -> dict[str, Any]:
    """Evaluate prompt payment commercial cash discounts (e.g. '2/10 net 30').

    '2/10 net 30' means 2% discount if remitted within 10 days of invoice date,
    otherwise full net amount due within 30 days.
    """
    inv_amt = Decimal(str(invoice_amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if isinstance(invoice_date, str):
        inv_dt = datetime.date.fromisoformat(invoice_date)
    else:
        inv_dt = invoice_date

    if isinstance(payment_date, str):
        pay_dt = datetime.date.fromisoformat(payment_date)
    else:
        pay_dt = payment_date

    days_elapsed = (pay_dt - inv_dt).days

    # Parse standard discount term: "<pct>/<days> net <net_days>"
    discount_pct = Decimal("0.02")
    eligibility_days = 10
    net_days = 30

    try:
        parts = discount_term.lower().replace("net", "").split()
        if len(parts) >= 2:
            slash_part = parts[0].split("/")
            discount_pct = Decimal(slash_part[0]) / Decimal("100")
            eligibility_days = int(slash_part[1])
            net_days = int(parts[1])
    except Exception:
        discount_pct = Decimal("0.02")
        eligibility_days = 10
        net_days = 30

    is_eligible = 0 <= days_elapsed <= eligibility_days
    discount_amount = (inv_amt * discount_pct).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if is_eligible else Decimal("0.00")
    expected_payment = (inv_amt - discount_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "term": discount_term,
        "invoice_amount": str(inv_amt),
        "days_elapsed": days_elapsed,
        "eligibility_window_days": eligibility_days,
        "is_discount_eligible": is_eligible,
        "discount_percent": float(discount_pct * 100),
        "discount_amount": str(discount_amount),
        "expected_settlement_amount": str(expected_payment),
        "commercial_rationale": (
            f"Payment executed on day {days_elapsed} (within {eligibility_days}-day prompt payment window). "
            f"Entitled to {float(discount_pct * 100)}% commercial cash discount of ₹{discount_amount}."
            if is_eligible else
            f"Payment executed on day {days_elapsed} (exceeded {eligibility_days}-day prompt payment window). "
            f"Full invoice amount of ₹{inv_amt} is contractually due."
        ),
    }


def draft_statutory_debit_note(
    vendor_name: str,
    invoice_ref: str,
    po_ref: str,
    variance_amount: Decimal | str | float,
    reason_code: str,
    sku_details: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Draft a legally binding GST Section 34 Statutory Debit Note / Commercial Claim.

    Adheres to Rule 53 of Central Goods and Services Tax Rules, 2017.
    """
    variance = Decimal(str(variance_amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    # Split into Base Taxable Value + GST (assuming standard 18% GST: 9% CGST + 9% SGST)
    base_taxable = (variance / Decimal("1.18")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total_tax = (variance - base_taxable).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    cgst = (total_tax / Decimal("2")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sgst = (total_tax - cgst).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    today_str = datetime.date.today().isoformat()
    note_id = f"DN-{datetime.date.today().year}-{abs(hash(f'{vendor_name}:{invoice_ref}:{variance}')) % 100000:05d}"

    legal_clauses = [
        "1. Statutory Basis: Issued pursuant to Section 34(1) of the Central Goods and Services Tax Act, 2017 and Rule 53 of the CGST Rules, 2017.",
        "2. Input Tax Credit (ITC) Adjustment: Recipient has adjusted/reversed corresponding ITC under Section 16(2) of the CGST Act.",
        "3. Commercial Settlement: The debit amount of ₹{} shall be offset against subsequent trade payables or recovered via direct bank wire.".format(variance),
        "4. Dispute Notice: Formal recovery notice served in accordance with the Indian Contract Act, 1872 Section 73 for breach of contract / delivery default."
    ]

    return {
        "debit_note_number": note_id,
        "date_of_issue": today_str,
        "recipient_details": {
            "legal_name": "Acme Global Technologies Ltd.",
            "gstin": "29AABCA1234F1Z8",
            "state_code": "29 - Karnataka",
        },
        "supplier_details": {
            "legal_name": vendor_name,
            "invoice_reference": invoice_ref,
            "po_reference": po_ref,
        },
        "reason_for_issuance": reason_code,
        "financial_breakdown": {
            "taxable_value": str(base_taxable),
            "cgst_rate_9pct": str(cgst),
            "sgst_rate_9pct": str(sgst),
            "igst_rate_18pct": "0.00",
            "total_debit_amount": str(variance),
        },
        "line_item_schedules": sku_details or [
            {
                "description": f"Short shipment / price variance on {po_ref}",
                "original_invoiced_amt": str(variance),
                "actual_accepted_amt": "0.00",
                "debited_value": str(variance),
            }
        ],
        "statutory_clauses": legal_clauses,
        "status": "ISSUED_FOR_RECOVERY",
    }


def inspect_running_balance_invariance(
    statement_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Audit running balance mathematical invariance across bank/ledger rows.

    Checks: Closing[t] == Closing[t-1] + Credit[t] - Debit[t].
    Pinpoints the exact row where arithmetic continuity fails (unrecorded bank fee, float delay).
    """
    if len(statement_rows) < 2:
        return {"invariant_holds": True, "audited_rows": len(statement_rows), "breaks": []}

    breaks = []
    prev_balance = None

    for idx, row in enumerate(statement_rows):
        try:
            closing = Decimal(str(row.get("closing_balance", row.get("balance", "0")))).quantize(Decimal("0.01"))
            cr = Decimal(str(row.get("credit", "0"))).quantize(Decimal("0.01"))
            dr = Decimal(str(row.get("debit", "0"))).quantize(Decimal("0.01"))
        except Exception:
            continue

        if prev_balance is not None:
            expected_closing = (prev_balance + cr - dr).quantize(Decimal("0.01"))
            delta = (closing - expected_closing).quantize(Decimal("0.01"))
            if abs(delta) > Decimal("0.001"):
                breaks.append({
                    "row_index": idx,
                    "date": str(row.get("date", "")),
                    "description": str(row.get("description", "")),
                    "previous_balance": str(prev_balance),
                    "credit": str(cr),
                    "debit": str(dr),
                    "expected_closing": str(expected_closing),
                    "actual_closing": str(closing),
                    "invariance_break_delta": str(delta),
                    "suspected_cause": (
                        "Unrecorded bank fee/interest debit" if delta < 0 else "Unrecorded float credit"
                    ),
                })
        prev_balance = closing

    return {
        "invariant_holds": len(breaks) == 0,
        "audited_rows": len(statement_rows),
        "breaks_count": len(breaks),
        "breaks": breaks,
        "sox_compliance": "PASS" if len(breaks) == 0 else "FAIL_INVARIANCE_BROKEN",
    }
