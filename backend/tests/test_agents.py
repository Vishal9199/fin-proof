"""Tests for autonomous AI agents and financial domain tools."""
import datetime
from decimal import Decimal
import pytest

from app.agents.tools import (
    solve_subset_sum,
    calculate_tax_withholding,
    evaluate_contract_terms,
    draft_statutory_debit_note,
    inspect_running_balance_invariance,
)
from app.agents.investigator import ForensicInvestigatorAgent
from app.agents.dispute_agent import DisputeRemediationAgent
from app.schemas import Transaction, TxnState


def test_solve_subset_sum():
    # 1 consolidated bank payment of 14,800 matches invoices: 5,000 + 6,800 + 3,000
    candidates = [
        {"id": "inv_1", "reference": "INV-101", "amount": "5000.00"},
        {"id": "inv_2", "reference": "INV-102", "amount": "6800.00"},
        {"id": "inv_3", "reference": "INV-103", "amount": "3000.00"},
        {"id": "inv_4", "reference": "INV-104", "amount": "9200.00"},
    ]
    res = solve_subset_sum(Decimal("14800.00"), candidates)
    assert res["matched"] is True
    assert res["matched_count"] == 3
    assert set(res["matched_ids"]) == {"inv_1", "inv_2", "inv_3"}
    assert res["residual_delta"] == "0.00"


def test_calculate_tax_withholding():
    # Contractor 2% corporate TDS under Section 194C
    res_corp = calculate_tax_withholding(Decimal("100000.00"), section="194C", is_corporate=True)
    assert res_corp["rate_percent"] == 2.0
    assert res_corp["tds_withheld"] == "2000.00"
    assert res_corp["net_payable"] == "98000.00"

    # Professional 10% TDS under Section 194J
    res_prof = calculate_tax_withholding(Decimal("50000.00"), section="194J")
    assert res_prof["rate_percent"] == 10.0
    assert res_prof["tds_withheld"] == "5000.00"
    assert res_prof["net_payable"] == "45000.00"


def test_evaluate_contract_terms():
    inv_date = datetime.date(2026, 5, 1)
    pay_date = datetime.date(2026, 5, 8)  # 7 days elapsed -> within 10 days
    res = evaluate_contract_terms(Decimal("50000.00"), inv_date, pay_date, "2/10 net 30")
    assert res["is_discount_eligible"] is True
    assert res["discount_amount"] == "1000.00"
    assert res["expected_settlement_amount"] == "49000.00"


def test_draft_statutory_debit_note():
    dn = draft_statutory_debit_note(
        vendor_name="Apex Logistics Ltd",
        invoice_ref="INV-8821",
        po_ref="PO-4412",
        variance_amount=Decimal("11800.00"),
        reason_code="Short shipment: 20 units missing",
    )
    assert dn["debit_note_number"].startswith("DN-")
    assert dn["financial_breakdown"]["total_debit_amount"] == "11800.00"
    assert dn["financial_breakdown"]["taxable_value"] == "10000.00"
    assert dn["financial_breakdown"]["cgst_rate_9pct"] == "900.00"
    assert dn["financial_breakdown"]["sgst_rate_9pct"] == "900.00"
    assert dn["status"] == "ISSUED_FOR_RECOVERY"


def test_inspect_running_balance_invariance():
    # Invariant rows
    rows_valid = [
        {"closing_balance": "10000.00", "credit": "10000.00", "debit": "0.00"},
        {"closing_balance": "8500.00", "credit": "0.00", "debit": "1500.00"},
        {"closing_balance": "12500.00", "credit": "4000.00", "debit": "0.00"},
    ]
    audit_ok = inspect_running_balance_invariance(rows_valid)
    assert audit_ok["invariant_holds"] is True
    assert audit_ok["breaks_count"] == 0

    # Invariant broken at row 2 (hidden 50 fee)
    rows_broken = [
        {"closing_balance": "10000.00", "credit": "10000.00", "debit": "0.00"},
        {"closing_balance": "8450.00", "credit": "0.00", "debit": "1500.00"}, # 10000 - 1500 = 8500 != 8450
    ]
    audit_broken = inspect_running_balance_invariance(rows_broken)
    assert audit_broken["invariant_holds"] is False
    assert audit_broken["breaks_count"] == 1
    assert audit_broken["breaks"][0]["invariance_break_delta"] == "-50.00"


def test_forensic_investigator_and_dispute_agent():
    txn = Transaction(
        id="txn_q1",
        source_doc="INV-990.pdf",
        source_type="tax_invoice",
        merchant="Apex Logistics Ltd",
        amount=Decimal("10000.00"),
        txn_date=datetime.date(2026, 5, 1),
        state=TxnState.QUARANTINE,
        quarantine_reason="Short shipment detected on line items",
        discrepancy={"what": "Physical shortfall of 20 units", "delta": "2000.00"},
    )
    investigator = ForensicInvestigatorAgent()
    inv = investigator.investigate(txn, [txn])
    assert inv.investigation_id.startswith("INV-FORENSIC-")
    assert inv.status == "DISPUTE_RECOVERY_TRIGGERED"
    assert len(inv.reasoning_steps) >= 2

    # Hand off to dispute agent
    dispute_agent = DisputeRemediationAgent()
    inv_remediated = dispute_agent.remediate(inv, invoice_ref="INV-990", po_ref="PO-551")
    assert inv_remediated.debit_note_draft is not None
    assert inv_remediated.debit_note_draft["financial_breakdown"]["total_debit_amount"] == "2000.00"
    assert "FORMAL NOTICE OF COMMERCIAL DISPUTE" in inv_remediated.debit_note_draft["recovery_notice_letter"]


def test_investigator_batch_settlement():
    q_txn = Transaction(
        id="txn_batch_break",
        source_doc="Bank_Wire.csv",
        source_type="bank_csv",
        merchant="Stripe Payments",
        amount=Decimal("15000.00"),
        txn_date=datetime.date(2026, 5, 2),
        state=TxnState.QUARANTINE,
        quarantine_reason="Consolidated lump sum payment — open invoices mismatch",
        discrepancy={"what": "Wire lump sum does not match single invoice", "delta": "15000.00"},
    )
    inv1 = Transaction(
        id="inv_1",
        source_doc="Inv_1.pdf",
        source_type="tax_invoice",
        merchant="Stripe Payments",
        amount=Decimal("10000.00"),
        txn_date=datetime.date(2026, 5, 1),
    )
    inv2 = Transaction(
        id="inv_2",
        source_doc="Inv_2.pdf",
        source_type="tax_invoice",
        merchant="Stripe Payments",
        amount=Decimal("5000.00"),
        txn_date=datetime.date(2026, 5, 1),
    )

    investigator = ForensicInvestigatorAgent()
    inv = investigator.investigate(q_txn, [q_txn, inv1, inv2])

    assert inv.break_id == "txn_batch_break"
    assert inv.status == "RESOLVED_AUTONOMOUSLY"
    assert "Consolidated Batch Settlement" in (inv.proven_root_cause or "")
    assert len(inv.reasoning_steps) >= 3
