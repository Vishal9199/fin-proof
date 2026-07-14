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

## Use Case 4 — AI Quarantine Advisor (One-Click Resolution)

**Who:** A finance reviewer who receives a pile of quarantined transactions after
reconciliation and wants AI-guided help resolving each one efficiently.

**The problem:** A transaction (e.g. `BREW & CO`) is quarantined due to a
cross-source amount conflict — the receipt says ₹450 but the bank statement says
₹540. The reviewer needs to decide which amount is correct and resolve the entry
without manually editing a ledger.

**Interaction flow:**
1. Open the FinProof dashboard after a reconciliation run.
2. Locate the quarantined `BREW & CO` card in the Quarantine lane.
3. Click **✨ AI Suggest** — the panel expands with an AI-generated explanation and
   action buttons.
4. The AI explains: *"Amount mismatch between receipt (₹450) and bank statement
   (₹540). Bank statements are typically authoritative for posted amounts."*
5. Click **"Use Statement Amount ₹540"** — the card moves to the Posted ledger,
   and `total_posted_amount` is updated immediately.

**Expected output:**
| Action | Effect |
|---|---|
| `GET /runs/{id}/quarantine/{txn_id}/suggest` | Returns `{explanation, actions[]}` |
| `POST /runs/{id}/quarantine/{txn_id}/resolve` with `override` | Transaction state → POSTED; total amount recalculated |
| `POST /runs/{id}/quarantine/{txn_id}/resolve` with `reject` | Transaction dismissed from ledger |

**What this demonstrates:** human-in-the-loop AI advisory, structured action
suggestion, and the principle that the AI recommends but the human decides.

---

## Use Case 5 — AI Ledger Chat with Inline Bar Charts

**Who:** A user who wants instant, conversational insight into their reconciled
ledger without exporting to a spreadsheet or writing queries.

**The problem:** After a reconciliation run, the user wants to know "How much did
I spend on Food this month?" or "What are my top spending categories?" — without
leaving the dashboard or manually summing rows.

**Interaction flow:**
1. After a run completes, scroll to the **AI Chat** panel at the bottom.
2. Click the 📊 **Category Breakdown** chip (or type *"Show spending by category"*).
3. The assistant responds with a natural-language summary *and* renders an animated
   CSS bar chart inline showing category totals side by side.
4. Click 💸 **Total Spend** to see the aggregate posted amount, or ask a free-text
   question like *"Why is Swiggy ₹320 posted?"*

**Expected output:**
```
AI: Here is the spending breakdown for your current run:

  SPENDING BREAKDOWN
  Food & Drink  ₹320  ████████
  Transport     ₹200  ██████
  Groceries     ₹450  ████████████
  Other         ₹750  ████████████████████
```

**What this demonstrates:** the conversational ledger query engine, structured
chart data generation, and CSS-native chart rendering (no third-party library).

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
