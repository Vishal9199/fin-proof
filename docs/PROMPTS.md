# FinProof — Prompt Engineering Documentation

This document explains every prompt used by the extraction pipeline, **why** it
was designed the way it is, and the self-consistency technique that turns two
cheap calls into a confidence signal without a labelled dataset.

---

## The Core Technique — Two-Read Self-Consistency

Instead of asking the model once and trusting the answer, FinProof asks **twice**:

1. **Read 1 (full extraction):** extract all fields (merchant, amount, date)
2. **Read 2 (amount-only recheck):** re-read *only* the amount independently

The two reads are **compared deterministically by code** (not by the model):

```
|read1_amount - read2_amount| ≤ 0.01  →  confidence = 0.97  (VERIFIED → POSTED)
|read1_amount - read2_amount| > 0.01  →  confidence = 0.55  (QUARANTINE)
```

This catches the classic OCR/vision failure: the model silently misreads `₹450`
as `₹480`. One read might be wrong; both reads making the *same* error on an
independent call is far less likely. The guardrail is deterministic — no amount
reaches POSTED unless the model agrees with itself.

---

## Prompt 1 — Text Receipt Extraction (`_EXTRACT_PROMPT`)

```
Extract this receipt as JSON with keys merchant (string),
amount (number, the final total paid), date (YYYY-MM-DD).
Return ONLY JSON.

<receipt text>
```

**Why this design:**
- **Schema-constrained output** — specifying exact JSON keys forces structured output
  that the deterministic parser can validate. Free-form responses are rejected.
- **"final total paid"** — explicitly disambiguates from subtotals, taxes, and
  per-item amounts. Early versions without this phrase returned line-item amounts.
- **"Return ONLY JSON"** — prevents the model adding explanatory prose that breaks
  the JSON parser. Critical for reliability.
- **YYYY-MM-DD** — ISO format avoids locale-specific date parsing bugs (DD/MM vs
  MM/DD ambiguity).

**What was tried and rejected:**
- Asking for currency separately → unnecessary complexity; ₹ is inferred from context
- Including item-level extraction → too verbose; the pipeline only needs the total
- JSON schema in the prompt → model compliance was worse than plain-English keys

---

## Prompt 2 — Text Receipt Amount Recheck (`_RECHECK_PROMPT`)

```
Return only the final total amount as a number.

<receipt text>
```

**Why this design:**
- Deliberately minimal — a long prompt biases the second read toward the first
  read's answer (the model "remembers" what it said). Keeping it simple maximises
  independence.
- Single scalar output — no JSON, just a number. Easier to parse, harder to
  hallucinate structure around.

---

## Prompt 3 — Vision/Image Extraction (`_VISUAL_EXTRACT_PROMPT`)

```
You are reading a financial document (photo, screenshot, or PDF).
Classify it and extract as JSON with keys:
kind ('receipt' | 'upi_payment' | 'bank_statement' | 'other'),
merchant (string — the merchant/payee; for UPI payments the recipient),
amount (number, the final total paid), date (YYYY-MM-DD).
If this is NOT a financial transaction document, set kind to 'other'.
Return ONLY JSON.
```

**Why this design:**
- **Document classification first (`kind`)** — a random photo (a selfie, a map
  screenshot) must be classified as `'other'` before any extraction. The pipeline
  sets confidence = 0.0 for `kind='other'`, which quarantines it with a clear reason
  instead of hallucinating a transaction (guardrail F10).
- **"for UPI payments the recipient"** — UPI screenshots list a person/UPI ID as the
  payee, not a merchant name. This clause prevents `merchant = null` for UPI inputs.
- Same `Return ONLY JSON` discipline as Prompt 1.

**Iteration history:**
- v1: no classification field → random images produced hallucinated transactions
- v2: added `kind` field with binary financial/non-financial → too coarse; bank
  statements and receipts need different downstream handling
- v3 (current): four-way classification drives routing logic, not just quarantine

---

## Prompt 4 — Vision Amount Recheck (`_VISUAL_RECHECK_PROMPT`)

```
Return ONLY the final total amount paid in this document, as a plain number.
If no amount is present, return 0.
```

**Why this design:**
- "If no amount is present, return 0" — handles non-financial images where the
  model saw `kind='other'` in read 1. Returning 0 gives a clean numeric parse
  and the confidence check (|read1 - 0| will be large) confirms low confidence.

---

## Prompt 5 — Bank Statement Row Extraction (`_STATEMENT_PROMPT`)

```
This document is a bank/card statement. Extract EVERY transaction row, across
all pages, as a JSON array of objects with keys: date (YYYY-MM-DD),
description (string), amount (number — the absolute transaction amount).
Exclude opening/closing balance lines. Return ONLY the JSON array.
```

**Why this design:**
- **"across all pages"** — without this, multi-page PDFs are truncated at page 1.
- **"the absolute transaction amount"** — bank statements show negative numbers for
  debits. The pipeline handles sign internally; asking for absolute values prevents
  the model returning -₹200 for a debit.
- **"Exclude opening/closing balance lines"** — balance rows look like transactions
  but aren't. Without this exclusion they appear as phantom transactions.
- **Uses `deep_model`** — bank statements are the most token-intensive document type.
  The deep (more capable) model is used for correctness-critical extraction.

---

## Prompt 6 — Bank Statement Amount Recheck (`_STATEMENT_RECHECK_PROMPT`)

```
Re-read ONLY the transaction amounts in this bank/card statement, across all
pages, in document order (exclude opening/closing balance lines).
Return ONLY a JSON array of plain numbers.
```

**Why this design:**
- **Element-wise comparison** — Read 2 returns an array of just amounts in document
  order. Code compares `read1[i].amount` vs `read2[i]` for every row individually.
  A single suspicious row quarantines alone; the rest still post.
- **"in document order"** — critical for the index alignment. If the model skips a
  row in read 2, every subsequent index is misaligned and the whole statement gets
  low confidence (F11 guardrail).
- **Row-count mismatch handling** — if `len(read2) != len(read1)`, all rows collapse
  to 0.55 confidence. Better to quarantine the whole statement than silently
  misalign amounts.

---

## Summary Table

| # | Prompt | Model tier | Output type | Self-consistency role |
|---|---|---|---|---|
| 1 | Receipt text extraction | fast | JSON object | Read 1 (full) |
| 2 | Receipt amount recheck | fast | plain number | Read 2 (amount only) |
| 3 | Vision extraction + classify | fast | JSON object | Read 1 (full) |
| 4 | Vision amount recheck | fast | plain number | Read 2 (amount only) |
| 5 | Statement row extraction | **deep** | JSON array | Read 1 (all rows) |
| 6 | Statement amount recheck | **deep** | number array | Read 2 (amounts only) |
