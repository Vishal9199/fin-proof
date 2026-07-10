# FinProof — Use Cases

Three concrete scenarios this system is built to solve, each with sample input
and the expected output the pipeline produces.

---

## Use Case 1 — Monthly Expense Reconciliation

**Who:** An individual tracking personal spending across multiple payment methods.

**The problem:** The same coffee purchase appears in both a UPI screenshot and a
bank statement. OCR reads the receipt as ₹450; the bank statement shows ₹540 for
the same merchant on the same day. Without a system, one of three things happens:
the duplicate is counted twice, the discrepancy is missed, or hours are spent
manually cross-referencing.

**Input files:**
- `sample_data/receipts/brew_co_receipt.txt` — paper receipt photo (₹450)
- `sample_data/bank_statement.csv` — bank CSV with a BREW & CO line (₹540)
- `sample_data/upi/upi_swiggy.txt` — UPI screenshot for a different transaction

**Expected output:**
| Transaction | State | Reason |
|---|---|---|
| BREW & CO ₹450 | 🟠 QUARANTINE | Cross-source amount conflict |
| BREW & CO ₹540 | 🟠 QUARANTINE | Cross-source amount conflict |
| SWIGGY ₹320 | ✅ POSTED | Matched across UPI + bank CSV, amounts agree |
| Link: BREW & CO | ANOMALY | Receipt ₹450 vs statement ₹540 — needs human review |
| Link: SWIGGY | DUPLICATE | Collapsed to one entry |

**What this demonstrates:** cross-source reconciliation, anomaly detection, and
the quarantine-over-post-when-uncertain principle.

---

## Use Case 2 — Schema-Drift Recovery

**Who:** A finance team whose bank sends CSV exports with renamed column headers
after a system upgrade, breaking every downstream script silently.

**The problem:** The bank renames `"Date"` to `"Transaction Date"` and `"Narration"`
to `"Description"`. A naive script crashes or reads the wrong columns. FinProof's
Pandera schema contract detects the drift, attempts fuzzy header recovery, and
either self-heals the mapping or quarantines the affected rows — it never silently
processes a wrong column.

**Input files:**
- `sample_data/bank_statement_drifted.csv` — same data, renamed headers

**Expected output:**
- 🟡 Schema drift detected (orange banner in dashboard)
- Headers remapped automatically: confidence shown for each mapping
- Rows that could not be reliably mapped → QUARANTINE with reason
- Valid rows continue to POSTED as normal

**What this demonstrates:** the Pandera schema-drift firewall (guardrail F5),
self-healing, and graceful degradation without crashing.

---

## Use Case 3 — Low-Quality / Faded Document Handling

**Who:** Anyone scanning old paper receipts or photographing them in bad lighting.

**The problem:** A faded receipt is uploaded. The vision model can read most of
it but is uncertain about the amount (is it ₹180 or ₹180.00 or ₹1800?). A
naive system would post its best guess. FinProof's two-read self-consistency
check detects the disagreement and quarantines the entry with a human-readable
reason instead of posting an uncertain number.

**Input files:**
- `sample_data/receipts/faded_receipt.txt` — simulates a low-legibility receipt

**Expected output:**
| Transaction | State | Reason |
|---|---|---|
| CAFE ZEST ₹360 | 🟠 QUARANTINE | Confidence 55% — below threshold (80%) |

**What this demonstrates:** the self-consistency guardrail (F1), the confidence
threshold gate, and the principle that **uncertain → quarantine, never → guess**.

---

## What "Good Output" Looks Like (Golden Run)

Running all 6 sample files together produces the deterministic mock-mode result:

| Metric | Expected value |
|---|---|
| Documents processed | 6 |
| Transactions POSTED | 5 |
| Transactions QUARANTINED | 3 |
| Total posted amount | ₹2,430.00 |
| Cross-source links found | 3 (2 duplicates + 1 anomaly) |
| Quarantine recall | 1.000 (all unsafe entries caught) |
| Faithfulness score | 1.00 (every claim backed by evidence) |

Run `python -m evals.run` in `backend/` to verify these numbers automatically.
