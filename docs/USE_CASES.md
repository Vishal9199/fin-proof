# FinProof — Use Cases & Target Personas

FinProof is an autonomous reconciliation engine engineered for high-stakes, regulated financial environments where financial numbers must be provable and unproven numbers are quarantined rather than guessed.

---

## 1. Target Audience: Who Is This For?

While basic expense demos often feature personal receipts, FinProof's architecture solves friction across four major institutional segments:

| Target Segment | Specific Teams & Personas | Concrete Problem Solved |
|---|---|---|
| **1. Commercial & Retail Banks** | • Settlement & Recon Desk<br/>• Nostro / Vostro Reconciler<br/>• Clearing Desk Operations | Reconciling internal Core Banking Systems (CBS) against payment switch logs (UPI / NPCI / SWIFT MT940), merchant settlement batches, and interchange fee deductions. Eliminates daily manual break-item queues. |
| **2. SME & Commercial Lending** | • Credit Underwriting Lead<br/>• Risk & Fraud Analyst<br/>• Forensic Accountant | Cross-examining borrower-submitted bank PDF statements against GSTR-2A/3B tax filings and vendor invoices to detect fabricated statements, revenue inflation, and circular trading. |
| **3. Enterprise CFO Suite & Treasury** | • Accounts Payable (AP) Lead<br/>• Financial Controller<br/>• Internal / SOX 404 Auditor | Autonomous 3-Way & 4-Way Matching: Purchase Orders (PO) vs. Delivery Challans / Goods Received Notes (GRN) vs. Vendor Tax Invoices vs. Bank Clearing Feeds. |
| **4. FinTechs & Payment Gateways** | • Merchant Operations Lead<br/>• Chargeback & Dispute Specialist | Reconciling merchant POS terminal batch slips, customer UPI mobile screenshots, gateway settlement APIs, and bank credit notices. |

---

## 2. Deterministic vs. Non-Deterministic Agents

Financial systems cannot be built purely on LLMs (which hallucinate numbers and fail arithmetic) nor purely on rigid code (which crashes on layout changes, faded receipts, or schema drift). FinProof enforces an architectural contract:

> **"The LLM proposes; deterministic code disposes."**

```
┌─────────────────────────────────┬────────────────────────────────────────────────────────┐
│ Dimension                       │ Deterministic Layer                                    │ Non-Deterministic (LLM / Agentic) Layer│
├─────────────────────────────────┼────────────────────────────────────────────────────────┼────────────────────────────────────────┤
│ Nature                          │ Mathematical, rule-based, 100% reproducible, 0 jitter │ Probabilistic, semantic, perceptual, multi-modal│
├─────────────────────────────────┼────────────────────────────────────────────────────────┼────────────────────────────────────────┤
│ Core Roles                      │ Schema firewalls, arithmetic invariance, state guards │ Messy perception, OCR, semantic reasoning, advisor│
├─────────────────────────────────┼────────────────────────────────────────────────────────┼────────────────────────────────────────┤
│ In FinProof                     │ • Pandera Schema Contracts (CSV validation & types)   │ • Multi-modal Vision Worker (receipts & screens)│
│                                 │ • Pydantic Canonical Model (`Decimal` money types)    │ • Semantic Merchant Normalizer                 │
│                                 │ • LangGraph State Machine (strict conditional guards) │ • Dual-Read Self-Consistency Verifier          │
│                                 │ • RapidFuzz String Distance algorithms                │ • AI Quarantine Advisor (remedy generation)    │
│                                 │ • Regex-based PII Redaction at egress boundary        │ • AI Ledger Chatbot (natural language queries) │
│                                 │ • Cryptographic ledger invariance (no self-approval)  │ • Document classification (financial triage)   │
└─────────────────────────────────┴────────────────────────────────────────────────────────┴────────────────────────────────────────┘
```

---

## 3. Manual Intervention (Human-in-the-Loop Architecture)

When automated checks find ambiguity or discrepancies, FinProof isolates the transaction in the **Quarantine Lane**.

### A. WHAT is missing or not matching?
1. **Cross-Source Amount Conflict:** The paper receipt reads ₹450, but the bank statement debit reads ₹540 (e.g. service charge, tip, or OCR digit confusion `540` vs `450`).
2. **Missing Transaction Pair / Orphan Leg:** A bank debit of ₹1,50,000 exists without any matching vendor invoice or purchase order in the batch.
3. **Quantity or Line-Item Discrepancy:** Purchase Order ordered 50 units; Goods Received Note (GRN) received 48 units; Vendor Invoice billed for 50 units.
4. **Low Confidence / Ambiguous OCR:** Faded thermal paper or tilted smartphone capture yields an extraction confidence of 55% (< 80% safety gate).
5. **Schema Drift / Header Renaming:** Bank export changes `"Date"` to `"Transaction Date"`, or date format shifts from `DD/MM/YYYY` to `MM/DD/YYYY`.
6. **Arithmetic / Tax Invariance Failure:** Line-item prices do not sum to invoice total, or calculated GST (18%) differs from printed subtotal.

### B. WHERE is it surfaced?
* **Dedicated Quarantine Lane:** Visually and logically separated from the clean `POSTED` ledger to prevent ledger pollution.
* **Side-by-Side Visual Diff Canvas:**
  * **Left Side:** Source document image / text with bounding box highlight over the conflicting amount.
  * **Right Side:** Corresponding bank statement line or ERP purchase order.
  * **Delta Badge:** `⚠️ Conflict: Δ = ₹90.00 (+20%) | RapidFuzz similarity: 94%`.
* **AgentOps Audit Drawer:** Complete execution span, showing the exact extraction prompts, raw model responses, and confidence breakdown.

### C. HOW does the user resolve it?
1. **✨ AI Quarantine Advisor:** One click triggers the advisor agent, which analyzes the discrepancy and proposes 2–3 structured actions:
   * *"Option A (Recommended): Use Bank Statement ₹540 (Authoritative clearing record; difference identified as 20% gratuity)."*
   * *"Option B: Use Receipt ₹450 (Quarantine ₹90 delta as unauthorized charge for merchant dispute)."*
   * *"Option C: Dismiss / Reject."*
2. **One-Click State Transition:** Selecting an option immediately transitions the record from `QUARANTINE` to `POSTED` (or `REJECTED`) and recalculates totals.
3. **Audit Trail Signature:** Every resolution logs `User_ID`, `Timestamp`, `Selected_Action`, and `Reason` into an immutable audit event stream.
4. **Inline Field Correction:** If necessary, human reviewers can manually correct fields with real-time Pydantic validation before submission.
5. **Adaptive Rule Synthesis:** Users can select *"Always apply this resolution for merchant BREW & CO under ₹1,000"*, creating a deterministic rule for future runs.

---

## 4. Multi-Agent Swarm Architecture

The pipeline uses six specialized agents coordinated by a LangGraph state machine:

1. **Ingestion & Security Router Agent:** Inspects binary magic bytes (MIME triage) and applies deterministic regex PII redaction before model egress.
2. **Parallel Extraction Worker Swarm (Fan-Out):**
   * *Vision Worker:* Multi-modal perception of camera photos and mobile app screenshots.
   * *PDF Worker:* Text-layer probe distinguishing born-digital statement tables from scanned PDFs.
   * *CSV / Tabular Worker:* Pandera schema validation with automated column drift self-healing.
3. **Self-Verification Agent:** Runs dual-read verification and per-field confidence checks; blocks low-confidence reads.
4. **Graph Reconciliation & Matching Agent (Fan-In):** Applies fuzzy entity matching, sliding temporal windows, and exact monetary matching to collapse duplicates and quarantine anomalies.
5. **AI Quarantine Advisor Agent:** Synthesizes conflict evidence and generates structured resolution payloads for human reviewers.
6. **Conversational Ledger & Analytics Agent:** Translates natural language queries into ledger calculations and animated CSS charts.

---

## 5. Concrete Real-World Use Cases

### Use Case 1 — Enterprise 3-Way Matching (Accounts Payable Automation)

* **Who:** Accounts Payable (AP) Team and Corporate Financial Controller.
* **Input Files (`sample_data/enterprise/`):**
  - `purchase_order_po_8841.txt`: PO-8841 for 50 UltraBooks @ ₹85,000 + 50 Docks @ ₹8,000 = ₹54,87,000 (inc. 18% IGST).
  - `goods_received_note_grn_320.txt`: GRN-0320 warehouse receipt showing 48 UltraBooks received (2 short-shipped).
  - `vendor_tax_invoice_inv_992.txt`: Vendor Invoice INV-992 billing for the full 50 UltraBooks (₹54,87,000).
  - `corporate_bank_feed.csv`: Bank debit of ₹52,86,460 reflecting adjusted payment (48 units + 2% prompt discount).
* **Expected Output:**
  | Document / Transaction | State | Reason / Evidence |
  |---|---|---|
  | INV-992 vs GRN-0320 | 🟠 QUARANTINE | Quantity Discrepancy: Billed 50 units vs Received 48 units (Overbilled by ₹2,00,600 inc. tax) |
  | Bank Debit ₹52,86,460 | 🟠 QUARANTINE | Cross-source amount conflict against Invoice INV-992 |
  | AI Quarantine Suggestion | ✨ ADVISORY | Explains short-shipment adjustment + 2% cash discount; offers *"Approve adjusted debit ₹52,86,460 & issue debit note for short shipment"* |

---

### Use Case 2 — Commercial Lending Underwriting & Fraud Detection

* **Who:** SME Credit Underwriter and Bank Risk Analyst.
* **The Problem:** Borrower submits PDF bank statements and GST tax filings to support a ₹5 Crore credit facility. Manual review takes 4 days and fails to detect round-tripping or edited PDF balances.
* **Input Files:**
  - 12-month Bank Statement PDF (scanned / digital).
  - GSTR-2B & GSTR-3B tax returns.
  - Top 10 customer sales invoices.
* **Expected Output:**
  - **Forensic PDF Inspection:** Validates font metadata and detects modified text layers.
  - **Cross-Reconciliation:** Matches total bank deposits against declared GSTR-3B monthly turnover.
  - **Anomaly Flag:** Quarantines circular transactions between related entities sharing identical GSTIN prefix or directors.

---

### Use Case 3 — Bank Settlement & Nostro / Vostro Reconciliation

* **Who:** Bank Treasury Operations & Settlement Desk.
* **The Problem:** Reconciling SWIFT MT940 daily international wire statements against domestic core banking transactions. Cryptic sender narrations, FX rate variances, and intermediary bank fee deductions cause manual settlement breaks.
* **Expected Output:**
  - Fuzzy matcher associates transactions across differing time zones and currency conversions.
  - Predictable fee deductions (e.g. $25 SWIFT intermediary fee) are automatically classified.
  - Unmatched payments route to Quarantine with automated payment trace inquiry generation.

---

### Use Case 4 — Monthly Personal / Small Business Expense Reconciliation

* **Who:** Small business owner or individual tracking expenses across cards, cash, and UPI.
* **Input Files:**
  - `sample_data/receipts/brew_co_receipt.txt`: Paper receipt (₹450).
  - `sample_data/bank_statement.csv`: Bank CSV with BREW & CO line (₹540).
  - `sample_data/upi/upi_swiggy.txt`: UPI screenshot (₹320).
* **Expected Output:**
  | Transaction | State | Reason |
  |---|---|---|
  | BREW & CO ₹450 | 🟠 QUARANTINE | Cross-source amount conflict |
  | BREW & CO ₹540 | 🟠 QUARANTINE | Cross-source amount conflict |
  | SWIGGY ₹320 | ✅ POSTED | Matched across UPI + bank CSV, amounts agree |
  | Link: BREW & CO | ANOMALY | Receipt ₹450 vs statement ₹540 — needs human review |
  | Link: SWIGGY | DUPLICATE | Collapsed to single posted entry |

---

### Use Case 5 — Schema-Drift Recovery (Pandera Self-Healing Firewall)

* **Who:** Finance operations team whose banking partner changes export format without notice.
* **Input Files:** `sample_data/bank_statement_drifted.csv` (renamed headers `"Transaction Date"`, `"Description"`).
* **Expected Output:**
  - 🟡 Schema drift detected banner displayed on dashboard.
  - RapidFuzz column mapper dynamically maps `"Transaction Date"` $\to$ `"Date"`.
  - Unmapped columns or malformed rows quarantined with descriptive error logs; valid rows proceed to `POSTED`.

---

### Use Case 6 — Faded / Low-Legibility Document Handling

* **Who:** Any user scanning thermal receipts, folded slips, or skewed mobile captures.
* **Input Files:** `sample_data/receipts/faded_receipt.txt` (simulates low-legibility thermal receipt).
* **Expected Output:**
  - Dual-read extraction detects disagreement between primary read and perturbation read.
  - Entry quarantined with reason: `Confidence 55% (< 80% gate) — digit ambiguity detected`.
  - Zero guess policy: system refuses to fabricate numbers into the ledger.

---

## 6. What "Good Output" Looks Like (Golden Benchmark)

Running the baseline 6 sample files produces the deterministic benchmark:

| Metric | Expected Golden Value | Verification Command |
|---|---|---|
| Documents Processed | 6 | `python -m scripts.run_local` |
| Transactions POSTED | 5 | Verified in Posted Ledger |
| Transactions QUARANTINED | 3 | Preserved in Quarantine Lane |
| Total Posted Amount | ₹2,430.00 | Exact Decimal arithmetic |
| Cross-Source Links Found | 3 (2 duplicates + 1 anomaly) | RapidFuzz graph matcher |
| Quarantine Recall (Safety Gate) | **1.000** | 100% unsafe items quarantined |
| Faithfulness Score | **1.00** | Every claim verified by source evidence |
