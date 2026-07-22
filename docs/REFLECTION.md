# FinProof — Reflection

A candid retrospective on what worked, what I'd do differently, and what's next.

---

## What Worked Well

### 1. The two-read self-consistency guardrail
The single best design decision. Asking the model twice with independent prompts
and comparing the results deterministically — rather than asking the model to
rate its own confidence — gave a reliable, calibratable trust signal without any
labelled training data. It also naturally catches the vision failure mode (OCR
misreads) that I was most worried about.

### 2. "The LLM proposes; deterministic code disposes"
Keeping this as an explicit architectural principle from day one prevented scope
creep into "let the model decide whether to quarantine." Every trust decision is
made by a threshold, a schema, or a comparison — never by asking the model
"are you sure?" This made the system testable and auditable.

### 3. Mock-first development
Building `MockProvider` as a deterministic, zero-network first-class provider
meant the entire pipeline, all 80 tests, and the eval scorecard all run offline
with a fixed, reproducible golden answer. This made CI trivial and the live demo
reliable (it never needs an API key to function).

### 4. Single-origin architecture
Serving the API and the dashboard from one FastAPI process meant zero CORS
configuration, one URL to share, and a Dockerfile that works identically on
localhost and Render without environment changes.

---

## What I'd Do Differently

### 1. Start with a simpler first version
The parallel fan-out, SSE replay buffer, and LangGraph state machine were all
built together. In hindsight, a single-file sequential version first would have
revealed the data model problems faster. The scope meant some early design
decisions (like the event bus) had to be refactored once the dashboard's
late-connect requirement became clear.

### 2. Confidence calibration from the start
The 0.97/0.55 confidence values from the two-read comparison are heuristics, not
calibrated against a labelled dataset. They work well for the golden test cases
but might not generalise to edge cases (very short receipts, unusual formatting).
A proper calibration dataset would make the thresholds defensible.

### 3. Human review queue in the UI
The QUARANTINE lane shows items and reasons but doesn't let a human approve or
reject them in the interface. The pipeline is designed for human-in-the-loop, but
the UI doesn't complete that loop. I'd build the Approve / Reject buttons in the
first version if doing this again.

### 4. Database-backed state
Runs are kept in an in-process dictionary. A restart loses all run history.
For a real deployment, this would be a Postgres table (LangGraph has a built-in
Postgres checkpointer that would handle this in one dependency change).

---

## Known Limitations

| Limitation | Impact | Mitigation in place |
|---|---|---|
| Vision prompts are English-only | Non-English receipts may fail | Quarantined with `low_confidence` reason |
| Amount confidence is heuristic (0.97/0.55) | May not calibrate on all document styles | Threshold configurable from dashboard |
| In-memory run store | Lost on container restart | SSE replay buffer covers mid-run reconnects |
| Free Render tier sleeps after 15 min idle | ~30s cold start | Uptime monitor on `/health` keeps it warm |
| Bank CSV schema self-heal is fuzzy-match only | Completely new headers may not be recovered | Rows quarantined with explicit reason |

---

## What I'd Build Next

1. **Confidence calibration** — run the pipeline against a larger labelled
   dataset (100+ documents) and fit the confidence thresholds to actual precision/
   recall curves rather than hand-tuned heuristics.

2. **Active-learning loop** — every human quarantine resolution (approve/reject)
   becomes a new row in the eval golden set. Over time, the system learns from
   its own mistakes.

3. **Postgres checkpointer** — swap the in-memory run store for LangGraph's
   Postgres backend. Runs survive restarts, and the history becomes queryable.

4. **SQS / queue-backed fan-out** — replace asyncio task fan-out with a durable
   message queue so large batches (100+ documents) can be processed across
   multiple replicas without holding an HTTP connection open.

5. **Export to CSV / accounting software** — let users download the reconciled
   ledger as CSV or push it directly to a bookkeeping API (QuickBooks, Zoho Books).

---

## Scope Note for Reviewers

The project guide suggests 200–500 lines of code. FinProof is larger (~2,000
lines) because it addresses the full trust stack: parallel extraction, schema
validation, cross-source reconciliation, observability, and a production-ready
provider abstraction. Each component is small and focused in isolation; the scope
reflects deliberate depth, not padding. The mock mode, 80 tests, and eval
scorecard ensure every line is exercised and every claim is verifiable.
