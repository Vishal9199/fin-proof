"""Ledger Sentinel API.

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
import time
import uuid
from collections import OrderedDict

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from .config import get_settings
from .events import bus
from .extraction import UploadedDoc, extract_document
from .graph.reconciliation import run_reconciliation
from .schemas import RunResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s · %(message)s",
)
log = logging.getLogger("ledger")

app = FastAPI(title="Ledger Sentinel", version="1.1.0")

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
    return {
        "status": "ok",
        "version": app.version,
        "mock_mode": s.mock_mode,
        "langfuse": s.langfuse_enabled,
        "max_concurrency": s.ledger_max_concurrency,
    }


@app.post("/reconcile")
async def reconcile_endpoint(files: list[UploadFile] = File(...)) -> dict:
    if not files:
        raise HTTPException(400, "Upload at least one document.")
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    docs = [UploadedDoc(name=f.filename or "unnamed", data=await f.read()) for f in files]
    _remember(_staged, run_id, docs)
    _remember(_status, run_id, {"state": "queued", "documents": len(docs)})
    # Launch immediately — execution does not wait for anyone to watch.
    asyncio.create_task(process_run(run_id, docs))
    log.info("run %s queued · %d documents", run_id, len(docs))
    return {"run_id": run_id, "documents": len(docs)}


async def process_run(run_id: str, docs: list[UploadedDoc]) -> None:
    settings = get_settings()
    started = time.perf_counter()
    _remember(_status, run_id, {"state": "running", "documents": len(docs)})
    try:
        await bus.publish(run_id, "run.started", {"documents": len(docs)})
        sem = asyncio.Semaphore(settings.ledger_max_concurrency)

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
        result = await run_reconciliation(run_id, extractions, settings.ledger_match_threshold)
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
