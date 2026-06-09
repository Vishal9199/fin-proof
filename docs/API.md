# Ledger Sentinel — API Reference

The backend is a single FastAPI service that exposes three surfaces:

1. **Reconciliation** — stage documents, launch a run, stream/poll the result.
2. **Control plane** — choose the model provider, key, and model at runtime.
3. **Health** — liveness and the current effective mode.

It is **single-origin**: the same process also serves the dashboard, so in a
deployed instance the API base is just the site's own origin (no CORS, no second
host). Interactive OpenAPI docs are always available at **`/docs`**, and the raw
schema at **`/openapi.json`**.

- **Base URL (local):** `http://localhost:8000`
- **Base URL (deployed):** the site's own origin — this instance is live at `https://mhussam-ai-ledger-sentinel.hf.space`
- **Content types:** `application/json` everywhere except `POST /reconcile` (`multipart/form-data`).

---

## Conventions

**Authentication.** Read endpoints are open. The **key-bearing control-plane
writes** (`PUT /config`, `POST /config/test`, `POST /providers/{id}/models`) are
gated *only if* the server is started with a `LEDGER_ADMIN_TOKEN`; when set, send
it as a header. Unset (the local/demo default) → fully open.

```
X-Admin-Token: <token>      # required only when LEDGER_ADMIN_TOKEN is configured
```

**Errors.** Failures return a JSON body and never a bare stack trace:

```json
{ "detail": "Human-readable reason." }
```

Live provider calls that fail (bad key, unreachable vendor) are reported as
**data, not HTTP errors** — `POST /config/test` and `POST /providers/{id}/models`
return `200` with `{"ok": false, "error": "..."}` so the dashboard can show the
reason inline.

**Safe by default.** The server always boots in deterministic **mock mode**. A
provider selected without a key transparently *collapses to mock* rather than
erroring (see `effective_provider`), so the API is always callable.

| Status | Meaning |
|---|---|
| `200` | OK |
| `202` | Run accepted and still in progress (poll `GET /runs/{id}`) |
| `400` | Invalid input (bad provider id, out-of-range threshold, no files) |
| `401` | Admin token required/incorrect (only when `LEDGER_ADMIN_TOKEN` is set) |
| `404` | Unknown `run_id` |
| `500` | The run failed (body carries the reason) |

---

## Reconciliation

### `POST /reconcile`
Stage a pile of documents and **launch the run immediately** as a background task.
Returns as soon as the run is accepted — execution does not wait for anyone to
watch the stream.

- **Body:** `multipart/form-data` with one or more `files`.
- **Returns:** `200`

```json
{ "run_id": "run_1a2b3c4d", "documents": 5 }
```

```bash
curl -s -X POST http://localhost:8000/reconcile \
  -F "files=@sample_data/receipts/brew_co_receipt.txt" \
  -F "files=@sample_data/bank_statement.csv" \
  -F "files=@sample_data/upi/upi_swiggy.txt"
```

### `GET /events/{run_id}`  · Server-Sent Events
Tails a run. On connect it **replays the full event history** from the run's
buffer, then streams live until a terminal event. This is why a dashboard that
connects late — or reconnects — still sees the whole run. (If SSE can't connect
at all, fall back to polling `GET /runs/{id}`.)

- **Returns:** `text/event-stream` (`404` if the `run_id` was never staged).

Each message is an SSE frame: `event: <type>` + `data: <json>`.

| Event | When | Payload (keys) |
|---|---|---|
| `run.started` | Run begins | `documents` |
| `agent.cell.start` | A document's worker starts | `doc` |
| `agent.cell.done` | A worker finishes | `doc, worker, latency_ms, model, faithfulness, count, ok` |
| `trace` | Any node emits an AgentOps span | `span, model, latency_ms, tokens_in, tokens_out, usd_cost, faithfulness` |
| `txn.verified` | A transaction passed the verify gate | transaction fields |
| `txn.posted` | A transaction was POSTED | transaction fields |
| `txn.quarantined` | A transaction was QUARANTINED | transaction fields + `quarantine_reason` |
| `run.completed` | **Terminal** — run finished | `posted, quarantined, links, total_posted_amount, documents, duration_ms` |
| `run.failed` | **Terminal** — run errored | `error` |

```bash
curl -N http://localhost:8000/events/run_1a2b3c4d
```

### `GET /runs/{run_id}`
The final, durable result — fetchable even if the client never opened the stream.

- **Returns:** `200` with a [`RunResult`](#runresult); `202` while still running;
  `404` if unknown; `500` if the run failed.

### `GET /runs/{run_id}/status`
Lightweight lifecycle probe for a polling client.

```json
{ "run_id": "run_1a2b3c4d", "state": "running", "documents": 5 }
```
`state` ∈ `queued · running · completed · failed`.

---

## Control plane (runtime model configuration)

The model stack is decided **entirely here at runtime**, never from the
environment. Flow: **select provider → supply key → fetch the models that key can
actually use → pick one → save**. Secrets are write-only — they are never
returned by any endpoint.

### `GET /providers`
The catalog the dashboard renders: every provider, its fallback model list,
pricing, and whether a key is configured.

```json
{
  "selected": "mock",
  "effective": "mock",
  "providers": [
    {
      "id": "anthropic", "label": "Anthropic · Claude",
      "requires_key": true, "key_configured": false,
      "default_fast": "claude-haiku-4-5-20251001",
      "default_deep": "claude-opus-4-8",
      "selected_fast": "claude-haiku-4-5-20251001",
      "selected_deep": "claude-opus-4-8",
      "docs_url": "https://console.anthropic.com/settings/keys",
      "models": [ { "id": "claude-opus-4-8", "label": "Claude Opus 4.8",
                    "price_in": 15.0, "price_out": 75.0 } ]
    }
  ]
}
```

### `POST /providers/{provider}/models`  · 🔒 admin
Ask the **vendor** which models the supplied key can actually use — this powers
the model dropdown (no error-prone free text). Never persists anything.

- **Body:** `{ "api_key": "sk-..." }` (optional — falls back to the stored key).
- **Returns:** `200`

```json
{ "ok": true, "provider": "anthropic",
  "models": [ { "id": "claude-opus-4-8", "label": "Claude Opus 4.8" } ] }
```
On a bad key it still returns `200`, as `{ "ok": false, "provider": "...", "models": [], "error": "..." }`.

### `GET /config`
The current runtime picture — a **secret-safe snapshot** (`keys_configured`
booleans only, never a key).

```json
{
  "provider": "mock", "effective_provider": "mock", "provider_label": "Mock",
  "mock_mode": true,
  "fast_model": "mock", "deep_model": "mock",
  "models": { "anthropic": { "fast": "...", "deep": "..." } },
  "keys_configured": { "anthropic": false, "google": false, "openai": false, "mock": true },
  "confidence_threshold": 0.8, "match_threshold": 0.82, "max_concurrency": 8
}
```

### `PUT /config`  · 🔒 admin
Apply a **partial** configuration change atomically; returns the new snapshot.
A blank `api_key` means "leave unchanged" (saving the form never wipes a stored
secret). Model/key edits target the provider being set, or the current one.

| Field | Type | Notes |
|---|---|---|
| `provider` | string | `anthropic · google · openai · mock` |
| `api_key` | string | write-only; blank = unchanged; ignored for `mock` |
| `fast_model` | string | model id for clean documents |
| `deep_model` | string | model id for the escalated/ambiguous path |
| `confidence_threshold` | float | `0..1` quarantine gate τ |
| `match_threshold` | float | `0..1` reconciliation fuzzy-match cutoff |
| `max_concurrency` | int | `≥ 1` fan-out ceiling |

```bash
curl -s -X PUT http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $LEDGER_ADMIN_TOKEN" \
  -d '{"provider":"anthropic","api_key":"sk-ant-...","fast_model":"claude-haiku-4-5-20251001"}'
```

### `POST /config/test`  · 🔒 admin
Validate a provider/key/model with one tiny live call (a `pong` round-trip).
Never persisted — lets the UI fail a bad key fast instead of silently degrading.

```json
{ "ok": true, "provider": "anthropic", "model": "claude-haiku-4-5-20251001",
  "latency_ms": 412, "detail": "pong" }
```

---

## Health

### `GET /health`
Liveness + the effective mode (what the pipeline will actually use right now).

```json
{
  "status": "ok", "version": "1.1.0",
  "mock_mode": true, "provider": "mock", "provider_label": "Mock",
  "model": "mock", "langfuse": false, "max_concurrency": 8
}
```

---

## Data models

Canonical shapes (Pydantic; see [`backend/app/schemas.py`](../backend/app/schemas.py)).
`amount` is always a **`Decimal`-precise string** in JSON — money is never a float.

### `Transaction`
```jsonc
{
  "id": "txn_...", "source_doc": "brew_co_receipt.txt",
  "source_type": "receipt | bank_csv | upi_screenshot",
  "merchant": "Brew & Co", "amount": "450.00", "currency": "INR",
  "txn_date": "2026-05-31",
  "confidence": { "amount": { "value": 0.97, "method": "self_consistency" } },
  "state": "EXTRACTED | VERIFIED | MATCHED | POSTED | QUARANTINE",
  "evidence": ["row 4", "crop:amount"],
  "quarantine_reason": "Amount mismatch: receipt 450 vs statement 540"
}
```

### `MatchLink`
```jsonc
{ "kind": "link | duplicate | anomaly", "txn_ids": ["txn_a","txn_b"],
  "score": 0.94, "detail": "Swiggy · bank line ↔ UPI screenshot" }
```

### `RunResult`
```jsonc
{
  "run_id": "run_1a2b3c4d",
  "posted":      [ /* Transaction[] */ ],
  "quarantined": [ /* Transaction[] */ ],
  "links":       [ /* MatchLink[]   */ ],
  "total_posted_amount": "1720.00",
  "documents": 5, "duration_ms": 84
}
```

---

## End-to-end with `curl`

```bash
BASE=http://localhost:8000

# 1. Launch a run
RUN=$(curl -s -X POST $BASE/reconcile \
  -F "files=@sample_data/receipts/brew_co_receipt.txt" \
  -F "files=@sample_data/bank_statement.csv" | python -c "import sys,json;print(json.load(sys.stdin)['run_id'])")

# 2a. Watch it live (Ctrl-C after run.completed)
curl -N $BASE/events/$RUN

# 2b. …or just poll for the durable result
curl -s $BASE/runs/$RUN | python -m json.tool
```

> The mock-mode golden run posts **3** transactions totalling **₹1720**,
> quarantines **3** (one cross-source amount conflict, one low-confidence read),
> and is fully deterministic — identical on every call.
