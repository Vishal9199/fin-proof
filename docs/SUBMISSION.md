# Submission — Build at Damco

**Candidate:** Mohammad Hussam · **Tracks:** A (Engineer) **+** B (Tech Lead) — one project, both rubrics.
**Project:** Ledger Sentinel — an autonomous financial-document reconciliation engine.
**Principle:** *the LLM proposes; deterministic code disposes.*

| | |
| --- | --- |
| ▶ **Live app** | https://mhussam-ai-ledger-sentinel.hf.space (boots to mock mode — no key) |
| 🎬 **Demo video** | `<YOUTUBE_UNLISTED_LINK>` ← **upload the screen recording, then paste here** |
| ⌥ **Code (public)** | https://github.com/mhussam-ai/ledger-sentinel |
| 🏛 **Architecture doc** | https://github.com/mhussam-ai/ledger-sentinel/blob/main/docs/ARCHITECTURE.md |

---

## The problem (real, personally experienced)

My spending lives in three card apps, a bank CSV, UPI screenshots, and a wallet of
paper receipts. The same coffee shows up twice with two different amounts; a receipt
gets double-counted; OCR quietly turns ₹450 into ₹480. **The hard part was never
*reading* the documents — it's trusting the merged result.** This is exactly Damco's
own example — *"tracking expenses across UPI apps is a nightmare"* — so I built the
**system** answer to it, not another categorizer script.

## One project, both tracks

Damco asks Engineers (Track A) to **build & ship** and Tech Leads (Track B) to
**design the architecture**. This single repo is both: the design and the shipped,
tested implementation are the same artifact.

| Damco asks for… | Track | Where it is |
| --- | --- | --- |
| Pick a complex problem (needs a system, not a script) | A + B | Cross-source reconciliation with self-verification — see above |
| **Build the solution — code it, ship it, public repo** | A | FastAPI · LangGraph state machine · Pandera · RapidFuzz · multi-provider · **46 tests** · deployed live |
| **Design the architecture — design doc, diagrams, trade-offs, scale** | B | [ARCHITECTURE.md](./ARCHITECTURE.md) — 6 Mermaid diagrams, state machine, scale math, F1–F8 failure modes |
| Record a 5–10 min video walking through your thinking | A + B | YouTube (Unlisted) — recorded from [WALKTHROUGH.md](./WALKTHROUGH.md) |
| AgentOps & observability | — | Traced/timed/costed/scored spans on the dashboard + a **gated eval scorecard** (safety gate = quarantine recall ≥ 1.0) |
| Cloud-native, not `localhost` | — | One container serves API + dashboard; live on a Hugging Face Space; AWS topology drawn in ARCHITECTURE §6 |
| Reliable AI ↔ structured-data integration | — | Pandera schema-drift firewall + one canonical Pydantic contract; quarantine over corruption |

## What the video covers (the union of both tracks' requirements)

Recorded from [WALKTHROUGH.md](./WALKTHROUGH.md), ~8 min:
Problem (what / why hard / why it matters / how scoped) → **live demo with two failure
scenarios** (the Brew & Co ₹450-vs-₹540 anomaly + CSV schema-drift self-heal) → how it's
built (guarded state machine, canonical contract) → tradeoffs (alternatives + *what would
change my mind*) → what's broken + what I'd build next.

## Rubric self-check (`engineer.test.ts`)

- **[PASS] Problem Identification** — a real, personally-lived friction; framed as a trust problem, not an OCR one.
- **[PASS] Technical understanding** — guarded state machine, two-read self-consistency, Pandera firewall, Decimal money, bounded concurrency; explained with the *why* and the rejected alternative.
- **[PASS] Scoping** — deliberately narrowed to cross-source reconciliation + self-verification; deterministic mock mode so it's demoable and testable; non-goals documented.
- **[PASS] Self-Awareness** — named failure modes F1–F8, an honest "what's broken," and a concrete roadmap (confidence calibration + active-learning eval loop).

## How to submit

Per the challenge page, email **hiring@damcogroup.com** with the subject below and
three attachments/links:

```
mail -s "Builder Challenge - Mohammad Hussam - Track A + B" hiring@damcogroup.com
  (link)   → <YOUTUBE_UNLISTED_LINK>        # the screen-recording walkthrough
  (source) → https://github.com/mhussam-ai/ledger-sentinel   # + docs/ARCHITECTURE.md
  (bio)    → 2–3 lines (draft below)
```

**Draft bio (edit to taste):**

> I build production AI systems where the model is one component inside deterministic
> guardrails — not the whole product. I care about evals, observability, and shipping
> things that fail *safe*. Ledger Sentinel is that philosophy applied to financial
> reconciliation: it refuses to post a number it can't prove.

## After you submit (so you can prepare)

1. **Code Review** — usually within 5 business days.
2. **Live Discussion (20 min)** — be ready for the track-specific scenarios Damco lists:
   - *Track A (Engineer):* "Can you add a small feature while sharing your screen? Why did you use X here instead of Y?" → know the codebase cold; be ready to live-edit (e.g., add a provider or a guard) and justify RapidFuzz-vs-LLM, Decimal-vs-float, LangGraph-vs-script.
   - *Track B (Tech Lead):* "A client just added a requirement — how do you adapt? Budget cut in half — what gets cut?" → lean on the stateless/worker-pool shape and the two-tier model routing (drop the deep model, keep the guards).
3. **Cultural Screening** — formal next steps.

## Pre-submit checklist

- [ ] Record the walkthrough ([WALKTHROUGH.md](./WALKTHROUGH.md)), ≤ 10:00.
- [ ] Upload to **YouTube → Unlisted**; paste the link into this file's table + the `(link)` slot above.
- [ ] Confirm the live Space is awake and the repo is public.
- [ ] Finalize the bio.
- [ ] Send the email (the page's "Open Email" button pre-fills it). Questions get a reply within 24h.
