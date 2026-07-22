<!-- MIT licensed -->

<!-- Live demo boots to mock mode — no API key needed. See docs/DEPLOY.md to deploy your own. -->

<div align="center">

# 🛡️ FinProof

**An autonomous reconciliation engine that turns a messy pile of receipts, bank
statements, and UPI screenshots into one clean, trustworthy ledger — and refuses
to post a number it can't prove.**

_The LLM proposes; deterministic code disposes._

[![Live Demo](https://img.shields.io/badge/live%20demo-online-brightgreen?logo=render&logoColor=white)](https://fin-proof.onrender.com)
[![Demo video](https://img.shields.io/badge/demo-90s%20walkthrough-ff4438)](https://fin-proof.onrender.com/fin-proof-demo.webp)
[![CI](https://github.com/Vishal9199/fin-proof/actions/workflows/ci.yml/badge.svg)](https://github.com/Vishal9199/fin-proof/actions/workflows/ci.yml)
![Tests](https://img.shields.io/badge/tests-80%20passing-brightgreen)
![Eval gates](https://img.shields.io/badge/eval%20gates-PASS-brightgreen)
![Python](https://img.shields.io/badge/python-3.13-blue)
![Providers](https://img.shields.io/badge/models-Claude%20%C2%B7%20Gemini%20%C2%B7%20GPT%20%C2%B7%20Mock-7c3aed)
![Frontend](https://img.shields.io/badge/frontend-no%20build%20step-ff69b4)
![License](https://img.shields.io/badge/license-MIT-green)

[**▶ Live Demo**](https://fin-proof.onrender.com) · [**🎬 Demo video**](https://fin-proof.onrender.com/fin-proof-demo.webp) · [Architecture](./docs/ARCHITECTURE.md) · [API reference](./docs/API.md) · [Quickstart](#-quickstart) · [How it works](#-how-it-works) · [Deploy](./docs/DEPLOY.md) · [Contributing](./docs/CONTRIBUTING.md)

<br/>

<img src="./docs/Dashboard.png" alt="FinProof dashboard mid-run: six documents extracted in parallel, a reconciliation canvas with cross-source duplicate links and a flagged BREW & CO amount-conflict anomaly, and a live AgentOps panel showing per-step latency, tokens, and cost." width="100%" />

<sub>A live 6-document run — parallel extraction · schema-drift self-heal · cross-source duplicates collapsed · the <b>BREW &amp; CO ₹450-vs-₹540 conflict quarantined</b> · per-step AgentOps cost &amp; faithfulness.</sub>

</div>

> **▶ Live demo → [fin-proof.onrender.com](https://fin-proof.onrender.com)**
> The app boots in deterministic **mock mode** — no API key, no network, no
> billing — so the full reconcile pipeline runs in your browser the moment it
> loads. Want your own? **[Deploy your own instance →](./docs/DEPLOY.md)**.
>
> **🎬 Watch the 90-second narrated walkthrough → [fin-proof-demo.webp](https://fin-proof.onrender.com/fin-proof-demo.webp)** — also embedded on the landing page.

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

> FinProof is that system. It is built around one principle:
> **the LLM proposes, deterministic code disposes.**

## Why this is more than a chatbot

| Capability | What it means |
|---|---|
| 🚀 **Parallel fan-out** | Every document gets its own extraction worker, run concurrently. 17 documents process in roughly the time of the slowest one. |
| 📸 **Real documents, not stand-ins** | JPEG/PNG photos and screenshots ride native **vision** content blocks; PDFs get a text-layer probe — born-digital statements expand into rows deterministically, scanned ones go to the model as native PDF attachments. Routing is by **magic bytes**, never the filename; a non-financial image is classified and quarantined, never turned into a number. |
| 🔍 **Self-verification** | Each amount is extracted twice and must agree; low-confidence fields are routed to a quarantine lane, never auto-posted. Statements get a per-row **element-wise** amount re-read. |
| 🔏 **PII redaction at the egress boundary** | Account numbers, PAN, IFSC, phones, and emails are masked deterministically before any document text reaches a live provider; mock mode never sends bytes anywhere. |
| 🔗 **Cross-source reconciliation** | A matching agent links the bank line, the UPI screenshot, and the paper receipt for the *same* purchase, collapsing duplicates. |
| 🧩 **Schema-drift firewall** | Bank CSVs change columns without warning. A Pandera contract detects drift and quarantines bad rows instead of crashing. |
| 📊 **AgentOps built in** | Every reasoning step is traced, scored (faithfulness / confidence), timed, and costed — visible live on the dashboard. |
| 🎯 **Gated evals** | A labeled golden set scores quarantine precision/recall, extraction exactness, and link F1 on every run; the safety metric (quarantine recall) is a hard CI gate. |
| 🔌 **Pluggable model providers** | Choose **Anthropic, Google Gemini, OpenAI, or Mock** from the dashboard — paste a key, **fetch the models that key can actually use**, pick one from a dropdown, and the agent switches live with no restart. Default is always mock; nothing about the model stack is decided from the environment. One uniform provider contract; the pipeline never imports an SDK. |
| 🔁 **Durable runs** | Execution is decoupled from the live stream and backed by a replay buffer — a late, dropped, or proxy-blocked dashboard still gets the full picture (or polls the result). |
| 🛟 **Graceful degradation** | No API key? It runs in deterministic mock mode so the demo never dies on stage. Transient model errors retry with backoff, then degrade — the run always completes. |
| 🤖 **AI Quarantine Advisor** | Every flagged card exposes a **✨ AI Suggest** button. One click fetches a structured explanation of *why* it was quarantined plus 2–3 actionable override options (use statement amount, use receipt amount, dismiss). Choosing an option resolves the item and moves it to the Posted ledger in a single click — no form, no manual edit. Works in mock mode with curated golden suggestions. |
| 💬 **AI Ledger Chat + charting** | A conversational AI assistant lives at the bottom of the dashboard. Pre-built query chips (📊 Category Breakdown, 💸 Total Spend, ⚠️ Show Flags) or free-text questions return instant answers. Category or spend queries automatically render animated, accessible **CSS bar charts** inline in the chat — no third-party chart library required. |

## 🎯 Key capabilities

A personal autonomous financial reconciliation engine — built to demonstrate
what real-world agentic AI looks like when it touches money:

| Capability | Where it lives |
|---|---|
| **AgentOps & observability** — monitor agent behavior, evaluate outputs, guard against bad ones | Every node emits a traced span (latency · tokens · USD · faithfulness), streamed live to the AgentOps panel and optionally to Langfuse. A **gated eval scorecard** turns a quality regression into a red build. → [ARCHITECTURE §8](./docs/ARCHITECTURE.md#8-failure-modes--hardening-agentops--guardrails) |
| **Cloud-native, not `localhost`** — a deployable system | One container serves API + dashboard; deployed live on **Render**, with the AWS production topology drawn out. → [DEPLOY.md](./docs/DEPLOY.md) · [ARCHITECTURE §6](./docs/ARCHITECTURE.md#6-scale-considerations) |
| **Reliable AI ↔ structured-data integration** | A **Pandera schema-drift firewall** quarantines (and self-heals) malformed bank CSVs instead of corrupting the ledger; one canonical Pydantic contract is the only thing the engine sees. → [ARCHITECTURE §5](./docs/ARCHITECTURE.md#5-the-contract-canonical-data-model) |
| **"Think in systems, not tasks" · end-to-end ownership** | A LangGraph state machine where every transition is a *guard*, 8 named failure modes each with a coded guardrail, and a documented roadmap. → [ARCHITECTURE §3](./docs/ARCHITECTURE.md#3-the-reconciliation-state-machine) |

### 📊 At a glance

| | |
|---|---|
| **Principle** | The LLM proposes; deterministic code disposes — no number is POSTED unless it can be proven |
| **Stack** | FastAPI · LangGraph · Pandera · RapidFuzz · Pydantic · vanilla-JS dashboard (no build) |
| **Models** | Anthropic Claude · Google Gemini · OpenAI GPT · deterministic Mock — switchable live from the dashboard |
| **Tests / evals** | `80` passing · `5` gated eval metrics (safety gate = quarantine recall ≥ 1.0) |
| **AI UX** | AI Quarantine Advisor (inline resolution) · AI Ledger Chat (query chips + CSS bar charts) |
| **Run anywhere** | Boots to mock mode: no API key, no network, deterministic every time |
| **Deploy** | One image, one origin → deployed on Render (Cloud Run ready) |

## 🏗️ How it works

```
   Upload pile ──► FAN-OUT (1 worker / doc, parallel; routed by magic bytes)
                       │  photos/screens → vision blocks (configured provider)
                       │  PDFs           → text-layer probe → rows · or native PDF vision
                       │  bank CSV       → Pandera schema contract
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
git clone https://github.com/Vishal9199/fin-proof
cd fin-proof

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
- **Deploy your own live demo → [DEPLOY.md](./docs/DEPLOY.md)** (Render / Cloud Run)

```bash
# Prove correctness without a server — deterministic, no API key:
cd backend
pytest -q                      # unit + end-to-end + provider + eval gates (80 tests)
python -m evals.run            # the gated eval scorecard
python -m scripts.run_local    # offline terminal demo of the full pipeline
```

## 🎬 The 90-second "wow"

1. Drag the whole `sample_data/` pile onto the dashboard.
2. Watch the **agent grid** light up — every document extracting in parallel, confidence streaming in.
3. The **reconciliation canvas** draws links between the bank line and the receipt photo for the same coffee.
4. One entry pulses **amber**: *"Amount mismatch — receipt ₹450, statement ₹540. Quarantined."* The system caught a discrepancy a human would miss, and shows the evidence trail.
5. Click **✨ AI Suggest** on the quarantined BREW & CO card — the AI explains the conflict and offers *"Use Statement ₹540"*, *"Use Receipt ₹450"*, or *"Dismiss"*. Pick one; the card moves to Posted instantly.
6. In the **AI Chat** at the bottom, click the 📊 **Category Breakdown** chip — the assistant returns a spending split and draws an animated CSS bar chart inline, with no page reload.
7. Open the **AgentOps tab**: traces, per-step latency, token cost, and a faithfulness score for every extraction.
8. (Optional) Mid-demo, drop in `bank_statement_drifted.csv` with a renamed column — the **schema-drift firewall** detects it, quarantines the rows, and self-heals the mapping live.

## 📚 Documentation map

| Doc | Audience | What's inside |
|---|---|---|
| **[README.md](./README.md)** (you are here) | Everyone | The problem, the thesis, what makes it a system, quickstart |
| **[USE_CASES.md](./docs/USE_CASES.md)** | Reviewers | 3 concrete use cases with sample inputs and expected outputs |
| **[PROMPTS.md](./docs/PROMPTS.md)** | Reviewers | All 6 prompts, the self-consistency technique, and iteration history |
| **[AI_USAGE_JOURNAL.md](./docs/AI_USAGE_JOURNAL.md)** | Reviewers | How AI was used to *build* this project — tools, prompts, decisions |
| **[REFLECTION.md](./docs/REFLECTION.md)** | Reviewers | What worked well, what I'd do differently, known limitations, roadmap |
| **[ARCHITECTURE.md](./docs/ARCHITECTURE.md)** | Tech Lead reviewers | Diagrams, the state machine, scale math, 8 failure modes + guardrails, evals, trade-offs, rejected alternatives |
| **[API.md](./docs/API.md)** | Integrators | Every REST endpoint, the SSE event protocol, the runtime control plane, `curl` examples |
| **[DEPLOY.md](./docs/DEPLOY.md)** | Operators | Deploy a live demo to Render or Cloud Run |
| **[CONTRIBUTING.md](./docs/CONTRIBUTING.md)** | Engineers | Dev setup, the test/eval workflow, and **adding a model provider in one method** |
| **[CONFIGURATION.md](./docs/CONFIGURATION.md)** | Operators | Every setting — ops env vars vs. dashboard-driven model config — with defaults |
| **[TROUBLESHOOTING.md](./docs/TROUBLESHOOTING.md)** | Everyone | Symptom → cause → fix for the real issues (venv, live providers, uploads, deploy) |
| **[SECURITY.md](./docs/SECURITY.md)** | Security reviewers | Secret handling, data governance, the human-in-the-loop gate, disclosure |

## 📁 Repository layout

```
fin-proof/
├── docs/
│   ├── USE_CASES.md           # 3 concrete use cases with inputs, outputs, and golden numbers
│   ├── PROMPTS.md             # all 6 prompts explained — design rationale + iterations
│   ├── AI_USAGE_JOURNAL.md    # how AI was used to BUILD the project
│   ├── REFLECTION.md          # what worked, what to improve, known limitations
│   ├── ARCHITECTURE.md        # system design, diagrams, scale, trade-offs, failure modes
│   ├── API.md                 # REST + SSE reference, control-plane flow, curl examples
│   ├── DEPLOY.md              # live demo deployment (Render) + AWS path
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
│   │   ├── privacy.py     # PII redaction at the egress boundary (text → live provider)
│   │   ├── providers/     # uniform LLMProvider contract (text + vision/PDF attachments)
│   │   ├── extraction/    # magic-byte router · vision/image · PDF lane · CSV/Pandera drift
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
