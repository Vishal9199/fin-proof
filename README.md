<div align="center">

# 🛡️ Ledger Sentinel

**An autonomous reconciliation engine that turns a messy pile of receipts, bank
statements, and UPI screenshots into one clean, trustworthy ledger — and refuses
to post a number it can't prove.**

[Architecture](./ARCHITECTURE.md) · [Quickstart](#-quickstart) · [How it works](#-how-it-works) · [Demo script](#-the-90-second-wow)

</div>

---

## The problem (one I live every month)

My spending is scattered across three card apps, a bank CSV, UPI screenshots, and
a wallet full of paper receipts. Reconciling them is genuinely awful: the same
coffee shows up in two places with two different amounts, a receipt gets
double-counted, and OCR quietly turns ₹450 into ₹480. The friction isn't
*reading* the documents — it's *trusting* the merged result.

Existing apps "categorize a screenshot." That's a script. The real problem needs
a **system** that can extract in parallel, **verify its own work**, cross-link
duplicates across sources, and quarantine anything it isn't sure about instead of
silently corrupting the ledger.

> Ledger Sentinel is that system. It is built around one principle:
> **the LLM proposes, deterministic code disposes.**

## Why this is more than a chatbot

| Capability | What it means |
|---|---|
| 🚀 **Parallel fan-out** | Every document gets its own extraction worker, run concurrently. 17 documents process in roughly the time of the slowest one. |
| 🔍 **Self-verification** | Each amount is extracted twice and must agree; low-confidence fields are routed to a quarantine lane, never auto-posted. |
| 🔗 **Cross-source reconciliation** | A matching agent links the bank line, the UPI screenshot, and the paper receipt for the *same* purchase, collapsing duplicates. |
| 🧱 **Schema-drift firewall** | Bank CSVs change columns without warning. A Pandera contract detects drift and quarantines bad rows instead of crashing. |
| 📊 **AgentOps built in** | Every reasoning step is traced, scored (faithfulness / confidence), timed, and costed — visible live on the dashboard. |
| 🎯 **Gated evals** | A labeled golden set scores quarantine precision/recall, extraction exactness, and link F1 on every run; the safety metric (quarantine recall) is a hard CI gate. |
| 🔁 **Durable runs** | Execution is decoupled from the live stream and backed by a replay buffer — a late, dropped, or proxy-blocked dashboard still gets the full picture (or polls the result). |
| 🛟 **Graceful degradation** | No API key? It runs in deterministic mock mode so the demo never dies on stage. Transient model errors retry with backoff, then degrade — the run always completes. |

## 🏗️ How it works

```
   Upload pile ──► FAN-OUT (1 worker / doc, parallel)
                       │  receipts/PDF → Claude vision
                       │  bank CSV     → Pandera schema contract
                       ▼
                  SELF-VERIFY  ──(low confidence)──► QUARANTINE ──► human review
                       │
                       ▼
                  FAN-IN to canonical Transaction store
                       │
                       ▼
                  RECONCILE (fuzzy match: amount × date × merchant)
                       │
            ┌──────────┼───────────┐
            ▼          ▼           ▼
          LINK     DUPLICATE    ANOMALY ──► QUARANTINE ──► human review
            └──────────┴───────────┘
                       ▼
                    POSTED ✅   (every step traced to the AgentOps panel)
```

The orchestration is a **LangGraph state machine** —
`EXTRACTED → VERIFIED → MATCHED → (conflict → QUARANTINE) → POSTED` — which gives
us checkpointing, replay, and a free audit trail. Full design, diagrams, scale
math, and trade-offs live in **[ARCHITECTURE.md](./ARCHITECTURE.md)**.

## ⚡ Quickstart

```bash
git clone https://github.com/mhussam-ai/ledger-sentinel
cd ledger-sentinel
cp .env.example .env        # add ANTHROPIC_API_KEY (optional — mock mode works without)

# One command, full stack:
docker compose up

# …or run the two pieces directly:
cd backend && pip install -r requirements.txt && uvicorn app.main:app --port 8000
cd frontend && python -m http.server 5173      # fully static — no build step
```

- Landing page → http://localhost:5173  (the submission front door)
- Dashboard → http://localhost:5173/app.html  (or click **Launch the Dashboard**)
- API + interactive docs → http://localhost:8000/docs
- Sample pile to drag in → [`sample_data/`](./sample_data)

```bash
# Prove correctness without a server — deterministic, no API key:
cd backend
pytest -q                      # unit + end-to-end + eval gates (16 tests)
python -m evals.run            # the gated eval scorecard
python -m scripts.run_local    # offline terminal demo of the full pipeline
```

## 🎬 The 90-second "wow"

1. Drag the whole `sample_data/` pile onto the dashboard.
2. Watch the **agent grid** light up — every document extracting in parallel, confidence streaming in.
3. The **reconciliation canvas** draws links between the bank line and the receipt photo for the same coffee.
4. One entry pulses **amber**: *"Amount mismatch — receipt ₹450, statement ₹540. Quarantined."* The system caught a discrepancy a human would miss, and shows the evidence trail.
5. Open the **AgentOps tab**: traces, per-step latency, token cost, and a faithfulness score for every extraction.
6. (Optional) Mid-demo, drop in `bank_statement_drifted.csv` with a renamed column — the **schema-drift firewall** detects it, quarantines the rows, and self-heals the mapping live.

## 📁 Repository layout

```
ledger-sentinel/
├── ARCHITECTURE.md        # system design, diagrams, scale, trade-offs, failure modes
├── backend/
│   ├── app/
│   │   ├── main.py        # FastAPI: decoupled run launch + SSE replay tail
│   │   ├── events.py      # event bus with per-run replay buffer (durable runs)
│   │   ├── schemas.py     # Pydantic canonical models (the contract)
│   │   ├── extraction/    # Claude vision · CSV/Pandera drift · self-verify · retry/backoff
│   │   └── graph/         # LangGraph reconciliation state machine + fuzzy matching
│   ├── evals/             # golden dataset · metrics · gated scorecard (python -m evals.run)
│   └── tests/             # unit + end-to-end + eval-gate regression tests
├── frontend/              # vanilla JS, no build step
│   ├── index.html         # cinematic landing page (the submission front door)
│   ├── app.html           # the live dashboard (grid · canvas · AgentOps)
│   ├── landing.js/.css     # scroll/typing animations · tokens.css = shared design system
│   └── app.js · styles.css # dashboard logic + styles
└── sample_data/           # a messy pile to reconcile
```

## 🛠️ Built on (and grateful for)

[Anthropic Claude](https://www.anthropic.com) · [LangGraph](https://github.com/langchain-ai/langgraph) ·
[Pandera](https://github.com/unionai-oss/pandera) · [Langfuse](https://github.com/langfuse/langfuse) ·
[RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) · [FastAPI](https://github.com/fastapi/fastapi)

## License

MIT — see [LICENSE](./LICENSE).
