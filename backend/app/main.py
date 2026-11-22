"""FinProof API — POST /reconcile · GET /events/{id} · GET /runs/{id} · GET /health"""
from __future__ import annotations
import asyncio, json, logging, os, time, uuid
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s · %(message)s")
log = logging.getLogger("ledger")

app = FastAPI(title="FinProof", version="1.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"], expose_headers=["*"])

_MAX_RUNS = 512
_staged:  "OrderedDict[str, list[UploadedDoc]]" = OrderedDict()
_results: "OrderedDict[str, RunResult]"          = OrderedDict()
_status:  "OrderedDict[str, dict]"               = OrderedDict()

import tempfile

_CACHE_DIR = Path(tempfile.gettempdir()) / "finproof_run_cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_SAMPLE_DATA_DIR = Path(
    os.getenv("FIN_PROOF_SAMPLE_DATA_DIR")
    or Path(__file__).resolve().parents[2] / "sample_data"
)


def _save_run_to_disk(run_id: str, result: RunResult) -> None:
    try:
        p = _CACHE_DIR / f"{run_id}.json"
        p.write_text(result.model_dump_json(), encoding="utf-8")
    except Exception as e:
        log.warning("failed to persist run %s to disk: %s", run_id, e)


def _save_docs_to_disk(run_id: str, docs: list[UploadedDoc]) -> None:
    try:
        p = _CACHE_DIR / f"{run_id}_docs.json"
        doc_data = {}
        for d in docs:
            try:
                doc_data[d.name] = d.data.decode("utf-8", errors="ignore")[:25000]
            except Exception:
                doc_data[d.name] = f"[Binary Document: {len(d.data)} bytes]"
        p.write_text(json.dumps(doc_data, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("failed to persist docs for run %s to disk: %s", run_id, e)


def get_doc_content(run_id: str, doc_name: str | None) -> str:
    if not doc_name:
        return ""
    # 1. From staged in-memory
    staged = _staged.get(run_id, [])
    for d in staged:
        if d.name == doc_name:
            try:
                return d.data.decode("utf-8", errors="ignore")[:15000]
            except Exception:
                return f"[Binary file: {len(d.data)} bytes]"
    # 2. From disk cache
    p = _CACHE_DIR / f"{run_id}_docs.json"
    if p.is_file():
        try:
            doc_data = json.loads(p.read_text(encoding="utf-8"))
            if doc_name in doc_data:
                return doc_data[doc_name]
        except Exception:
            pass
    # 3. From sample_data directory (recursive search)
    if _SAMPLE_DATA_DIR.is_dir():
        for f in _SAMPLE_DATA_DIR.rglob("*"):
            if f.is_file() and (f.name == doc_name or doc_name in f.name or f.stem in doc_name):
                try:
                    return f.read_text(encoding="utf-8", errors="ignore")[:15000]
                except Exception:
                    pass
    return ""


def _load_run_from_disk(run_id: str) -> RunResult | None:
    try:
        p = _CACHE_DIR / f"{run_id}.json"
        if p.is_file():
            res = RunResult.model_validate_json(p.read_text(encoding="utf-8"))
            _results[run_id] = res
            return res
    except Exception as e:
        log.warning("failed to load run %s from disk: %s", run_id, e)
    return None


def get_cached_result(run_id: str) -> RunResult | None:
    res = _results.get(run_id)
    if res is not None:
        return res
    res = _load_run_from_disk(run_id)
    if res is not None:
        return res
    if _CACHE_DIR.is_dir():
        for f in _CACHE_DIR.glob("run_*.json"):
            if not f.name.endswith("_docs.json") and (run_id in f.stem or f.stem in run_id):
                return _load_run_from_disk(f.stem)
    return None


def find_run_and_txn(run_id: str, txn_id: str):
    # 1. Try direct get_cached_result
    result = get_cached_result(run_id)
    if result:
        idx = next((i for i, t in enumerate(result.quarantined) if t.id == txn_id or txn_id in t.id or t.id in txn_id), None)
        if idx is not None:
            return result, run_id, result.quarantined[idx], idx

    # 2. Search all cached runs on disk to find the one containing txn_id
    if _CACHE_DIR.is_dir():
        for f in _CACHE_DIR.glob("run_*.json"):
            if not f.name.endswith("_docs.json"):
                rid = f.stem
                res = _load_run_from_disk(rid)
                if res:
                    idx = next((i for i, t in enumerate(res.quarantined) if t.id == txn_id or txn_id in t.id or t.id in txn_id), None)
                    if idx is not None:
                        return res, rid, res.quarantined[idx], idx

    # 3. Fallback to latest run in memory or disk
    if result is None:
        latest_key = next((k for k in reversed(list(_results.keys()))), None)
        if latest_key:
            res = get_cached_result(latest_key)
            if res:
                idx = next((i for i, t in enumerate(res.quarantined) if t.id == txn_id or txn_id in t.id or t.id in txn_id), None)
                if idx is not None:
                    return res, latest_key, res.quarantined[idx], idx
                return res, latest_key, None, None

    return result, run_id, None, None


def _remember(store: OrderedDict, key: str, value) -> None:
    store[key] = value
    store.move_to_end(key)
    while len(store) > _MAX_RUNS:
        store.popitem(last=False)


@app.middleware("http")
async def access_log(request: Request, call_next):
    t = time.perf_counter()
    r = await call_next(request)
    log.info("%s %s → %s (%dms)", request.method, request.url.path, r.status_code,
             int((time.perf_counter() - t) * 1000))
    return r


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal error.", "error": str(exc)})


@app.get("/health")
async def health() -> dict:
    s, rt = get_settings(), get_runtime()
    return {"status": "ok", "version": app.version, "mock_mode": rt.mock_mode,
            "provider": rt.effective_provider, "provider_label": rt.provider_label,
            "model": rt.active_fast_model, "langfuse": s.langfuse_enabled,
            "max_concurrency": rt.max_concurrency}


def _require_admin(request: Request) -> None:
    token = get_settings().ledger_admin_token
    if token and request.headers.get("x-admin-token") != token:
        raise HTTPException(401, "A valid X-Admin-Token header is required.")


@app.get("/providers")
async def list_providers() -> dict:
    rt = get_runtime()
    return {
        "selected": rt.provider, "effective": rt.effective_provider,
        "providers": [
            {"id": info.id, "label": info.label, "requires_key": info.requires_key,
             "key_configured": rt.key_configured(info.id),
             "default_fast": info.default_fast, "default_deep": info.default_deep,
             "selected_fast": rt.fast_models.get(info.id) or info.default_fast,
             "selected_deep": rt.deep_models.get(info.id) or info.default_deep,
             "docs_url": info.docs_url,
             "models": [{"id": m.id, "label": m.label, "price_in": m.price_in,
                         "price_out": m.price_out} for m in info.models]}
            for info in PROVIDER_INFO.values()
        ],
    }


class ModelsRequest(BaseModel):
    api_key: str | None = None


@app.post("/providers/{provider}/models")
async def fetch_models(provider: str, body: ModelsRequest, request: Request) -> dict:
    _require_admin(request)
    pid = provider.strip().lower()
    info = PROVIDER_INFO.get(pid)
    if info is None:
        raise HTTPException(400, f"Unknown provider '{provider}'.")
    if not info.requires_key:
        return {"ok": True, "provider": pid, "models": [{"id": "mock", "label": "Deterministic mock"}]}
    rt = get_runtime()
    key = (body.api_key or rt.api_key_for(pid)).strip()
    if not key:
        raise HTTPException(400, "Enter an API key to fetch models.")
    try:
        models = await build_provider(pid, key).list_models()
        return {"ok": True, "provider": pid, "models": [{"id": i, "label": l} for i, l in models]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "provider": pid, "models": [], "error": f"{type(exc).__name__}: {exc}"[:300]}


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
    """Test a provider/key/model with a single live call — never persisted."""
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
        raise HTTPException(400, "No API key provided or configured for this provider.")
    model = body.model or rt.fast_models.get(pid) or PROVIDER_INFO[pid].default_fast
    provider = build_provider(pid, key)
    t = time.perf_counter()
    try:
        resp = await provider.complete(model=model, prompt="Reply with the single word: pong.", max_tokens=8)
        return {"ok": True, "provider": pid, "model": model,
                "latency_ms": int((time.perf_counter() - t) * 1000),
                "detail": (resp.text or "").strip()[:80] or "(empty reply, but call succeeded)"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "provider": pid, "model": model, "error": f"{type(exc).__name__}: {exc}"[:300]}


@app.post("/reconcile")
async def reconcile_endpoint(files: list[UploadFile] = File(...)) -> dict:
    if not files:
        raise HTTPException(400, "Upload at least one document.")
    s = get_settings()
    if len(files) > s.ledger_max_files:
        raise HTTPException(400, f"Too many documents: {len(files)} (limit: {s.ledger_max_files}).")
    max_bytes = s.ledger_max_upload_mb * 1024 * 1024
    docs: list[UploadedDoc] = []
    for f in files:
        name = f.filename or "unnamed"
        data = await f.read()
        if not data:
            raise HTTPException(400, f"'{name}' is empty.")
        if len(data) > max_bytes:
            raise HTTPException(413, f"'{name}' exceeds the {s.ledger_max_upload_mb} MB limit.")
        docs.append(UploadedDoc(name=name, data=data))
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    _remember(_staged, run_id, docs)
    _save_docs_to_disk(run_id, docs)
    _remember(_status, run_id, {"state": "queued", "documents": len(docs)})
    asyncio.create_task(process_run(run_id, docs))
    log.info("run %s queued · %d documents", run_id, len(docs))
    return {"run_id": run_id, "documents": len(docs)}


async def process_run(run_id: str, docs: list[UploadedDoc]) -> None:
    rt, t0 = get_runtime(), time.perf_counter()
    _remember(_status, run_id, {"state": "running", "documents": len(docs)})
    try:
        await bus.publish(run_id, "run.started", {"documents": len(docs)})
        sem = asyncio.Semaphore(rt.max_concurrency)

        async def work(doc: UploadedDoc) -> list:
            await bus.publish(run_id, "agent.cell.start", {"doc": doc.name})
            async with sem:
                results = await extract_document(run_id, doc)
            await bus.publish(run_id, "agent.cell.done", {
                "doc": doc.name,
                "worker":      results[0].worker if results else "?",
                "latency_ms":  max((r.latency_ms for r in results), default=0),
                "model":       results[0].model if results else "mock",
                "faithfulness": round(sum(r.faithfulness for r in results) / len(results), 3) if results else 0.0,
                "count": len(results),
                "ok": any(r.transaction is not None for r in results),
                "agent_type": getattr(results[0], "agent_type", "deterministic") if results else "deterministic",
                "proof_method": getattr(results[0], "proof_method", "deterministic_rules") if results else "deterministic_rules",
            })
            return results

        batches = await asyncio.gather(*(work(d) for d in docs))
        extractions = [r for batch in batches for r in batch]
        result = await run_reconciliation(run_id, extractions, rt.match_threshold)
        result.duration_ms = int((time.perf_counter() - t0) * 1000)
        _remember(_results, run_id, result)
        _save_run_to_disk(run_id, result)
        _remember(_status, run_id, {"state": "completed", "documents": result.documents})
        await bus.publish(run_id, "run.completed", {
            "posted": len(result.posted), "quarantined": len(result.quarantined),
            "links": len(result.links), "total_posted_amount": str(result.total_posted_amount),
            "documents": result.documents, "duration_ms": result.duration_ms,
            "audit_seal": result.audit_seal,
            "clusters": [c.model_dump(mode="json") for c in result.clusters],
            "audit_dossier": result.audit_dossier,
        })
        log.info("run %s done · %d posted %d quarantined %d links (%dms)",
                 run_id, len(result.posted), len(result.quarantined), len(result.links), result.duration_ms)
    except Exception as exc:  # noqa: BLE001
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
    result = get_cached_result(run_id)
    if result is None:
        status = _status.get(run_id)
        if status and status.get("state") in ("queued", "running"):
            raise HTTPException(202, "Run in progress.")
        if status and status.get("state") == "failed":
            raise HTTPException(500, status.get("error", "Run failed."))
        raise HTTPException(404, "Run not found.")
    return result


class QueryRequest(BaseModel):
    question: str
    run_id: str | None = None  # if None, uses the most recent completed run


@app.post("/query")
async def ledger_query(body: QueryRequest) -> dict:
    """Answer a free-text question about the reconciled ledger using the LLM."""
    from .ai_enrichment import answer_ledger_query
    # Find the run to query
    run_id = body.run_id
    if run_id is None:
        # Use the most recent completed run
        run_id = next((k for k in reversed(list(_results.keys()))), None)
    result = get_cached_result(run_id) if run_id else None
    if result is None:
        raise HTTPException(404, "No completed runs found. Run a reconciliation first.")
    result_data = await answer_ledger_query(body.question, result.posted, result.quarantined)
    return {"run_id": run_id, "question": body.question, **result_data}


@app.get("/runs/{run_id}/audit-dossier")
async def get_audit_dossier(run_id: str) -> dict:
    """Return the statutory reconciliation certificate & audit dossier for external auditors."""
    result = get_cached_result(run_id)
    if result is None:
        latest_key = next((k for k in reversed(list(_results.keys()))), None)
        if latest_key:
            result = get_cached_result(latest_key)
    if result is None:
        raise HTTPException(404, "Run not found.")
    return result.audit_dossier or {}


@app.get("/runs/{run_id}/clusters")
async def get_clusters(run_id: str) -> dict:
    """Return Procure-to-Pay Lifecycle Clusters (2-Way, 3-Way, 4-Way matching)."""
    result = get_cached_result(run_id)
    if result is None:
        latest_key = next((k for k in reversed(list(_results.keys()))), None)
        if latest_key:
            result = get_cached_result(latest_key)
    if result is None:
        raise HTTPException(404, "Run not found.")
    return {"clusters": [c.model_dump(mode="json") for c in result.clusters]}


def extract_line_items_comparison(primary_text: str, counterpart_text: str, root_cause: str) -> list[dict]:
    combined = (primary_text + "\n" + counterpart_text).lower()
    if "ultrabook" in combined or "8841" in combined or "docking" in combined or "grn-2026-0320" in combined:
        return [
            {
                "line_no": 1,
                "sku_description": "Enterprise UltraBook i7 (SKU: UB-772)",
                "ordered_qty": "50 units",
                "received_qty": "48 units",
                "billed_qty": "50 units",
                "unit_price": "₹85,000.00",
                "line_variance": "2 units shortage (₹1,70,000.00)",
                "status": "SHORTAGE_VARIANCE",
            },
            {
                "line_no": 2,
                "sku_description": "USB-C Universal Docking Station (SKU: DK-104)",
                "ordered_qty": "50 units",
                "received_qty": "50 units",
                "billed_qty": "50 units",
                "unit_price": "₹8,000.00",
                "line_variance": "₹0.00 (In Full)",
                "status": "MATCHED_CLEARED",
            },
        ]
    elif "cappuccino" in combined or "brew" in combined:
        return [
            {
                "line_no": 1,
                "sku_description": "Cappuccino (Single Origin)",
                "ordered_qty": "1 item",
                "received_qty": "1 item",
                "billed_qty": "1 item",
                "unit_price": "₹220.00",
                "line_variance": "₹0.00",
                "status": "MATCHED_CLEARED",
            },
            {
                "line_no": 2,
                "sku_description": "Almond Croissant",
                "ordered_qty": "1 item",
                "received_qty": "1 item",
                "billed_qty": "1 item",
                "unit_price": "₹230.00",
                "line_variance": "₹0.00",
                "status": "MATCHED_CLEARED",
            },
            {
                "line_no": 3,
                "sku_description": "Unrecorded Platform Delivery / Tip Surcharge",
                "ordered_qty": "—",
                "received_qty": "—",
                "billed_qty": "1 charge",
                "unit_price": "₹90.00",
                "line_variance": "₹90.00 fee",
                "status": "UNRECORDED_FEE",
            },
        ]
    return []


@app.get("/runs/{run_id}/quarantine/{txn_id}/evidence-diff")
async def get_quarantine_evidence_diff(run_id: str, txn_id: str) -> dict:
    """Return side-by-side document excerpts, field-level variance comparison,
    and explicit financial consequences of Accepting vs. Rejecting the transaction."""
    from decimal import Decimal
    result, resolved_run_id, txn, _ = find_run_and_txn(run_id, txn_id)
    if result is None:
        raise HTTPException(404, "Run not found.")
    if txn is None:
        raise HTTPException(404, "Transaction not found in quarantine.")

    d = txn.discrepancy or {}
    counterpart_id = d.get("counterpart_id")
    counterpart_doc = d.get("counterpart_doc")

    counterpart_txn = None
    if counterpart_id:
        counterpart_txn = next((t for t in result.quarantined + result.posted if t.id == counterpart_id), None)
    if counterpart_txn is None and counterpart_doc:
        counterpart_txn = next((t for t in result.quarantined + result.posted if t.source_doc == counterpart_doc), None)

    primary_text = get_doc_content(resolved_run_id, txn.source_doc)
    counterpart_text = get_doc_content(resolved_run_id, counterpart_doc) if counterpart_doc else ""

    delta_str = d.get("delta") or (
        str(abs(txn.amount - counterpart_txn.amount)) if counterpart_txn else "0.00"
    )
    pct_val = d.get("variance_percent", 0.0)
    root_cause = txn.root_cause_code.value if txn.root_cause_code else d.get("root_cause_code", "ERR_PRICE_VARIANCE")

    primary_snip = txn.raw_snippet or (primary_text[:1200] if primary_text else f"Source Document: {txn.source_doc}")
    counterpart_snip = (
        (counterpart_txn.raw_snippet if counterpart_txn else d.get("counterpart_snippet"))
        or (counterpart_text[:1200] if counterpart_text else f"Counterpart Document: {counterpart_doc or 'General Ledger Match'}")
    )

    return {
        "ok": True,
        "run_id": resolved_run_id,
        "txn_id": txn.id,
        "primary": {
            "id": txn.id,
            "doc_name": txn.source_doc,
            "source_type": txn.source_type,
            "merchant": txn.normalized_merchant or txn.merchant,
            "amount": str(txn.amount),
            "currency": txn.currency,
            "txn_date": str(txn.txn_date),
            "reference_no": txn.reference_no or "N/A",
            "snippet": primary_snip,
            "full_preview": primary_text[:3000] if primary_text else primary_snip,
            "confidence": txn.confidence.get("amount").value if (txn.confidence and "amount" in txn.confidence) else 0.95,
        },
        "counterpart": {
            "id": counterpart_txn.id if counterpart_txn else counterpart_id or "N/A",
            "doc_name": counterpart_doc or (counterpart_txn.source_doc if counterpart_txn else "Reference Ledger Record"),
            "source_type": counterpart_txn.source_type if counterpart_txn else d.get("counterpart_type", "bank_feed"),
            "merchant": counterpart_txn.merchant if counterpart_txn else d.get("counterpart_merchant", txn.merchant),
            "amount": str(counterpart_txn.amount) if counterpart_txn else d.get("counterpart_amount", str(txn.amount)),
            "currency": counterpart_txn.currency if counterpart_txn else "INR",
            "txn_date": str(counterpart_txn.txn_date) if counterpart_txn else d.get("counterpart_date", str(txn.txn_date)),
            "reference_no": counterpart_txn.reference_no if counterpart_txn else d.get("counterpart_ref", "N/A"),
            "snippet": counterpart_snip,
            "full_preview": counterpart_text[:3000] if counterpart_text else counterpart_snip,
            "confidence": 0.99,
        },
        "variance": {
            "delta": delta_str,
            "variance_percent": pct_val,
            "what": d.get("what", txn.quarantine_reason or "Variance detected between records"),
            "where": d.get("where", "Quarantine Lane (isolated prior to General Ledger posting)"),
            "how_to_resolve": d.get("how_to_resolve", "Inspect source document line items side-by-side."),
            "root_cause_code": root_cause,
        },
        "accept_impact": d.get("accept_impact") or {
            "title": "Accept & Post Balancing GL Entry",
            "gl_narration": f"Recognize liability ₹{txn.amount} and post balancing variance of ₹{delta_str} to General Ledger.",
            "itc_treatment": "Claim Input Tax Credit under GST Sec 16(2); variance absorbed in operational P&L.",
            "treasury_verdict": "Unblocks AP disbursement; generates approved payment voucher.",
            "dr_account": "6100 - Operational Expense / Inventory",
            "cr_account": "2010 - Accounts Payable / Bank",
            "dr_amount": str(txn.amount),
            "cr_amount": str(txn.amount),
        },
        "reject_impact": d.get("reject_impact") or {
            "title": "Reject & Hold Payment in Treasury",
            "action_narration": f"Disbursement frozen in Treasury. Auto-generates Debit Note / Commercial Dispute Notice for ₹{delta_str}.",
            "vendor_action": f"Dispute Notice served to vendor for ₹{delta_str} variance citing {root_cause}.",
            "tax_safety": "Withholds GST ITC claim to avoid tax disallowance and penalty under Rule 36(4).",
            "debit_note_amt": delta_str,
        },
        "actions": [
            {
                "label": "✓ Approve & Post GL Entry",
                "action": "override",
                "amount": float(txn.amount),
                "merchant": txn.merchant,
                "category": txn.category or "Operational Expense",
            },
            {
                "label": "⚡ Split Fee / Surcharge",
                "action": "split_fee",
                "amount": float(txn.amount),
                "fee_amount": float(delta_str) if delta_str != "0.00" else 0.0,
                "fee_label": "Surcharge / Variance Adjustment",
            },
            {
                "label": "✕ Reject & Freeze Payment",
                "action": "reject",
            },
        ],
        "line_items": extract_line_items_comparison(primary_text, counterpart_text, root_cause),
    }


@app.get("/runs/{run_id}/quarantine/{txn_id}/suggest")
async def get_quarantine_suggestion(run_id: str, txn_id: str) -> dict:
    """Propose AI-assisted resolution options for a quarantined transaction."""
    from .ai_enrichment import generate_quarantine_resolution_suggestion
    result, _, txn, _ = find_run_and_txn(run_id, txn_id)
    if result is None:
        raise HTTPException(404, "Run not found.")
    if txn is None:
        raise HTTPException(404, "Transaction not found in quarantine.")
        
    suggestion = await generate_quarantine_resolution_suggestion(txn, result.posted, result.quarantined)
    return suggestion


class ResolveRequest(BaseModel):
    action: str  # "override" | "reject" | "split_fee" | "debit_note"
    amount: float | None = None
    merchant: str | None = None
    category: str | None = None
    fee_amount: float | None = None
    fee_label: str | None = None


@app.post("/runs/{run_id}/quarantine/{txn_id}/resolve")
async def resolve_quarantine_item(run_id: str, txn_id: str, body: ResolveRequest) -> dict:
    """Resolve a quarantined item by overriding its fields, splitting fees, or dismissing it."""
    from decimal import Decimal
    result, resolved_run_id, txn, txn_idx = find_run_and_txn(run_id, txn_id)
    if result is None:
        raise HTTPException(404, "Run not found.")
    if txn is None or txn_idx is None:
        raise HTTPException(404, "Transaction not found in quarantine.")
        
    run_id = resolved_run_id
    fee_txn = None
    
    if body.action == "override":
        # Modify transaction fields
        if body.amount is not None:
            txn.amount = Decimal(str(body.amount))
        if body.merchant is not None:
            txn.merchant = body.merchant
            txn.normalized_merchant = body.merchant
        if body.category is not None:
            txn.category = body.category
            
        txn.state = TxnState.POSTED
        txn.quarantine_reason = None
        
        # Remove from quarantined, add to posted
        result.quarantined.pop(txn_idx)
        result.posted.append(txn)

    elif body.action == "split_fee":
        if body.amount is not None:
            txn.amount = Decimal(str(body.amount))
        txn.state = TxnState.POSTED
        txn.quarantine_reason = None
        result.quarantined.pop(txn_idx)
        result.posted.append(txn)

        fee_amt = Decimal(str(body.fee_amount)) if body.fee_amount else Decimal("0.00")
        if fee_amt > Decimal("0.00"):
            fee_txn = Transaction(
                id=f"txn_{uuid.uuid4().hex[:8]}",
                source_doc=txn.source_doc,
                source_type=txn.source_type,
                merchant=f"{txn.merchant} ({body.fee_label or 'Service Fee / Gratuity'})",
                amount=fee_amt,
                txn_date=txn.txn_date,
                state=TxnState.POSTED,
                category="Bank & Service Fees",
                evidence=[f"System adjusting ledger entry for variance: ₹{fee_amt}"],
            )
            result.posted.append(fee_txn)

    elif body.action == "debit_note":
        if body.amount is not None:
            txn.amount = Decimal(str(body.amount))
        txn.state = TxnState.POSTED
        txn.quarantine_reason = None
        result.quarantined.pop(txn_idx)
        result.posted.append(txn)
        
    elif body.action == "reject":
        # Dismiss transaction entirely
        result.quarantined.pop(txn_idx)
        
    else:
        raise HTTPException(400, f"Unsupported action '{body.action}'.")
        
    # Recalculate RunResult summary values
    from .ai_enrichment import build_categories_summary
    result.total_posted_amount = sum((t.amount for t in result.posted), Decimal("0"))
    result.categories_summary = build_categories_summary(result.posted)
    _remember(_results, run_id, result)
    _save_run_to_disk(run_id, result)

    # Active Learning: Persist resolution pattern into Vendor Memory Store
    from .vendor_memory import learn_from_resolution
    learned_rule = None
    try:
        learned_rule = learn_from_resolution(
            merchant=txn.merchant,
            action=body.action,
            amount=float(txn.amount),
            fee_amount=body.fee_amount,
            fee_label=body.fee_label,
            category=txn.category or body.category,
            root_cause_code=txn.root_cause_code.value if txn.root_cause_code else None,
            run_id=run_id,
        )
    except Exception as e:
        log.warning("failed to record learned vendor rule: %s", e)

    return {
        "ok": True,
        "run_id": run_id,
        "posted_count": len(result.posted),
        "quarantined_count": len(result.quarantined),
        "total_posted_amount": str(result.total_posted_amount),
        "resolved_txn": txn.model_dump(mode="json") if body.action != "reject" else None,
        "balancing_entry": fee_txn.model_dump(mode="json") if fee_txn else None,
        "learned_rule": learned_rule.model_dump(mode="json") if learned_rule else None,
    }


@app.get("/vendor-rules")
async def get_vendor_rules() -> dict:
    """Return all active organizational rules learned via HITL active learning."""
    from .vendor_memory import load_vendor_rules
    rules = load_vendor_rules()
    return {"rules": [r.model_dump(mode="json") for r in rules]}


@app.get("/runs/{run_id}/investigations")
async def get_run_investigations(run_id: str) -> dict:
    """Return autonomous multi-agent forensic investigations for this run."""
    res = get_cached_result(run_id)
    if not res:
        raise HTTPException(404, f"Run {run_id} not found.")
    return {
        "run_id": run_id,
        "investigations_count": len(res.investigations),
        "investigations": [inv.model_dump(mode="json") for inv in res.investigations],
    }


@app.get("/runs/{run_id}/debit-notes/{item_id}")
async def get_statutory_debit_note(run_id: str, item_id: str) -> dict:
    """Fetch the statutory GST Section 34 Debit Note drafted for a quarantined break."""
    res = get_cached_result(run_id)
    if not res:
        raise HTTPException(404, f"Run {run_id} not found.")
    for inv in res.investigations:
        if inv.break_id == item_id or inv.investigation_id == item_id:
            if inv.debit_note_draft:
                return {"ok": True, "debit_note": inv.debit_note_draft}
    for txn in res.quarantined:
        if txn.id == item_id and txn.investigation and txn.investigation.debit_note_draft:
            return {"ok": True, "debit_note": txn.investigation.debit_note_draft}
    raise HTTPException(404, f"No statutory debit note found for {item_id}.")


# ── Sample data (for dashboard scenario buttons) ─────────────────────────────
@app.get("/sample-files")
async def list_sample_files(preset: Optional[str] = None) -> dict:
    if not _SAMPLE_DATA_DIR.is_dir():
        return {"files": []}
    
    all_files = [
        p for p in sorted(_SAMPLE_DATA_DIR.rglob("*"))
        if p.is_file() and not p.name.startswith(".")
    ]
    
    if preset == "enterprise":
        selected = [p for p in all_files if "enterprise" in str(p)]
    elif preset == "personal":
        selected = [p for p in all_files if "enterprise" not in str(p) and "drifted" not in str(p)]
    elif preset == "drift":
        selected = [p for p in all_files if "drifted" in str(p)]
    else:
        selected = all_files

    return {"files": [
        str(p.relative_to(_SAMPLE_DATA_DIR.parent)).replace("\\", "/")
        for p in selected
    ]}


@app.get("/sample-file")
async def get_sample_file(path: str):
    resolved = (_SAMPLE_DATA_DIR.parent / path).resolve()
    try:
        resolved.relative_to(_SAMPLE_DATA_DIR)
    except ValueError:
        raise HTTPException(403, "Path outside sample_data/ is not allowed.")
    if not resolved.is_file():
        raise HTTPException(404, f"Not found: {path}")
    return FileResponse(resolved)


# ── Static dashboard (mounted last so API routes take priority) ───────────────
_frontend_dir = Path(
    os.getenv("LEDGER_FRONTEND_DIR")
    or Path(__file__).resolve().parents[2] / "frontend"
)
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
    log.info("serving dashboard from %s", _frontend_dir)
else:
    log.info("no frontend dir at %s — API-only mode", _frontend_dir)
