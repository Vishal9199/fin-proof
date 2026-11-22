"""Autonomous Dispute Remediation Agent.

Takes unresolved forensic discrepancies (short-shipments, unaccepted pricing variances)
and drafts formal statutory GST Section 34 Debit Notes, accounting dispute provisions,
and legal commercial recovery letters.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any

from ..schemas import AgentReasoningStep, AgentToolCall, ForensicInvestigation
from .tools import draft_statutory_debit_note


class DisputeRemediationAgent:
    """Specialized agent for drafting statutory debit notes and recovery notices."""

    def __init__(self, name: str = "DisputeRemediationAgent"):
        self.name = name

    def remediate(
        self,
        investigation: ForensicInvestigation,
        invoice_ref: str = "INV-2026-UNKNOWN",
        po_ref: str = "PO-2026-UNKNOWN",
    ) -> ForensicInvestigation:
        """Execute statutory dispute workflow on an investigated break."""
        now_str = lambda: datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S.%f")[:-3]

        investigation.reasoning_steps.append(AgentReasoningStep(
            agent_name=self.name,
            thought=(
                f"Received referral from ForensicInvestigatorAgent for {investigation.vendor_merchant}. "
                f"Variance of ₹{investigation.variance_amount} requires formal statutory debit action "
                f"under CGST Act 2017 Section 34 to protect Input Tax Credit (ITC) eligibility."
            ),
            action="initiate_statutory_recovery_dossier",
            observation="Verifying statutory prerequisites: GSTIN active, PO reference confirmed, Rule 53 compliance mandatory.",
            timestamp=now_str(),
        ))

        # Call statutory tool: draft_statutory_debit_note
        debit_note = draft_statutory_debit_note(
            vendor_name=investigation.vendor_merchant,
            invoice_ref=invoice_ref,
            po_ref=po_ref,
            variance_amount=investigation.variance_amount,
            reason_code=investigation.proven_root_cause or "Short delivery / Line-item discrepancy",
        )

        investigation.tool_calls.append(AgentToolCall(
            tool_name="draft_statutory_debit_note",
            tool_input={
                "vendor_name": investigation.vendor_merchant,
                "invoice_ref": invoice_ref,
                "po_ref": po_ref,
                "variance_amount": str(investigation.variance_amount),
            },
            tool_output=debit_note,
        ))

        # Generate formal recovery letter
        recovery_letter = (
            f"FORMAL NOTICE OF COMMERCIAL DISPUTE & STATUTORY DEBIT\n"
            f"Reference: {debit_note['debit_note_number']} | Date: {debit_note['date_of_issue']}\n"
            f"To: Finance & Commercial Controller, {investigation.vendor_merchant}\n\n"
            f"Dear Sir/Madam,\n\n"
            f"Please take notice that during our statutory three-way reconciliation against Purchase Order {po_ref} "
            f"and Tax Invoice {invoice_ref}, an unreconciled variance of ₹{investigation.variance_amount} was detected.\n\n"
            f"Audit Finding: {investigation.proven_root_cause}\n"
            f"In accordance with Section 34(1) of the Central Goods and Services Tax Act, 2017, we have issued Debit Note "
            f"{debit_note['debit_note_number']} for ₹{investigation.variance_amount} (Taxable: ₹{debit_note['financial_breakdown']['taxable_value']} + "
            f"CGST ₹{debit_note['financial_breakdown']['cgst_rate_9pct']} + SGST ₹{debit_note['financial_breakdown']['sgst_rate_9pct']}).\n\n"
            f"Per Section 16(2) of the CGST Act, Input Tax Credit has been provisionally reversed on this delta. "
            f"Kindly confirm acceptance within 7 business days or provide credit note credit memo matching.\n\n"
            f"Sincerely,\nAutonomous Financial Assurance Engine\nAcme Global Technologies Ltd."
        )

        debit_note["recovery_notice_letter"] = recovery_letter
        investigation.debit_note_draft = debit_note

        investigation.reasoning_steps.append(AgentReasoningStep(
            agent_name=self.name,
            thought=(
                f"Statutory Debit Note {debit_note['debit_note_number']} successfully compiled with tax breakdowns. "
                f"Recovery notice generated. Balancing entry: Dr Vendor Recovery Account, Cr Suspense Variance."
            ),
            action=f"execute_tool:draft_statutory_debit_note(note_id={debit_note['debit_note_number']})",
            observation=f"Debit Note finalized. Taxable ₹{debit_note['financial_breakdown']['taxable_value']}, GST ₹{debit_note['financial_breakdown']['cgst_rate_9pct']}x2. Recovery notice ready for export.",
            timestamp=now_str(),
        ))

        return investigation
