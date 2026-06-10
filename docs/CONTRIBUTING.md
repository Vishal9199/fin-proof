# Contributing & Extending Ledger Sentinel

This guide covers the dev loop, the project's non-negotiable conventions, and the
two extension points the architecture is built around: **adding a model provider**
and **adding a document source** — each is a small, local change because the core
pipeline depends only on contracts, never on concretions.

---

## Development setup

Everything runs on **Python 3.13**, no build step for the frontend.

```bash
git clone https://github.com/mhussam-ai/ledger-sentinel
cd ledger-sentinel/backend

python -m venv .venv
.venv\Scripts\activate            # Windows  ·  source .venv/bin/activate elsewhere
pip install -r requirements.txt

uvicorn app.main:app --reload --port 8000   # → http://localhost:8000 (dashboard + API)
```

The server boots in **mock mode** — no API key, no network — so the full pipeline,
the tests, and the evals all run offline and deterministically.

### The inner loop

```bash
cd backend
pytest -q                    # 80 tests: unit + end-to-end + providers + ingestion + eval gates
python -m evals.run          # the gated eval scorecard (prints PASS/FAIL + diagnostics)
python -m scripts.run_local  # offline terminal demo of a full reconciliation run
```

CI (`.github/workflows/ci.yml`) runs `pytest` **and** the eval scorecard on every
push — a quality regression is a red build, not a silent drift.

---

## Project conventions (the load-bearing ones)

1. **The pipeline never imports a vendor SDK.** Extraction depends on the
   `LLMProvider` contract only. Vendor client libraries are imported *lazily*,
   inside the provider's client factory, so they are optional dependencies and
   the system imports cleanly even when one isn't installed.
2. **Money is `Decimal`, never `float`.** Float arithmetic on currency is a
   correctness bug. Parse to `Decimal` at the boundary (`providers/parsing.py`).
3. **Secrets are write-only.** Never add a field that serializes an API key back
   out. `public_snapshot()` exposes `keys_configured` booleans only.
4. **The model proposes; deterministic code disposes.** New "judgement" belongs
   behind a deterministic guard (a threshold, a schema, a self-consistency check),
   not in a prompt. If the model can "talk its way" into POSTED, it's a bug.
5. **Mock stays deterministic.** The mock provider is the demo *and* the eval
   ground truth *and* the universal degradation target. Keep it exact.
6. **Tests + evals stay green.** `pytest -q` and `python -m evals.run` must pass
   before a change is done.

---

## Extension point 1 — add a model provider (one method)

The high-value logic that must be identical across vendors — the two-read
**self-consistency** guardrail (F1) and **retry/backoff** (F6) — lives once in
`providers/base.py`. A vendor therefore implements exactly **one** primitive,
`complete()`, plus `list_models()` for the dashboard dropdown. Everything else —
the verify gate, the cost meter, the dashboard UI, the mock fallback — comes for
free.

### Step 1 — implement the contract

`backend/app/providers/acme_provider.py`:

```python
"""Acme provider — SDK imported lazily so it stays an optional dependency."""
from __future__ import annotations

from .base import LLMProvider, LLMResponse


class AcmeProvider(LLMProvider):
    id = "acme"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            from acme_sdk import AsyncAcme   # lazy: only imported if Acme is used
            self._client = AsyncAcme(api_key=self._api_key)
        return self._client

    async def complete(self, *, model, prompt, system=None, max_tokens=512) -> LLMResponse:
        resp = await self._get_client().generate(
            model=model, input=prompt, system=system, max_tokens=max_tokens,
        )
        return LLMResponse(
            text=resp.output_text,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            model=model,
        )

    async def list_models(self) -> list[tuple[str, str]]:
        models = await self._get_client().models.list()
        return [(m.id, getattr(m, "display_name", None) or m.id) for m in models]
```

That's the entire vendor-specific surface. You do **not** write extraction,
self-consistency, retry, confidence, or quarantine logic — `base.py` already
applies all of it to your `complete()`.

**Optional — real-document support.** If the vendor can read images/PDFs, also
override `complete_multimodal()` (prompt + `Attachment` list → `LLMResponse`;
base64 the bytes into whatever envelope the vendor wants) and set
`supports_attachments = True`. The shared visual/statement extraction in
`base.py` — two-read self-consistency, non-financial classification (F10), the
per-row statement re-read (F11) — then applies to your vendor unchanged. Without
it, image and scanned-PDF uploads are quarantined with a clear reason when your
provider is selected (F9); text documents still work fully.

### Step 2 — register it in the factory

`backend/app/providers/__init__.py`:

```python
from .acme_provider import AcmeProvider

_BUILDERS: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "google": GoogleProvider,
    "openai": OpenAIProvider,
    "acme": AcmeProvider,        # ← add this line
    "mock": MockProvider,
}
```

### Step 3 — add it to the catalog (drives the UI + the cost meter)

`backend/app/providers/catalog.py` → add a `ProviderInfo` to `PROVIDER_INFO`:

```python
"acme": ProviderInfo(
    id="acme",
    label="Acme · Nova",
    requires_key=True,
    key_env=("ACME_API_KEY",),          # documentation only — keys come from the UI
    default_fast="nova-flash",
    default_deep="nova-pro",
    docs_url="https://acme.example/keys",
    models=(
        ModelInfo("nova-pro",   "Nova Pro",   5.0, 15.0),   # USD / 1M tokens (in, out)
        ModelInfo("nova-flash", "Nova Flash", 0.5,  1.5),
    ),
),
```

### Step 4 (optional) — pin the SDK

Add `acme-sdk==x.y.z` to `backend/requirements.txt`. It stays optional: mock mode
and the other providers keep working without it.

**Done.** The new provider now appears in the dashboard's provider list; pasting a
key and clicking **Fetch models** calls your `list_models()`; the two-tier routing,
self-consistency, retry/backoff, cost attribution, and mock fallback all apply
automatically. Add a fake-client test alongside `backend/tests/test_providers.py`
(it stubs the client so no network is touched) and you're green.

---

## Extension point 2 — add a document source

Because the reconciliation engine sees **only** the canonical `Transaction`
contract (`schemas.py`), a new source type is additive:

1. Add the literal to `SourceType` (e.g. `"pdf_invoice"`).
2. Add a worker in `backend/app/extraction/` that returns a `Transaction` (or a
   list) with per-field `confidence` and an `evidence` trail. Route to it from the
   type router.
3. The verify gate, reconciliation, quarantine routing, tracing, and evals work
   unchanged — they operate on the contract, not the format.

Add a golden example to `backend/evals/dataset.py` so the new source is covered by
the gated scorecard.

---

## Submitting a change

- Keep the diff focused; match the surrounding style (type hints, and docstrings
  that explain *why*, not *what*).
- Run `pytest -q` and `python -m evals.run` — both green.
- If you changed extraction, reconciliation, or thresholds, add/adjust a golden
  row so the evals still *prove* the behavior.
- PRs to `main`. CI must be green before merge.
