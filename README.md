---
title: Ledger Sentinel
emoji: 🛡️
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Autonomous financial-document reconciliation engine
---

<!-- The YAML block above configures the Hugging Face Space (Docker SDK). It is
     ignored as content by GitHub (rendered as a small metadata table) and read
     by Hugging Face to build/route the Space. Live demo boots to mock mode — no
     API key needed. See docs/DEPLOY.md to publish your own. -->

<div align="center">

# 🛡️ Ledger Sentinel

**An autonomous reconciliation engine that turns a messy pile of receipts, bank
statements, and UPI screenshots into one clean, trustworthy ledger — and refuses
to post a number it can't prove.**

_The LLM proposes; deterministic code disposes._

[![Live Demo](https://img.shields.io/badge/live%20demo-online-brightgreen?logo=huggingface&logoColor=white)](https://huggingface.co/spaces/mhussam-ai/ledger-sentinel)
[![Demo video](https://img.shields.io/badge/demo-90s%20walkthrough-ff4438)](https://mhussam-ai-ledger-sentinel.hf.space/ledger-sentinel-demo.mp4)
[![CI](https://github.com/mhussam-ai/ledger-sentinel/actions/workflows/ci.yml/badge.svg)](https://github.com/mhussam-ai/ledger-sentinel/actions/workflows/ci.yml)
![Tests](https://img.shields.io/badge/tests-46%20passing-brightgreen)
![Eval gates](https://img.shields.io/badge/eval%20gates-PASS-brightgreen)
![Python](https://img.shields.io/badge/python-3.13-blue)
![Providers](https://img.shields.io/badge/models-Claude%20%C2%B7%20Gemini%20%C2%B7%20GPT%20%C2%B7%20Mock-7c3aed)
![Frontend](https://img.shields.io/badge/frontend-no%20build%20step-ff69b4)
![License](https://img.shields.io/badge/license-MIT-green)

[**▶ Live Demo**](https://huggingface.co/spaces/mhussam-ai/ledger-sentinel) · [**🎬 Demo video**](https://mhussam-ai-ledger-sentinel.hf.space/ledger-sentinel-demo.mp4) · [Architecture](./docs/ARCHITECTURE.md) · [API reference](./docs/API.md) · [Quickstart](#-quickstart) · [How it works](#-how-it-works) · [Deploy](./docs/DEPLOY.md) · [Demo script](./docs/DEMO.md) · [Contributing](./docs/CONTRIBUTING.md)

<br/>

<img src="./docs/Dashboard.png" alt="Ledger Sentinel dashboard mid-run: six documents extracted in parallel, a reconciliation canvas with cross-source duplicate links and a flagged BREW & CO amount-conflict anomaly, and a live AgentOps panel showing per-step latency, tokens, and cost." width="100%" />

<sub>A live 6-document run — parallel extraction · schema-drift self-heal · cross-source duplicates collapsed · the <b>BREW &amp; CO ₹450-vs-₹540 conflict quarantined</b> · per-step AgentOps cost &amp; faithfulness.</sub>

</div>

> **▶ Live demo → [huggingface.co/spaces/mhussam-ai/ledger-sentinel](https://huggingface.co/spaces/mhussam-ai/ledger-sentinel)**
> The app boots in deterministic **mock mode** — no API key, no network, no
> billing — so the full reconcile pipeline runs in your browser the moment it
> loads. Want your own? **[Deploy a free Space in ~5 minutes →](./docs/DEPLOY.md)**.
>
> **🎬 Watch the 90-second narrated walkthrough → [ledger-sentinel-demo.mp4](https://mhussam-ai-ledger-sentinel.hf.space/ledger-sentinel-demo.mp4)** — also embedded on the landing page.

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
| 🧩 **Schema-drift firewall** | Bank CSVs change columns without warning. A Pandera contract detects drift and quarantines bad rows instead of crashing. |
| 📊 **AgentOps built in** | Every reasoning step is traced, scored (faithfulness / confidence), timed, and costed — visible live on the dashboard. |
| 🎯 **Gated evals** | A labeled golden set scores quarantine precision/recall, extraction exactness, and link F1 on every run; the safety metric (quarantine recall) is a hard CI gate. |
| 🔌 **Pluggable model providers** | Choose **Anthropic, Google Gemini, OpenAI, or Mock** from the dashboard — paste a key, **fetch the models that key can actually use**, pick one from a dropdown, and the agent switches live with no restart. Default is always mock; nothing about the model stack is decided from the environment. One uniform provider contract; the pipeline never imports an SDK. |
| 🔁 **Durable runs** | Execution is decoupled from the live stream and backed by a replay buffer — a late, dropped, or proxy-blocked dashboard still gets the full picture (or polls the result). |
| 🛟 **Graceful degradation** | No API key? It runs in deterministic mock mode so the demo never dies on stage. Transient model errors retry with backoff, then degrade — the run always completes. |

## 🎯 Built for both tracks

This is **one submission for both the Engineer and the Tech Lead track** — the
architecture and the shipped, tested implementation are the same artifact. The
table maps what Damco said it looks for to where it lives in this repo.

| What Damco evaluates | Where it is in this project |
|---|---|
| **AgentOps & observability** — monitor agent behavior, evaluate outputs, guard against bad ones | Every node emits a traced span (latency · tokens · USD · faithfulness), streamed live to the AgentOps panel and optionally to Langfuse. A **gated eval scorecard** turns a quality regression into a red build. → [ARCHITECTURE §8](./docs/ARCHITECTURE.md#8-failure-modes--hardening-agentops--guardrails) |
| **Cloud-native, not `localhost`** — a deployable system | One container serves API + dashboard; ships to a free **Hugging Face Space** in ~5 min, with the AWS production topology drawn out. → [DEPLOY.md](./docs/DEPLOY.md) · [ARCHITECTURE §6](./docs/ARCHITECTURE.md#6-scale-considerations) |
| **Reliable AI ↔ structured-data integration** | A **Pandera schema-drift firewall** quarantines (and self-heals) malformed bank CSVs instead of corrupting the ledger; one canonical Pydantic contract is the only thing the engine sees. → [ARCHITECTURE §5](./docs/ARCHITECTURE.md#5-the-contract-canonical-data-model) |
| **"Think in systems, not tasks" · end-to-end ownership** | A LangGraph state machine where every transition is a *guard*, 8 named failure modes each with a coded guardrail, and a documented roadmap. → [ARCHITECTURE §3](./docs/ARCHITECTURE.md#3-the-reconciliation-state-machine) |

### 📊 At a glance

| | |
|---|---|
| **Principle** | The LLM proposes; deterministic code disposes — no number is POSTED unless it can be proven |
| **Stack** | FastAPI · LangGraph · Pandera · RapidFuzz · Pydantic · vanilla-JS dashboard (no build) |
| **Models** | Anthropic Claude · Google Gemini · OpenAI GPT · deterministic Mock — switchable live from the dashboard |
| **Tests / evals** | `46` passing · `5` gated eval metrics (safety gate = quarantine recall ≥ 1.0) |
| **Run anywhere** | Boots to mock mode: no API key, no network, deterministic every time |
| **Deploy** | One image, one origin → free Hugging Face Space (Render / Cloud Run ready) |

## 🏗️ How it works

```
   Upload pile ──► FAN-OUT (1 worker / doc, parallel)
                       │  receipts/PDF → vision (configured provider)
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
math, and trade-offs live in **[ARCHITECTURE.md](./docs/ARCHITECTURE.md)**.

## ⚡ Quickstart

```bash
git clone https://github.com/mhussam-ai/ledger-sentinel
cd ledger-sentinel

# One command, full stack — builds the SAME single-origin image the live demo
# runs (one FastAPI process serves the dashboard + the API). Boots to mock mode;
# pick provider + key + model live in the dashboard ⚙️ — no env, no keys.
docker compose up                              # → http://localhost:8000

# …or run it directly with Python (no build step, no separate UI server):
cd backend && pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

- Landing page → http://localhost:8000  (the submission front door)
- Dashboard → http://localhost:8000/app.html  (or click **Launch the Dashboard**)
- API + interactive docs → http://localhost:8000/docs
- Sample pile to drag in → [`sample_data/`](./sample_data)
- **Deploy your own free live demo → [DEPLOY.md](./docs/DEPLOY.md)** (Hugging Face Space)

```bash
# Prove correctness without a server — deterministic, no API key:
cd backend
pytest -q                      # unit + end-to-end + provider + eval gates (46 tests)
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

## 📚 Documentation map

| Doc | Audience | What's inside |
|---|---|---|
| **[README.md](./README.md)** (you are here) | Everyone | The problem, the thesis, what makes it a system, quickstart |
| **[ARCHITECTURE.md](./docs/ARCHITECTURE.md)** | Tech Lead reviewers | Diagrams, the state machine, scale math, 8 failure modes + guardrails, evals, trade-offs, rejected alternatives |
| **[API.md](./docs/API.md)** | Integrators | Every REST endpoint, the SSE event protocol, the runtime control plane, `curl` examples |
| **[DEPLOY.md](./docs/DEPLOY.md)** | Operators | Ship a free live demo to a Hugging Face Space in ~5 min (Render / Cloud Run too) |
| **[DEMO.md](./docs/DEMO.md)** | The 5–10 min video | A beat-by-beat recording script + a live-Q&A trade-off cheat sheet |
| **[CONTRIBUTING.md](./docs/CONTRIBUTING.md)** | Engineers | Dev setup, the test/eval workflow, and **adding a model provider in one method** |
| **[CONFIGURATION.md](./docs/CONFIGURATION.md)** | Operators | Every setting — ops env vars vs. dashboard-driven model config — with defaults |
| **[TROUBLESHOOTING.md](./docs/TROUBLESHOOTING.md)** | Everyone | Symptom → cause → fix for the real issues (venv, live providers, uploads, deploy) |
| **[SECURITY.md](./docs/SECURITY.md)** | Security reviewers | Secret handling, data governance, the human-in-the-loop gate, disclosure |

## 📁 Repository layout

```
ledger-sentinel/
├── docs/
│   ├── ARCHITECTURE.md        # system design, diagrams, scale, trade-offs, failure modes
│   ├── API.md                 # REST + SSE reference, control-plane flow, curl examples
│   ├── DEPLOY.md              # free live demo (Hugging Face Space) + AWS path
│   ├── DEMO.md                # the 5–10 min video script + Q&A cheat sheet
│   ├── CONTRIBUTING.md        # dev workflow + "add a provider in one method"
│   ├── CONFIGURATION.md       # every setting: ops env vars vs dashboard model config
│   ├── TROUBLESHOOTING.md     # symptom → cause → fix for the real issues
│   └── SECURITY.md            # secret handling, data governance, disclosure
├── Dockerfile             # single image: API + dashboard, one origin (the live demo)
├── docker-compose.yml     # one-command local stack (same image as prod)
├── .github/workflows/     # CI: pytest + gated eval scorecard on every push
├── backend/
│   ├── app/
│   │   ├── main.py        # FastAPI: decoupled run launch + SSE replay tail + /config control plane
│   │   ├── events.py      # event bus with per-run replay buffer (durable runs)
│   │   ├── schemas.py     # Pydantic canonical models (the contract)
│   │   ├── runtime.py     # control plane: live provider/key/model selection (plug-and-play)
│   │   ├── providers/     # uniform LLMProvider contract → Anthropic · Google · OpenAI · Mock
│   │   ├── extraction/    # vision (any provider) · CSV/Pandera drift · self-verify · retry/backoff
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

[Anthropic Claude](https://www.anthropic.com) · [Google Gemini](https://ai.google.dev) ·
[OpenAI](https://platform.openai.com) · [LangGraph](https://github.com/langchain-ai/langgraph) ·
[Pandera](https://github.com/unionai-oss/pandera) · [Langfuse](https://github.com/langfuse/langfuse) ·
[RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) · [FastAPI](https://github.com/fastapi/fastapi)

## License

MIT — see [LICENSE](./LICENSE).
