"""FinProof API.

Endpoints:
    POST /reconcile          stage a pile of documents, launch the run, return run_id
    GET  /events/{run_id}    SSE stream that *tails* the run (full replay on connect)
    GET  /runs/{run_id}      the final RunResult (posted / quarantined / links)
    GET  /runs/{run_id}/status   lightweight lifecycle status (for polling fallback)
    GET  /health             liveness + mode

Design note — why the run is launched on POST, not on SSE-subscribe:
    Execution is fully decoupled from whether a browser is watching. The POST
    handler kicks off `process_run` as a background task immediately, and the SSE
    endpoint is a pure *tailer* that replays history then streams live (see
    events.EventBus). This removes a whole class of "the dashboard connected a
    moment too late, so nothing ever ran / it hangs forever" failures, and means
    /runs/{id} eventually returns a result even if the client never opened SSE at
    all. The fan-out itself lives in `process_run`: every document is extracted
    concurrently under a bounded semaphore (protecting the model rate limit),
    then fed into the LangGraph reconciliation machine.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .config import get_settings
from .events import bus
from .extraction import UploadedDoc, extract_document
from .graph.reconciliation import run_reconciliation
from .providers import build_provider
from .providers.catalog import PROVIDER_INFO
from .runtime import ConfigError, get_runtime, update_runtime
from .schemas import RunResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s · %(message)s",
)
log = logging.getLogger("ledger")

app = FastAPI(title="FinProof", version="1.1.0")

# CORS is added *outermost* so its headers are present on every response —
# including error responses produced by the exception handler below. Without
# that, a 500 would reach the browser stripped of CORS headers and surface in the
# UI as the misleading "could not reach the API".
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Run-scoped state. Bounded so a long-lived server cannot leak across many runs.
_MAX_RUNS = 512
_staged: "OrderedDict[str, list[UploadedDoc]]" = OrderedDict()
_results: "OrderedDict[str, RunResult]" = OrderedDict()
_status: "OrderedDict[str, dict]" = OrderedDict()


def _remember(store: OrderedDict, key: str, value) -> None:
    store[key] = value
    store.move_to_end(key)
    while len(store) > _MAX_RUNS:
        store.popitem(last=False)


@app.middleware("http")
async def access_log(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    dur_ms = int((time.perf_counter() - started) * 1000)
    log.info("%s %s → %s (%dms)", request.method, request.url.path, response.status_code, dur_ms)
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Never leak a bare stack trace as a non-JSON 500 (which the browser would
    report as 'unreachable'). Always return structured JSON; CORS headers are
    re-applied by the middleware on the way out."""
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal error.", "error": str(exc)})


@app.get("/health")
async def health() -> dict:
    s = get_settings()
    rt = get_runtime()
    return {
        "status": "ok",
        "version": app.version,
        "mock_mode": rt.mock_mode,
        "provider": rt.effective_provider,
        "provider_label": rt.provider_label,
        "model": rt.active_fast_model,
        "langfuse": s.langfuse_enabled,
        "max_concurrency": rt.max_concurrency,
    }


# ── Control plane: provider / model / key configuration ────────────────────────
def _require_admin(request: Request) -> None:
    """Gate configuration writes on the admin token *if one is configured*.

    Unset (the local/demo default) → open. This is a lightweight stand-in for the
    RBAC a production control plane would enforce on a sensitive, key-bearing
    endpoint; the check lives here so secrets are never writable anonymously when
    an operator has opted into protecting them."""
    token = get_settings().ledger_admin_token
    if token and request.headers.get("x-admin-token") != token:
        raise HTTPException(401, "A valid X-Admin-Token header is required to change configuration.")


@app.get("/providers")
async def list_providers() -> dict:
    """Catalog for the dashboard: providers, their models, pricing, key status."""
    rt = get_runtime()
    return {
        "selected": rt.provider,
        "effective": rt.effective_provider,
        "providers": [
            {
                "id": info.id,
                "label": info.label,
                "requires_key": info.requires_key,
                "key_configured": rt.key_configured(info.id),
                "default_fast": info.default_fast,
                "default_deep": info.default_deep,
                "selected_fast": rt.fast_models.get(info.id) or info.default_fast,
                "selected_deep": rt.deep_models.get(info.id) or info.default_deep,
                "docs_url": info.docs_url,
                "models": [
                    {"id": m.id, "label": m.label, "price_in": m.price_in, "price_out": m.price_out}
                    for m in info.models
                ],
            }
            for info in PROVIDER_INFO.values()
        ],
    }


class ModelsRequest(BaseModel):
    api_key: str | None = None


@app.post("/providers/{provider}/models")
async def fetch_models(provider: str, body: ModelsRequest, request: Request) -> dict:
    """Fetch the live model list a key can actually use — this populates the
    dashboard's model dropdown (no error-prone free text). Uses the supplied key
    or the one already configured for the provider; never persists anything."""
    _require_admin(request)
    pid = provider.strip().lower()
    info = PROVIDER_INFO.get(pid)
    if info is None:
        raise HTTPException(400, f"Unknown provider '{provider}'.")
    if not info.requires_key:  # mock
        return {"ok": True, "provider": pid, "models": [{"id": "mock", "label": "Deterministic mock"}]}

    rt = get_runtime()
    key = (body.api_key or rt.api_key_for(pid)).strip()
    if not key:
        raise HTTPException(400, "Enter an API key to fetch this provider's models.")
    try:
        models = await build_provider(pid, key).list_models()
        return {"ok": True, "provider": pid,
                "models": [{"id": mid, "label": label} for mid, label in models]}
    except Exception as exc:  # noqa: BLE001 — report as data so the UI can show it
        return {"ok": False, "provider": pid, "models": [],
                "error": f"{type(exc).__name__}: {exc}"[:300]}


@app.get("/config")
async def get_config() -> dict:
    return get_runtime().public_snapshot()


class ConfigUpdate(BaseModel):
    provider: str | None = None
    api_key: str | None = None
    fast_model: str | None = None
    deep_model: str | None = None
    confidence_threshold: float | None = None
    match_threshold: float | None = None
    max_concurrency: int | None = None


@app.put("/config")
async def put_config(update: ConfigUpdate, request: Request) -> dict:
    _require_admin(request)
    try:
        rt = update_runtime(**update.model_dump(exclude_none=True))
    except ConfigError as exc:
        raise HTTPException(400, str(exc))
    log.info("config updated · provider=%s model=%s mock=%s",
             rt.effective_provider, rt.active_fast_model, rt.mock_mode)
    return rt.public_snapshot()


class ConfigTest(BaseModel):
    provider: str | None = None
    api_key: str | None = None
    model: str | None = None


@app.post("/config/test")
async def test_config(body: ConfigTest, request: Request) -> dict:
    """Validate a provider/key/model with one tiny live call — never persisted.

    Lets the dashboard offer a 'Test connection' button before saving, so a bad
    key fails fast and visibly instead of silently degrading runs to mock."""
    _require_admin(request)
    rt = get_runtime()
    pid = (body.provider or rt.provider).strip().lower()
    if pid not in PROVIDER_INFO:
        raise HTTPException(400, f"Unknown provider '{pid}'.")
    if pid == "mock":
        return {"ok": True, "provider": "mock", "model": "mock", "latency_ms": 0,
                "detail": "Mock mode is deterministic and needs no API key."}

    key = (body.api_key or rt.api_key_for(pid)).strip()
    if not key:
        raise HTTPException(400, "No API key was provided or configured for this provider.")
    model = (body.model or rt.fast_models.get(pid) or PROVIDER_INFO[pid].default_fast)

    provider = build_provider(pid, key)
    started = time.perf_counter()
    try:
        resp = await provider.complete(
            model=model, prompt="Reply with the single word: pong.", max_tokens=8
        )
        return {
            "ok": True, "provider": pid, "model": model,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "detail": (resp.text or "").strip()[:80] or "(empty reply, but call succeeded)",
        }
    except Exception as exc:  # noqa: BLE001 — report failure as data, not a 500
        return {
            "ok": False, "provider": pid, "model": model,
            "error": f"{type(exc).__name__}: {exc}"[:300],
        }


@app.post("/reconcile")
async def reconcile_endpoint(files: list[UploadFile] = File(...)) -> dict:
    if not files:
        raise HTTPException(400, "Upload at least one document.")
    s = get_settings()
    if len(files) > s.ledger_max_files:
        raise HTTPException(
            400, f"Too many documents: {len(files)} (the per-run limit is {s.ledger_max_files})."
        )
    max_bytes = s.ledger_max_upload_mb * 1024 * 1024
    docs: list[UploadedDoc] = []
    for f in files:
        name = f.filename or "unnamed"
        data = await f.read()
        if not data:
            raise HTTPException(400, f"'{name}' is empty.")
        if len(data) > max_bytes:
            raise HTTPException(
                413, f"'{name}' exceeds the {s.ledger_max_upload_mb} MB per-file limit."
            )
        docs.append(UploadedDoc(name=name, data=data))
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    _remember(_staged, run_id, docs)
    _remember(_status, run_id, {"state": "queued", "documents": len(docs)})
    # Launch immediately — execution does not wait for anyone to watch.
    asyncio.create_task(process_run(run_id, docs))
    log.info("run %s queued · %d documents", run_id, len(docs))
    return {"run_id": run_id, "documents": len(docs)}


async def process_run(run_id: str, docs: list[UploadedDoc]) -> None:
    rt = get_runtime()
    started = time.perf_counter()
    _remember(_status, run_id, {"state": "running", "documents": len(docs)})
    try:
        await bus.publish(run_id, "run.started", {"documents": len(docs)})
        sem = asyncio.Semaphore(rt.max_concurrency)

        async def work(doc: UploadedDoc) -> list:
            await bus.publish(run_id, "agent.cell.start", {"doc": doc.name})
            async with sem:
                results = await extract_document(run_id, doc)
            # One cell per document (a CSV expands to many rows but is one worker).
            await bus.publish(
                run_id, "agent.cell.done",
                {"doc": doc.name,
                 "worker": results[0].worker if results else "?",
                 "latency_ms": max((r.latency_ms for r in results), default=0),
                 "model": results[0].model if results else "mock",
                 "faithfulness": round(sum(r.faithfulness for r in results) / len(results), 3) if results else 0.0,
                 "count": len(results),
                 "ok": any(r.transaction is not None for r in results)},
            )
            return results

        # FAN-OUT: every document extracted concurrently; wall-clock ≈ slowest doc.
        batches = await asyncio.gather(*(work(d) for d in docs))
        extractions = [r for batch in batches for r in batch]

        # FAN-IN → reconciliation state machine.
        result = await run_reconciliation(run_id, extractions, rt.match_threshold)
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        _remember(_results, run_id, result)
        _remember(_status, run_id, {"state": "completed", "documents": result.documents})

        await bus.publish(
            run_id, "run.completed",
            {"posted": len(result.posted), "quarantined": len(result.quarantined),
             "links": len(result.links), "total_posted_amount": str(result.total_posted_amount),
             "documents": result.documents, "duration_ms": result.duration_ms},
        )
        log.info("run %s completed · %d posted, %d quarantined, %d links (%dms)",
                 run_id, len(result.posted), len(result.quarantined), len(result.links),
                 result.duration_ms)
    except Exception as exc:  # noqa: BLE001 — a failed run must surface, not hang
        log.exception("run %s failed", run_id)
        _remember(_status, run_id, {"state": "failed", "error": str(exc)})
        await bus.publish(run_id, "run.failed", {"error": str(exc)})


@app.get("/events/{run_id}")
async def events(run_id: str):
    if run_id not in _status and run_id not in _results and not bus.is_terminated(run_id):
        raise HTTPException(404, "Unknown run_id — POST /reconcile first.")

    queue = await bus.subscribe(run_id)

    async def event_generator():
        try:
            while True:
                event = await queue.get()
                yield {"event": event["type"], "data": json.dumps(event["payload"])}
                if event["type"] in ("run.completed", "run.failed"):
                    break
        finally:
            await bus.unsubscribe(run_id, queue)

    return EventSourceResponse(event_generator())


@app.get("/runs/{run_id}/status")
async def get_status(run_id: str) -> dict:
    status = _status.get(run_id)
    if status is None:
        raise HTTPException(404, "Unknown run_id.")
    return {"run_id": run_id, **status}


@app.get("/runs/{run_id}")
async def get_run(run_id: str) -> RunResult:
    result = _results.get(run_id)
    if result is None:
        # Distinguish "still working" from "never existed" for a polling client.
        status = _status.get(run_id)
        if status and status.get("state") in ("queued", "running"):
            raise HTTPException(202, "Run in progress.")
        if status and status.get("state") == "failed":
            raise HTTPException(500, status.get("error", "Run failed."))
        raise HTTPException(404, "Run not found.")
    return result


# ── Static dashboard (single-origin deploy) ───────────────────────────────────
# Serve the vanilla-JS dashboard from the *same* process as the API. Mounted
# last, so every API route above is matched first and this only catches the
# remaining paths ("/", "/app.html", "/styles.css", ...). One origin means no
# CORS and one public URL — exactly what a single free container (Hugging Face
# Space / Render / Cloud Run) exposes. The directory is resolved from an env
# override first (set in the container) and falls back to the repo's frontend/
# folder for local `uvicorn`, so opening http://localhost:8000/ "just works".
_frontend_dir = Path(
    os.getenv("LEDGER_FRONTEND_DIR")
    or Path(__file__).resolve().parents[2] / "frontend"
)
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
    log.info("serving dashboard from %s", _frontend_dir)
else:  # API-only deployment (e.g. behind a separate static CDN) — that's fine.
    log.info("no frontend dir at %s — running API-only", _frontend_dir)
