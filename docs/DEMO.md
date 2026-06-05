# Demo & Video Script — Ledger Sentinel

This is the playbook for the 5–10 minute submission video. It is engineered so
the "wow" beats land in a fixed order and the system **cannot fail on camera**
(mock mode is deterministic — same result every take, no API key, no network).

---

## 0. Pre-flight (once)

```bash
cd ledger-sentinel/backend

# One process serves BOTH the landing page and the dashboard (single origin).
python -m venv .venv && .venv\Scripts\activate     # (Windows) or: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Open **http://localhost:8000** — the **landing page** loads first (use it for the
Act-1 problem framing). Click **Launch the Dashboard** (or go to
**/app.html**); the badge top-right should read **MOCK MODE** (or **LIVE · Claude /
Gemini / GPT** once you configure a provider in the dashboard ⚙️).

> Tip: do the live recording in MOCK MODE. It is instant, free, and identical
> every take. Mention on camera that selecting a provider, pasting a key, and
> picking a model **right in the dashboard ⚙️** swaps in real Claude / Gemini /
> GPT vision — the pipeline is unchanged. That *is* the point.
>
> Demoing from the **live Hugging Face Space**? Open the `…hf.space` URL once a
> minute before recording so the free instance is warm (see DEPLOY.md).

**Dry-run the pipeline once** (no UI) to confirm everything is wired:

```bash
cd backend && python -m scripts.run_local          # 3 posted ₹1720, 3 quarantined, 1 anomaly
python -m scripts.run_local --drift                # + 2 self-healed drifted rows
pytest -q                                          # 45 passed
python -m evals.run                                # gated eval scorecard → PASS
```

---

## 1. The 8-minute script

### Act 1 — Problem & ownership (0:00–1:30)
- "I'm submitting one project for **both** tracks: I designed the architecture
  *and* shipped the code. Same repo, both rubrics."
- The friction: *"My spending lives in three card apps, a bank CSV, UPI
  screenshots, and paper receipts. The same coffee shows up twice with two
  different amounts. The hard part isn't reading the documents — it's **trusting
  the merged result**."*
- Thesis line, say it verbatim: **"The LLM proposes; deterministic code disposes."**

### Act 2 — Architecture (1:30–3:30)  *(Tech Lead rubric)*
- Open `ARCHITECTURE.md` on GitHub (Mermaid diagrams render inline).
- Walk the **§2 system diagram**: upload → parallel fan-out → self-verify gate →
  fan-in → reconcile → POSTED or QUARANTINE.
- Pause on the **§3 state machine**: "Every transition is a *guard*, not a
  suggestion. The model can never talk its way into POSTED."
- Hit two trade-offs from **§7**: (1) parallel fan-out turns `Σ(docs)` latency
  into `max(docs)`; (2) two-tier model routing — Haiku for clean docs, Opus only
  for the ambiguous 20%.
- One scale line from **§6**: "Local and prod are the same shape — stateless API
  + worker pool. Going to AWS is swapping the in-memory bus for SQS and the
  checkpointer for Postgres, not a redesign."

### Act 3 — Live demo (3:30–6:30)  *(Engineer rubric)*
Transition line: **"Architecture is theory until it ships. Let's run it."**

1. Drag the `sample_data/` files (the 3 receipts + the UPI + `bank_statement.csv`)
   onto the dashboard. Click **Reconcile**.
2. **Beat 1 — parallel grid:** "Five workers fire at once — each its own
   document, streaming latency and a faithfulness score." (point at Agent panel)
3. **Beat 2 — duplicates collapse:** "The bank line and the UPI screenshot for
   the same Swiggy order get linked and collapsed — no double-counting."
4. **Beat 3 — the catch (the money shot):** point at the **amber/red ANOMALY**
   card. *"Brew & Co: the receipt says ₹450, the statement says ₹540. The system
   refused to post either — it quarantined the conflict with the evidence. A
   human would've missed this."*
5. **Beat 4 — low-confidence quarantine:** "Cafe Zest's line items don't add up
   to its total — a smudged scan. Self-consistency failed, so it's quarantined
   instead of guessed."
6. **Beat 5 — AgentOps:** point at the right panel. "Every step is traced,
   timed, costed, and scored. This is the observability layer — the discipline,
   not just the feature."

### Act 4 — Self-healing (6:30–7:30)  *(the futuristic beat)*
- Re-run, this time also drag `bank_statement_drifted.csv` (headers are
  `Txn Date, Narration, Withdrawal` — a vendor renamed every column).
- Point at the **amber drift banner**: *"Schema drift detected. A naive pipeline
  corrupts every row here. Instead it fuzzy-remapped the columns at 100%
  confidence and **self-healed** — Blue Tokai and Uber posted cleanly."*

### Act 5 — Failure modes & honesty (7:30–8:00)
- "What breaks? Vision misreads digits, sources double-count, CSVs drift, the
  API rate-limits. Each has a named guardrail in **§8** — and when unsure, the
  system quarantines rather than corrupts."
- "What I'd build next: confidence *calibration*, and an active-learning loop
  where every human quarantine-resolution becomes a regression eval."

---

## 2. Trade-off cheat sheet (for the live Q&A)

| If they ask… | Say |
|---|---|
| "Why LangGraph not a script?" | Checkpointing + replay + guarded edges = free audit trail; the quarantine branches are explicit and unit-tested. |
| "Why not just OCR?" | OCR returns text; reconciliation needs *structured, confidence-scored* fields. The confidence is what drives the quarantine gate. |
| "Is matching an LLM call?" | No — deterministic RapidFuzz. Matching must be fast, cheap, explainable, and testable. |
| "Why Decimal?" | Float arithmetic on currency is a correctness bug. Money is exact. |
| "How does it scale?" | Stateless workers behind a queue; throughput scales linearly with replicas until the model quota — then it's a cost/routing problem, not architecture. |
| "What's the AgentOps story?" | Traces + deterministic faithfulness scoring + (optional) Langfuse persistence + the human-in-the-loop quarantine gate. |

---

## 3. Recording tips
- Record at 1080p, hide other windows, bump terminal + browser font size.
- Pre-select the `sample_data` files so the drag is one motion.
- If a take wobbles, just re-run — mock mode is identical every time.
- Keep `ARCHITECTURE.md` open in a second tab for Act 2.
