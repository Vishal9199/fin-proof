# FinProof — AI Usage Journal

How AI tools were used **to build** this project — the prompts, the iterations,
and what changed based on the output.

---

## Phase 1 — Problem Definition

**Tools used:** Claude (Anthropic), ChatGPT (OpenAI)

### Prompt used to refine the problem statement:
```
I want to build an AI project for a certification. The domain is personal finance
reconciliation — matching receipts, bank statements, and UPI screenshots.
What are the hardest failure modes I should design around?
What separates a "script" solution from a "system" solution here?
```

**What the AI surfaced that I hadn't considered:**
- Silent digit misreads (₹450 → ₹480) from vision models — led to the
  two-read self-consistency guardrail
- CSV schema drift (banks rename headers without warning) — led to Pandera contract
- The duplicate-vs-conflict distinction (same purchase appearing twice = duplicate;
  same purchase with different amounts = anomaly requiring quarantine)

**How it changed the design:**
Moved from "build an OCR + categorization script" to "build a pipeline with
explicit trust verification at every stage."

---

## Phase 2 — Architecture Design

**Tools used:** Claude, GitHub Copilot

### Prompt for state machine design:
```
I want to model a document reconciliation pipeline as a state machine.
States are: EXTRACTED, VERIFIED, MATCHED, POSTED, QUARANTINE.
What transitions should be guarded, and what condition triggers each?
Draw this as a Mermaid diagram.
```

**Output used:** The Mermaid state diagram in `docs/ARCHITECTURE.md` was
derived directly from this prompt's output and refined over 3 iterations.

### Prompt for LangGraph decision:
```
I need an orchestration framework for a multi-step agentic pipeline in Python.
It needs: parallel fan-out, state passing between nodes, checkpointing.
Compare LangGraph, Celery, and asyncio directly. When would you choose each?
```

**Decision made:** LangGraph — gave state machine semantics "for free" and a
replay buffer that solved the late-connecting dashboard problem.

---

## Phase 3 — Prompt Engineering (for the app's own prompts)

**Tools used:** Claude

### Developing `_EXTRACT_PROMPT` (receipt extraction):

**Iteration 1:**
```
Extract the merchant name, amount, and date from this receipt.
```
Problem: model returned free-form text, not parseable.

**Iteration 2:**
```
Extract this receipt as JSON: {"merchant": ..., "amount": ..., "date": ...}
```
Problem: model sometimes returned `"amount": "₹450.00"` (string), sometimes
`450.0` (number). Parser needed to handle both.

**Iteration 3 (final):**
```
Extract this receipt as JSON with keys merchant (string),
amount (number, the final total paid), date (YYYY-MM-DD).
Return ONLY JSON.
```
Added type hints inline and "Return ONLY JSON" to prevent explanatory prose.
Result: 100% parseable output on all test cases.

### Developing the self-consistency recheck prompt:

**Problem encountered:** The recheck prompt originally mirrored the extract
prompt. The model's second answer was biased toward its first — not truly
independent. Shortened to:
```
Return only the final total amount as a number.
```
Independence improved significantly; the disagreement detection became meaningful.

---

## Phase 4 — Code Implementation

**Tools used:** GitHub Copilot (inline), Claude (design review)

### Used Copilot for:
- Boilerplate FastAPI route signatures
- Pandera schema definition for bank CSV validation
- RapidFuzz fuzzy-match integration for merchant name comparison

### Used Claude for design review:
```
Here is my provider base class. The goal is: every vendor implements only
complete(), and the self-consistency guardrail + retry logic lives once in
the base. Review for correctness and any edge cases I'm missing.
[pasted base.py]
```

**Changes made based on review:**
- Added `ProviderCapabilityError` (separate from `TransientProviderError`) so
  vision failures on text-only providers are never retried — they're routed
  to quarantine immediately
- Added `_status_of()` helper for SDK-agnostic HTTP status code extraction

---

## Phase 5 — Testing & Evals

**Tools used:** Claude

### Prompt for golden dataset design:
```
I have a reconciliation pipeline. I need a golden eval dataset that tests:
quarantine recall (did it catch everything unsafe?),
amount exactness (did it extract the right numbers?),
link F1 (did it correctly identify duplicates vs anomalies?).
What should my labeled examples look like?
```

**Output:** The structure of `backend/evals/dataset.py` — labeled ground-truth
entries with expected state (POSTED/QUARANTINE) and expected links.

---

## Summary — AI Contribution to the Build

| Phase | AI tool | Contribution |
|---|---|---|
| Problem framing | Claude | Identified self-verification and schema-drift as key failure modes |
| Architecture | Claude | State machine design, LangGraph decision rationale |
| Prompt design | Claude | 3+ iterations per prompt, independence of recheck prompt |
| Code review | Claude | ProviderCapabilityError split, status-code helper |
| Boilerplate | Copilot | Route signatures, schema definitions, fuzzy-match wiring |
| Eval design | Claude | Golden dataset structure and metric definitions |
