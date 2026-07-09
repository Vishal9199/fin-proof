# Troubleshooting

Symptom → cause → fix, for the issues you're most likely to hit. The system is
built to **degrade safely** (a misconfiguration becomes mock mode, not a crash),
so most of these are "it ran, but not the way I expected."

---

## Setup & running

### `ModuleNotFoundError: No module named 'pandera'` (or `fastapi`, `google.genai`, …)
**Cause:** you launched `uvicorn` from a Python that isn't the project venv —
typically a **global `uvicorn`** on your `PATH`. The app imports cleanly and even
serves pages, then fails the moment a CSV is processed (the Pandera import is lazy).

**Fix:** run from the venv explicitly so the right interpreter is used:
```powershell
cd fin-proof/backend
.\.venv\Scripts\Activate.ps1                     # or: source .venv/bin/activate
python -m uvicorn app.main:app --port 8000       # `python -m` can't pick the global uvicorn
```

### The page loads but nothing is styled / 404s on `/styles.css`
**Cause:** the dashboard is served by the **same** FastAPI process (single origin).
If you opened the raw `app.html` file from disk (`file://`), the relative asset/API
paths don't resolve.

**Fix:** open it through the server: **http://localhost:8000/** (landing) and
**/app.html** (dashboard).

### `GET /favicon.ico → 404`
Harmless — there's no favicon. Ignore it.

---

## Live model providers

### Badge says **MOCK MODE** even though I selected a provider
**Cause:** the provider needs a key and none (or a wrong one) is configured, so the
runtime **collapses to mock** by design (`effective_provider`) rather than erroring.

**Fix:** open ⚙️, paste a valid key, click **Test connection** (it does one tiny live
call and reports the exact error if the key is bad), then **Save**. The badge flips to
`LIVE · <provider>`.

### A run "succeeds" but every trace says `mock` / `<provider>→mock` with **0 tok**
**Cause:** the live extraction **failed and degraded to the deterministic parser
(F6)**. The `<provider>→mock` label (e.g. `google→mock`) is the tell that a real call
was attempted and fell back. The console has the reason (`WARNING ledger.vision …
extraction failed … Traceback`).

**Common root cause (Gemini):** *thinking models* (e.g. `gemini-3.x`, `*-pro-preview`)
consumed the whole output budget on internal reasoning and returned **empty text**
(`finish_reason=MAX_TOKENS`), so JSON parsing failed. This is handled — the Google
adapter floors `max_output_tokens` at 2048. If you still see it, the doc may be large;
pick a lighter model or raise the floor.

### Live results differ from mock mode (e.g. CAFE ZEST posts instead of quarantining)
**Not a bug.** Mock mode is **deterministic**; a live LLM is not. The deterministic
parser quarantines CAFE ZEST because its line-items don't sum to the total, whereas a
live model may read the amount confidently and post it.

**Fix:** for the **recorded demo, use MOCK mode** — it's instant, free, and the
quarantine/anomaly beats fire identically every take. Use a live provider to *prove
the switch works*, not for the scripted beats.

### `EST. COST` shows **$0.0000** even with real tokens
**Cause:** the chosen model isn't in the pricing catalog (often a dated/preview id like
`gemini-3.1-pro-preview-customtools`), so it estimates $0 (the meter under-reports
rather than guessing high). CSV extraction and mock are *always* $0 (no LLM call).

**Fix:** pick a **catalogued** model (`gemini-2.5-flash`/`pro`, etc.) for an exact
figure — a family fallback already estimates most preview variants. To add exact prices,
edit [`catalog.py`](../backend/app/providers/catalog.py).

### The model dropdown shows image/music/robotics/TTS models
**Cause:** stale fetch — a key can unlock many non-text models. The list is filtered to
**text/vision** models that can actually extract.

**Fix:** click **Fetch models** again after updating the key. Pick a general model
(`Gemini Flash Latest` for speed, `Gemini Pro Latest` for hard docs).

### Live Gemini runs are slow (8–12 s per document)
**Cause:** Pro **thinking** models reason before answering.

**Fix:** use a **Flash** model for the demo; the CSV path stays at ~5 ms (no LLM).

### `401` when saving config / fetching models
**Cause:** `LEDGER_ADMIN_TOKEN` is set, so the control-plane writes are gated.

**Fix:** send the header `X-Admin-Token: <token>` (the dashboard prompts for it), or
unset the token for an open local demo. See [CONFIGURATION.md](./CONFIGURATION.md).

---

## Dashboard & uploads

### My photo / screenshot / scanned PDF came back QUARANTINED in mock mode
**Cause:** working as designed (F9). Mock mode is fully offline and deterministic —
it reads **no pixels** and refuses to invent a number for an image or a scanned PDF
(one with no text layer). The quarantine reason on the card says exactly this.

**Fix:** open ⚙️, configure a vision-capable provider (Anthropic / Google / OpenAI)
and re-run: images ride native vision blocks and scanned PDFs are sent to the model
as native PDF attachments. Born-digital PDFs and text receipts extract fine in mock
mode without any key.

### A statement PDF was quarantined with "over the N-page cap"
**Cause:** the PDF exceeds `LEDGER_MAX_PDF_PAGES` (default 40). It is refused with a
reason rather than silently truncated — a partial statement would corrupt the ledger.

**Fix:** raise `LEDGER_MAX_PDF_PAGES` in `.env` (mind provider input limits and cost:
the whole document is read twice for the per-row self-consistency re-read), or split
the PDF.

### A random image posted nothing and says "classified as non-financial"
**Cause:** working as designed (F10) — the vision pass classifies the document first;
anything that isn't a receipt / UPI payment / statement is quarantined instead of
being hallucinated into a transaction. The model's reading is kept in the evidence
trail for review.

### Upload rejected with 413 / "Too many documents"
**Cause:** the upload guards (F13): per-file size cap `LEDGER_MAX_UPLOAD_MB`
(default 15 MB) and per-run count cap `LEDGER_MAX_FILES` (default 40).

**Fix:** raise the env vars, or trim the pile. Empty files are rejected with a 400.

### "Could not reach the API at …" after dropping a folder
**Cause (historic):** a dropped *folder* used to arrive as one unreadable directory
entry, which failed the upload. This is fixed — the dropzone now recurses into folders.

**If you still see it:** the backend isn't reachable. Confirm `uvicorn` is running and
you're on **http://localhost:8000** (single origin — no separate `:5173` UI anymore).

### Nothing streams live / the grid sits empty for a moment
**Not a failure.** Execution is decoupled from the stream; if SSE can't establish (a
proxy, say), the client transparently **falls back to polling** `/runs/{id}`. The result
always renders. A genuinely failed run shows a `run.failed` message, never an infinite
spinner.

### I changed `app.js`/`styles.css` but the dashboard looks the same
**Cause:** the browser cached the JS/CSS. (`uvicorn --reload` only restarts the backend.)

**Fix:** **hard refresh** — `Ctrl+F5` (or `Cmd+Shift+R`).

---

## Deployment (Hugging Face Space / Render / Cloud Run)

### First request after idle takes ~30–60 s
**Cause:** free instances **sleep** when idle and cold-start on the next request.

**Fix:** open the URL (or hit `/health`) ~1 minute before demoing; or point a free
uptime monitor at `/health` every few minutes. See [DEPLOY.md](./DEPLOY.md).

### The README hero image / a doc link 404s on GitHub
**Cause:** a relative path that doesn't resolve from its file's location (docs live in
`docs/`, so links to root files need `../`).

**Fix:** root files from a doc → `../README.md`, `../backend/...`; docs → each other →
`./OTHER.md`; the README → docs → `./docs/...`.

### Port already in use
Local binds **8000**; the container binds **7860** (mapped to host 8000 by
`docker-compose`). Change the host port in `docker-compose.yml` or the `--port` flag.

---

Still stuck? Reproduce in **mock mode** first (no key, no network) to isolate whether
it's the pipeline or the provider — that single step localizes most issues.
