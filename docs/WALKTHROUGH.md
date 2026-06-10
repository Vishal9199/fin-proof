# Screen-Recording Walkthrough — the submission video

The master script for the **5–10 minute** screen recording you'll upload to
**YouTube (Unlisted)** — the official `(link)` attachment of the submission.
It walks the evaluator through the **landing page → the live app → the
architecture & code**, in one take, and is built so the "wow" beats land in a
fixed order and **nothing can fail on camera** (the app boots to deterministic
mock mode — same result every take, no API key, no network).

> One video, **both tracks** (Track A Engineer + Track B Tech Lead). It therefore
> covers the *union* of what each track's video must include (see the map below).
> For the deeper dashboard beat-by-beat + live-Q&A cheat sheet see [DEMO.md](./DEMO.md);
> for the design narrative keep [ARCHITECTURE.md](./ARCHITECTURE.md) in a tab.

Target length: **~8:00** (hard cap 10:00). Presenter: **Mohammad Hussam**.

Damco's mantras to keep in mind while recording: *"clear thinking matters more
than perfect diagrams"* and *"we're not looking for polish — we're looking for
understanding."* Talk like you're explaining it to a teammate.

---

## What Damco asks the video to cover (both tracks) → where it lands here

| Required section (A = Engineer, B = Tech Lead)                          | Covered in |
| ----------------------------------------------------------------------- | ---------- |
| **Problem** — what you chose, why it's hard, why it matters to you, how you scoped it (A + B) | Act 1 |
| **Live Demo** — show it working, incl. **≥1 edge case / failure scenario** (B) | Act 3 + Act 4 |
| **System Design / How You Built It** — architecture, components, data flows, code, key choices + *why* (A + B) | Act 5–6 |
| **Tradeoffs** — key decisions, alternatives considered, **what would change your mind** (A) | Act 6 |
| **Failure Modes / What's Broken** — what breaks, how it behaves, what you'd improve, *be honest* (A + B) | Act 7 |

---

## 0. Pre-flight (do this before you hit record)

- [ ] **Warm the Space.** Open `https://mhussam-ai-ledger-sentinel.hf.space` ~1 min
      before recording so the free instance is awake (cold start is slow).
- [ ] **Tabs, left to right:**
      1. Landing — `https://mhussam-ai-ledger-sentinel.hf.space`
      2. Dashboard — `…hf.space/app.html`
      3. GitHub repo — `https://github.com/mhussam-ai/ledger-sentinel`
      4. ARCHITECTURE.md on GitHub (renders the Mermaid diagrams inline)
- [ ] **Sample files ready** for the drag: `sample_data/` → the 3 receipts +
      `upi_swiggy.txt` + `bank_statement.csv` (keep `bank_statement_drifted.csv`
      aside for Act 4). Pre-select them so the drag is one motion.
- [ ] Record **MOCK MODE** (badge top-right). Instant, free, identical every take.
- [ ] 1080p, hide bookmarks/extensions, browser zoom ~110–125%, close noisy apps.
- [ ] (Optional) one API key (Claude / Gemini / GPT) ready for the live switch in Act 5b.
- [ ] Dry-run once: `cd backend && python -m scripts.run_local` → `3 posted ₹1720,
      3 quarantined, 1 anomaly`. Confidence before the take.

---

## 1. The script

Timings are guides. **[SHOW]** = what's on screen / what to click. The rest is what you say.

### Act 0 — Cold open, on the landing page (0:00–0:25)

**[SHOW]** Landing hero: *"Extract. Verify. Reconcile."* + the green "Live on Hugging Face" pill.

> "Hi, I'm Mohammad Hussam, and this is **Ledger Sentinel**. I'm submitting one
> project for **both tracks** — I designed the architecture *and* shipped the code,
> in one public repo. And everything I'll show you is **live in the browser** — no
> install, no API key."

### Act 1 — The problem: real, hard, scoped (0:25–1:45) — *Problem (A + B)*

**[SHOW]** Scroll slowly through the **Problem** section.

> "This is a problem I live every month *(why it matters to me)*. My spending is
> scattered across card apps, a bank CSV, UPI screenshots, and paper receipts. The
> same coffee shows up twice with two different amounts; OCR quietly turns ₹450 into
> ₹480. **The hard part was never *reading* the documents — it's trusting the merged
> result** *(why it's hard)*. That's literally Damco's own example: *'tracking
> expenses across UPI apps is a nightmare.'*"

> "**How I scoped it** *(scoping)*: I deliberately did **not** build another
> categorizer. I scoped it to the one thing that actually breaks trust — *cross-source
> reconciliation with self-verification* — and drew a hard line: **no number gets
> posted unless it can be proven; anything unproven is quarantined, never guessed.**
> One principle runs through all of it: **the LLM proposes; deterministic code disposes.**"

### Act 2 — (Optional) the 90-second explainer (1:45–2:05)

**[SHOW]** Scroll to the **"90-second tour"** (`#demo`) section. Hit play; let the
hook + title land (~10s), then pause.

> "I also produced a short explainer — embedded right here — but let me show you the
> **real thing, live**."

> *Tip:* don't let the embedded video's narration run under your voice. Sample ~10s,
> then move on. **Cut this act first if you're tight on time.**

### Act 3 — Live demo: the run (2:05–4:15) — *Live Demo (B)*

**[SHOW]** Click **Launch the Dashboard**. Badge top-right reads **MOCK MODE**.

> "The dashboard boots to deterministic mock mode — identical every run, no key."

**[SHOW]** Drag the pre-selected `sample_data` files onto the drop zone. Click **Reconcile**.

- **Beat 1 — parallel fan-out.** *[point at the Extraction panel]*
  > "Each document gets its own worker, firing **in parallel** — latency and a
  > faithfulness score stream in per document."
- **Beat 2 — duplicates collapse.** *[point at a duplicate link on the canvas]*
  > "The bank line and the UPI screenshot for the *same* Swiggy order get linked and
  > collapsed — no double-counting."
- **Beat 3 — THE CATCH (the edge case — the money shot).** *[point at the red ANOMALY card]*
  > "Here's the edge case that matters, and the one a human misses. **Brew & Co**: the
  > receipt says **₹450**, the bank statement says **₹540**. Same purchase, two
  > different numbers. The system **refuses to post either** — it quarantines the
  > conflict with both values as evidence." *(beat of silence)*
- **Beat 4 — low-confidence quarantine.** *[point at the faded receipt in the quarantine lane]*
  > "This faded receipt's numbers don't reconcile — a smudged scan. Self-consistency
  > failed, so it's **quarantined, not guessed**." *(say the name the dashboard shows)*
- **Beat 5 — AgentOps.** *[point at the right-hand panel]*
  > "Every step is **traced, timed, costed, and scored — live**. That's the
  > observability discipline, built in — not bolted on."

**[SHOW]** Bottom bar.

> "Three posted — **₹1720, exact**; three quarantined; nothing posted that couldn't be proven."

### Act 4 — Second failure scenario: self-healing drift (4:15–4:50) — *edge case (B)*

**[SHOW]** Re-run, this time **also** drag `bank_statement_drifted.csv` (headers
renamed to `Txn Date, Narration, Withdrawal`). Point at the amber drift banner.

> "A second failure scenario: a vendor renames every CSV column overnight. A naive
> pipeline corrupts every row. Instead, the **Pandera schema firewall** detects the
> drift, **fuzzy-remaps** the columns at 100% confidence, and **self-heals** — the
> clean rows post anyway. No crash, no corruption."

### Act 5 — How I built it (4:50–6:10) — *System Design / How You Built It (A + B)*

**[SHOW]** *(5a)* Switch to **GitHub** → open **ARCHITECTURE.md**.

> "For how it's built — everything's public. Here's the design doc."

- **[SHOW] §2 system diagram.** "Upload → **parallel fan-out** (one worker per doc) →
  **self-verify** gate → **fan-in** to one canonical model → **reconcile** → POSTED or QUARANTINE."
- **[SHOW] §3 state machine.** "The key choice: it's a **state machine where every
  transition is a guard**, not a suggestion. The model can never *talk its way* into
  POSTED. That's the structural difference between this and a prompt chain — and it's
  what makes the quarantine branches unit-testable."
- **[SHOW] §5 contract.** "One canonical Pydantic `Transaction` is the only thing the
  engine sees, every record carries an evidence trail — that's the reliable AI↔
  structured-data integration."

**[SHOW]** *(5b, optional)* Back to the dashboard ⚙️ settings.

> "And the model is pluggable from the dashboard — pick a provider, paste a key, fetch
> the models it can actually use, save. Real Claude, Gemini, or GPT, same pipeline,
> defaults back to mock. *(If you have a key: switch it, re-run one doc → LIVE badge.)*"

### Act 6 — Tradeoffs: decisions, alternatives, what would change my mind (6:10–7:10) — *Tradeoffs (A)*

**[SHOW]** ARCHITECTURE.md §6 (scale) and §7 (trade-offs).

> "Three decisions I'd defend, with the alternative I rejected:"

- > "**Parallel fan-out over sequential** — sequential is simpler, but latency is the
  > *sum* of documents; fan-out makes it the *max*. The cost is concurrency control,
  > which I bounded with a semaphore."
- > "**Deterministic RapidFuzz matching, not an LLM** — matching has to be fast, cheap,
  > explainable and testable. An LLM matcher would be none of those."
- > "**Decimal, never float** — float arithmetic on currency is a correctness bug."
- > "**On scale**: local and prod are the *same shape* — a stateless API and a worker
  > pool. Going to AWS is swapping the in-memory bus for SQS and the checkpointer for
  > Postgres, not a redesign."
- > "**What would change my mind?** If quarantine *precision* dropped — too many false
  > flags would train operators to rubber-stamp, which is worse than a miss. I'd move
  > the confidence threshold from a constant to a *calibrated* one and let the eval set
  > pick it."

### Act 7 — What's broken + what I'd build next (7:10–8:00) — *Failure Modes / What's Broken (A + B)*

**[SHOW]** ARCHITECTURE.md §8 failure-modes table (and/or the eval scorecard / tests).

> "Being honest about what breaks: vision misreads digits, sources double-count, CSVs
> drift, the model rate-limits — **each has a named guardrail, F1 through F8**, and the
> rule is always *when unsure, quarantine rather than corrupt*. And because it touches
> money, the metric I gate on isn't accuracy — it's **quarantine recall**, locked at
> **1.0** as a hard CI gate across **80 tests**. Regress what the system catches and the
> build goes red."

> "**What I'd improve with more time**: confidence *calibration* against a bigger
> labeled set, and an active-learning loop where every human quarantine-resolution
> becomes a new eval row."

> "One submission, both tracks — the architecture and the shipped, tested code are the
> **same artifact**. It's live at this URL, the code's on GitHub. Thanks for watching."

---

## 2. Links recap (say or show at the end)

- **Live app:** `https://mhussam-ai-ledger-sentinel.hf.space`
- **Code:** `https://github.com/mhussam-ai/ledger-sentinel`
- **Architecture:** `…/blob/main/docs/ARCHITECTURE.md`

## 3. If you run over 10:00, cut in this order

1. **Act 2** — mention the explainer exists; don't play it.
2. **Act 5b** — narrate the provider switch, don't demo it.
3. **Act 6** — keep "what would change my mind" + one tradeoff; drop the rest.

Load-bearing, never cut: the **Brew & Co anomaly (Beat 3)** — the moment the pitch
turns on, give it a beat of silence — and the **state-machine-as-guard** line in Act 5.

## 4. After you record

Upload to **YouTube as _Unlisted_** → that link is the `(link)` attachment. Then see
[SUBMISSION.md](./SUBMISSION.md) for the exact email command and checklist.
