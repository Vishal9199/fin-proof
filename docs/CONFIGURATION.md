# Configuration Reference

FinProof separates **two kinds of configuration** by design:

| | **Ops config** | **Model config** |
|---|---|---|
| What | thresholds, concurrency, retries, admin token, observability | provider · API key · fast/deep model |
| Where | environment variables (`.env`) — see below | the **dashboard** ⚙️ at runtime (`PUT /config`) |
| Source | [`backend/app/config.py`](../backend/app/config.py) | [`backend/app/runtime.py`](../backend/app/runtime.py) |
| Lifecycle | read once at startup | mutable live, no restart |

**The model stack is *never* configured from the environment.** The app always
boots in deterministic **mock mode** and stays there until an operator selects a
provider, pastes a key, and picks a model in the UI. This keeps the default safe
and reproducible — an ambient `OPENAI_API_KEY` in the shell can never silently
change what the agent does. See [ARCHITECTURE §7.5](./ARCHITECTURE.md#75-pluggable-model-providers--the-runtime-control-plane).

---

## Ops config — environment variables

All optional; every one has a safe default, so the app runs with an empty
environment. Set them in `.env` (git-ignored) or your platform's env settings.

| Variable | Default | Purpose |
|---|---|---|
| `LEDGER_ADMIN_TOKEN` | `""` (open) | If set, the key-bearing control-plane writes (`PUT /config`, `POST /config/test`, `POST /providers/{id}/models`) require an `X-Admin-Token: <token>` header. **Set this on any public deployment.** |
| `LEDGER_CONFIDENCE_THRESHOLD` | `0.80` | Extractions whose confidence is below this route to the **QUARANTINE** lane (the `τ` gate). Range `0–1`. |
| `LEDGER_MATCH_THRESHOLD` | `0.82` | Reconciliation fuzzy-match acceptance cutoff. Lower = more aggressive linking. Range `0–1`. |
| `LEDGER_MAX_CONCURRENCY` | `8` | Max extraction workers fanned out concurrently (a semaphore that protects the model rate limit). |
| `LEDGER_MAX_RETRIES` | `3` | Attempts for a transient model failure (429/5xx/timeout) before degrading to the deterministic parser (F6). |
| `LEDGER_RETRY_BASE_DELAY` | `0.5` | Base seconds for exponential backoff + jitter between retries. |
| `LEDGER_MAX_UPLOAD_MB` | `15` | Per-file upload size cap; an oversized file is rejected with a structured `413` naming the file (F13). |
| `LEDGER_MAX_FILES` | `40` | Per-run document count cap (`400` when exceeded). |
| `LEDGER_MAX_PDF_PAGES` | `40` | Page ceiling for the PDF lane. An over-cap PDF is **quarantined with a reason**, never silently truncated — a partial statement is a corrupt ledger. |
| `LEDGER_REDACT_PII` | `true` | Mask account numbers, PAN, IFSC, Aadhaar-style groups, phones, and emails (keeping the last 4 chars) before document **text** is sent to a live provider (F12). Mock mode never sends bytes anywhere; pixels can't be masked (see [SECURITY.md](./SECURITY.md)). |
| `LANGFUSE_PUBLIC_KEY` | `""` | Enables Langfuse trace persistence **only if** both keys are set; otherwise traces stay in-process and still render on the AgentOps tab. |
| `LANGFUSE_SECRET_KEY` | `""` | — |
| `LANGFUSE_HOST` | `https://cloud.langfuse.com` | Langfuse endpoint (self-hosted or cloud). |

> The thresholds and concurrency above **seed** the runtime defaults; once running,
> they're also tunable live from the dashboard (which overrides the seed for that
> process). Retry policy and the admin token are ops-only.

### Deployment-only variables
These are read by the container/host, not by application logic:

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `7860` | Port the server binds (`uvicorn ... --port ${PORT:-7860}`). Honored by Render/Cloud Run/Heroku; Hugging Face routes to `app_port: 7860`. |
| `LEDGER_FRONTEND_DIR` | repo `frontend/` | Absolute path to the static dashboard the API serves (the [root Dockerfile](../Dockerfile) sets `/app/frontend`). Unset → resolved relative to the source tree for local `uvicorn`. |

---

## Model config — set from the dashboard (runtime)

Chosen at runtime via the Settings modal ⚙️ (`PUT /config`); see the
[API reference](./API.md#control-plane-runtime-model-configuration).

| Field | Notes |
|---|---|
| `provider` | `anthropic · google · openai · mock`. Default `mock`. |
| `api_key` | **Write-only** — never returned by any endpoint. Blank on save = "leave unchanged". Ignored for `mock`. |
| `fast_model` | Model for clean documents (the p50 path). |
| `deep_model` | Model for the ambiguous/escalated path. |
| `confidence_threshold` / `match_threshold` | Live overrides of the gates above (`0–1`). |
| `max_concurrency` | Live override of the fan-out ceiling (`≥ 1`). |

**Safe-by-default behaviors:**
- A provider selected **without a key collapses to mock** (`effective_provider`) — a
  misconfiguration degrades to the deterministic demo, never a 500.
- Models are **discovered, not typed**: after a key is supplied, the dashboard calls
  `POST /providers/{id}/models` to fetch the models that key can actually use
  (filtered to text/vision-capable models) and offers them in a dropdown.
- **Secrets are write-only**: `GET /config` returns `keys_configured` booleans, never
  a key.

---

## Quick recipes

```bash
# Fully open local demo — nothing to configure (boots to mock).
uvicorn app.main:app --port 8000

# Gate the control plane + tighten the quarantine threshold.
# .env:
LEDGER_ADMIN_TOKEN=choose-a-strong-token
LEDGER_CONFIDENCE_THRESHOLD=0.85

# Persist traces to Langfuse.
# .env:
LANGFUSE_PUBLIC_KEY=pk-...
LANGFUSE_SECRET_KEY=sk-...
```

Provider/key/model are then set in the dashboard — see
[DEPLOY.md](./DEPLOY.md) for production and [TROUBLESHOOTING.md](./TROUBLESHOOTING.md)
if something doesn't behave.
