// FinProof — dependency-free dashboard. No framework, no build step.
// Run executes server-side on POST /reconcile; SSE streams results live;
// polling /runs/{id} is the fallback if SSE is blocked.

const API = location.protocol === "file:" || !location.hostname ? "http://localhost:8000" : "";
const $ = (id) => document.getElementById(id);

let selectedFiles = [];
let metrics = { spans: 0, latency: 0, cost: 0, faithSum: 0, faithN: 0 };
let ledgerState = { posted: [], quarantined: [] };
let currentRunId = localStorage.getItem("finproof_active_run") || null;
let activeClusters = [];
let activeDossier = null;

// ── Workspace Profile Switcher (Point 1: Who is this for?) ─────────────────
document.querySelectorAll(".ws-pill").forEach((pill) => {
  pill.addEventListener("click", () => {
    document.querySelectorAll(".ws-pill").forEach((p) => p.classList.remove("active"));
    pill.classList.add("active");
    const mode = pill.dataset.mode;
    const statusEl = $("ws-status-badge");
    if (mode === "enterprise_ap") {
      statusEl.textContent = "Compliance: SOX 404 & GST Section 16(2) | 3-Way Match Active";
    } else if (mode === "bank_recon") {
      statusEl.textContent = "Compliance: RBI Settlement Guidelines & ISO 20022 | $0.00 Tolerance";
    } else if (mode === "lending_underwrite") {
      statusEl.textContent = "Compliance: Basel III & GSTR-2B Forensic Audit | Anti-Fraud Active";
    } else {
      statusEl.textContent = "Mode: Retail / SME Autonomous Reconciliation";
    }
  });
});

// ── Health badge ───────────────────────────────────────────────────────────
const PROVIDER_SHORT = { anthropic: "Claude", google: "Gemini", openai: "GPT", mock: "Mock" };

async function refreshHealth(attempt = 0) {
  try {
    const h = await (await fetch(`${API}/health`)).json();
    const b = $("mode-badge");
    b.textContent = h.mock_mode ? "MOCK MODE" : `LIVE · ${PROVIDER_SHORT[h.provider] || h.provider}`;
    b.className = "badge " + (h.mock_mode ? "badge-mock" : "badge-live");
    b.title = `${h.provider_label || "provider"} · ${h.model || ""} · v${h.version || "?"} — click to configure`;
  } catch {
    const b = $("mode-badge");
    b.textContent = "API offline"; b.className = "badge badge-error";
    b.title = `Could not reach ${API}/health`;
    if (attempt < 3) setTimeout(() => refreshHealth(attempt + 1), 1500);
  }
}
refreshHealth();

// ── Error banner ──────────────────────────────────────────────────────────
const showError  = (msg) => { $("error-banner").textContent = msg; $("error-banner").classList.remove("hidden"); };
const clearError = ()    => $("error-banner").classList.add("hidden");

// ── File selection (click + drag/drop) ───────────────────────────────────
const dz = $("dropzone");
$("file-input").addEventListener("change", (e) => setFiles([...e.target.files]));
["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
dz.addEventListener("drop", async (e) => setFiles(await filesFromDrop(e.dataTransfer)));

// Walk dropped folders via the entries API so dragging sample_data/ uploads all files inside.
async function filesFromDrop(dt) {
  const items = dt.items;
  if (!items?.length || typeof items[0].webkitGetAsEntry !== "function") return [...dt.files];
  const roots = [...items].map((it) => it.webkitGetAsEntry()).filter(Boolean);
  const out = [];
  async function walk(entry) {
    if (entry.isFile) {
      out.push(await new Promise((res, rej) => entry.file(res, rej)));
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      let batch;
      do {
        batch = await new Promise((res, rej) => reader.readEntries(res, rej));
        for (const e of batch) await walk(e);
      } while (batch.length);
    }
  }
  for (const r of roots) await walk(r);
  return out.filter((f) => f.size > 0 && !/^\.|Thumbs\.db$/.test(f.name));
}

function setFiles(files) {
  selectedFiles = files;
  $("file-list").innerHTML = files.map((f) => `<li>${f.name}</li>`).join("");
  $("run-btn").disabled = files.length === 0;
}

// ── Load sample data & preset scenario buttons (Point 5: Enhanced Use Cases) ─
async function loadFilesByPreset(preset = null, btn = $("load-sample-btn")) {
  btn.disabled = true;
  const oldText = btn.textContent;
  btn.textContent = "Loading…";
  try {
    const url = preset ? `${API}/sample-files?preset=${preset}` : `${API}/sample-files`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const { files: paths } = await res.json();
    const fetched = await Promise.all(paths.map(async (p) => {
      const r = await fetch(`${API}/sample-file?path=${encodeURIComponent(p)}`);
      if (!r.ok) throw new Error(`Could not fetch ${p}`);
      const blob = await r.blob();
      return new File([blob], p.split("/").pop(), { type: blob.type || "text/plain" });
    }));
    setFiles(fetched);
    btn.textContent = `✓ ${fetched.length} files loaded`;
    setTimeout(() => { btn.textContent = oldText; btn.disabled = false; }, 2000);
  } catch (err) {
    btn.textContent = oldText; btn.disabled = false;
    showError(`Could not load files: ${err.message}. Drop files manually instead.`);
  }
}

$("load-sample-btn").addEventListener("click", () => loadFilesByPreset(null, $("load-sample-btn")));

document.querySelectorAll(".preset-btn").forEach((btn) => {
  btn.addEventListener("click", () => loadFilesByPreset(btn.dataset.preset, btn));
});

// ── Run ───────────────────────────────────────────────────────────────────
$("run-btn").addEventListener("click", startRun);

async function startRun() {
  resetPanels(); clearError(); $("run-btn").disabled = true;
  const form = new FormData();
  selectedFiles.forEach((f) => form.append("files", f));
  let runId;
  try {
    const res = await fetch(`${API}/reconcile`, { method: "POST", body: form });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(`POST /reconcile → HTTP ${res.status}. ${body.slice(0, 200)}`);
    }
    const data = await res.json();
    runId = data.run_id;
    if (!runId) throw new Error("Server did not return a run_id.");
  } catch (err) {
    const reachable = err instanceof Error && !/Failed to fetch|NetworkError|TypeError/.test(err.message);
    showError(reachable
      ? `Run could not start: ${err.message}`
      : `Could not reach the API at ${API || location.origin}. Is the backend running?`);
    $("run-btn").disabled = false;
    return;
  }
  streamRun(runId);
  currentRunId = runId;
  localStorage.setItem("finproof_active_run", runId);
}

function streamRun(runId) {
  let settled = false, opened = false;
  const es = new EventSource(`${API}/events/${runId}`);
  const on = (type, fn) => es.addEventListener(type, (e) => fn(JSON.parse(e.data)));

  es.onopen = () => { opened = true; };
  on("run.started", () => {});
  on("agent.cell.start", (p) => upsertCell(p.doc, "running"));
  on("agent.cell.done",  (p) => upsertCell(p.doc, "done", p));
  on("trace", addTrace);
  on("drift", showDrift);
  on("txn.posted", (p) => { ledgerState.posted.push(p); addCard("posted-list", "posted", p.normalized_merchant || p.merchant, `₹${p.amount}`, "Posted to ledger", false, p); });
  on("txn.quarantined", (p) => { ledgerState.quarantined.push(p); addCard("quarantine-list", "quarantine", p.normalized_merchant || p.merchant, `⚠ ₹${p.amount}`, p.reason || p.quarantine_reason, false, p); });
  on("txn.enriched", (p) => {
    // Update existing card with normalized name, category, and possibly a new reason
    const card = document.querySelector(`.card[data-id="${p.id}"]`);
    if (card) {
      card.querySelector(".merchant").innerHTML = `${p.normalized_merchant} <span class="cat-badge">${p.category}</span>`;
      if (p.quarantine_reason) card.querySelector(".why").textContent = p.quarantine_reason;
    }
  });
  on("run.narrative", (p) => {
    $("ai-insights").classList.remove("hidden");
    $("ai-narrative").textContent = p.narrative;
  });
  on("canvas.duplicate", (p) => addCard("links-list", "duplicate", "DUPLICATE", `${(p.score * 100) | 0}%`, p.detail));
  on("canvas.anomaly",   (p) => addCard("links-list", "anomaly", "ANOMALY", `${(p.score * 100) | 0}%`, p.detail, true));
  on("canvas.3way_match", (p) => addCard("links-list", "posted", "3-WAY MATCH", `${(p.score * 100) | 0}%`, p.detail));
  on("canvas.quantity_discrepancy", (p) => addCard("links-list", "anomaly", "SHORT-SHIPMENT", `⚠ DIFF`, p.detail, true));
  on("run.completed", (p) => {
    settled = true;
    activeClusters = p.clusters || [];
    activeDossier = p.audit_dossier || null;
    renderClusters(activeClusters);
    showSummary(p);
    es.close();
    $("run-btn").disabled = false;
  });
  on("run.failed", (p) => { settled = true; es.close(); showError(`Run failed: ${p.error || "unknown error"}`); $("run-btn").disabled = false; });

  es.onerror = () => {
    if (settled) { es.close(); return; }
    if (!opened) { es.close(); pollForResult(runId); }
  };
}

// ── Polling fallback ──────────────────────────────────────────────────────
async function pollForResult(runId, attempt = 0) {
  if (attempt === 0) showError("Live stream unavailable — falling back to polling…");
  try {
    const res = await fetch(`${API}/runs/${runId}`);
    if (res.status === 202) return setTimeout(() => pollForResult(runId, attempt + 1), 800);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    clearError(); renderResult(await res.json()); $("run-btn").disabled = false;
  } catch (err) {
    if (attempt < 40) return setTimeout(() => pollForResult(runId, attempt + 1), 800);
    showError(`Gave up polling: ${err.message}`); $("run-btn").disabled = false;
  }
}

function renderResult(r) {
  resetPanels();
  if (r.run_id) {
    currentRunId = r.run_id;
    localStorage.setItem("finproof_active_run", r.run_id);
  }
  ledgerState = { posted: r.posted || [], quarantined: r.quarantined || [] };
  activeClusters = r.clusters || [];
  activeDossier = r.audit_dossier || null;
  renderClusters(activeClusters);
  (r.links || []).forEach((l) => {
    if (l.kind === "duplicate") addCard("links-list", "duplicate", "DUPLICATE", `${(l.score * 100) | 0}%`, l.detail);
    if (l.kind === "anomaly")   addCard("links-list", "anomaly",   "ANOMALY",   `${(l.score * 100) | 0}%`, l.detail, true);
    if (l.kind === "3way_match") addCard("links-list", "posted", "3-WAY MATCH", `${(l.score * 100) | 0}%`, l.detail || `Matched PO/GRN with variance ₹${l.variance_amount || 0}`);
    if (l.kind === "quantity_discrepancy") addCard("links-list", "anomaly", "SHORT-SHIPMENT", `⚠ DIFF`, l.detail || `Quantity discrepancy detected`, true);
  });
  (r.posted     || []).forEach((t) => addCard("posted-list",    "posted",    t.normalized_merchant || t.merchant, `₹${t.amount}`,   "Posted to ledger", false, t));
  (r.quarantined|| []).forEach((t) => addCard("quarantine-list","quarantine",t.normalized_merchant || t.merchant, `⚠ ₹${t.amount}`, t.quarantine_reason || t.reason || "Quarantined", false, t));
  
  if (r.narrative) {
    $("ai-insights").classList.remove("hidden");
    $("ai-narrative").textContent = r.narrative;
  }
  
  showSummary({ total_posted_amount: r.total_posted_amount, posted: (r.posted||[]).length,
    quarantined: (r.quarantined||[]).length, links: (r.links||[]).length, documents: r.documents, audit_seal: r.audit_seal });
}

// ── Renderers ────────────────────────────────────────────────────────────
function upsertCell(doc, state, p = {}) {
  let cell = document.querySelector(`[data-doc="${cssEscape(doc)}"]`);
  if (!cell) {
    cell = document.createElement("div");
    cell.className = "agent-cell"; cell.dataset.doc = doc;
    cell.innerHTML = `<span class="dot"></span><div class="doc">${escapeHtml(doc)}</div><div class="meta"></div>`;
    $("agent-grid").appendChild(cell);
    $("agent-count").textContent = $("agent-grid").children.length;
  }
  cell.querySelector(".dot").className = "dot " + state;
  if (state === "done") {
    cell.classList.add("done");
    const isDet = (p.agent_type === "deterministic") || (p.worker && p.worker.includes("deterministic")) || (p.worker === "csv-worker" || p.worker === "csv" || p.model === "deterministic");
    const badgeClass = isDet ? "badge-deterministic" : "badge-non-det";
    const badgeLabel = isDet ? "⚡ DETERMINISTIC" : "🤖 NON-DET LLM";
    const proofText = p.proof_method ? ` · ${p.proof_method}` : "";
    cell.querySelector(".meta").innerHTML =
      `<div><span>${p.worker || ""}</span><span>${p.latency_ms ?? 0}ms</span>` +
      `<span>${p.count ?? 1} txn</span><span>faith ${(p.faithfulness ?? 1).toFixed(2)}</span></div>` +
      `<span class="agent-type-badge ${badgeClass}" title="Execution Architecture">${badgeLabel}${proofText}</span>`;
  }
}

// ── Evidence & Variance Diff Modal (Point 2: HITL Manual Intervention) ────
const diffOverlay = $("diff-modal-overlay");
const closeDiffModal = () => { if (diffOverlay) diffOverlay.classList.add("hidden"); };

if ($("diff-modal-close")) $("diff-modal-close").addEventListener("click", closeDiffModal);
if ($("diff-modal-dismiss")) $("diff-modal-dismiss").addEventListener("click", closeDiffModal);
if (diffOverlay) diffOverlay.addEventListener("click", (e) => { if (e.target === diffOverlay) closeDiffModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && diffOverlay && !diffOverlay.classList.contains("hidden")) closeDiffModal(); });

async function openDiffModal(txn) {
  if (!txn) return;
  const bodyEl = $("diff-modal-body");
  if (!bodyEl) return;

  if ($("diff-title")) $("diff-title").innerHTML = "🔍 Side-by-Side Dual-Document Evidence & Impact Inspector";
  
  bodyEl.innerHTML = `<div style="padding: 28px; text-align: center; color: var(--muted);"><span class="spinner"></span> Loading source documents and financial impact analysis…</div>`;
  diffOverlay.classList.remove("hidden");

  let diffData = null;
  if (currentRunId && txn.id) {
    try {
      const res = await fetch(`${API}/runs/${currentRunId}/quarantine/${txn.id}/evidence-diff`);
      if (res.ok) {
        diffData = await res.json();
      }
    } catch (e) {
      console.warn("Could not fetch evidence diff from API, falling back to local metadata", e);
    }
  }

  // Fallback to local transaction object if API unavailable
  if (!diffData || !diffData.primary) {
    const d = txn.discrepancy || {};
    const deltaVal = d.delta || "0.00";
    const variancePct = d.variance_percent != null ? d.variance_percent : 0.0;
    diffData = {
      primary: {
        doc_name: txn.source_doc || "Uploaded Document",
        source_type: txn.source_type || "receipt",
        merchant: txn.normalized_merchant || txn.merchant || "Unknown",
        amount: String(txn.amount || "0.00"),
        txn_date: String(txn.txn_date || "N/A"),
        reference_no: txn.reference_no || "N/A",
        full_preview: txn.raw_snippet || d.primary_snippet || "Document content recorded in quarantine vault."
      },
      counterpart: {
        doc_name: d.counterpart_doc || "Counterpart Statement / Ledger Record",
        source_type: d.counterpart_type || "bank_feed",
        merchant: d.counterpart_merchant || txn.merchant || "Counterpart",
        amount: d.counterpart_amount || String(txn.amount || "0.00"),
        txn_date: d.counterpart_date || String(txn.txn_date || "N/A"),
        reference_no: d.counterpart_ref || "N/A",
        full_preview: d.counterpart_snippet || "Counterpart document content recorded in quarantine vault."
      },
      variance: {
        delta: deltaVal,
        variance_percent: variancePct,
        what: d.what || txn.quarantine_reason || "Amount mismatch across sources",
        where: d.where || "Quarantine Lane",
        how_to_resolve: d.how_to_resolve || "Review variance against source documents.",
        root_cause_code: txn.root_cause_code || d.root_cause_code || "ERR_PRICE_VARIANCE"
      },
      accept_impact: d.accept_impact || {
        gl_narration: `Recognize liability ₹${txn.amount} and post balancing variance of ₹${deltaVal} to General Ledger.`,
        itc_treatment: "Claim Input Tax Credit under GST Sec 16(2); variance absorbed in operational P&L.",
        treasury_verdict: "Unblocks AP disbursement; generates approved payment voucher."
      },
      reject_impact: d.reject_impact || {
        vendor_action: `Dispute Notice served to vendor for ₹${deltaVal} variance.`,
        tax_safety: "Withholds GST ITC claim to avoid tax disallowance and penalty under Rule 36(4)."
      },
      actions: [
        { label: "✓ Approve & Post GL Entry", action: "override", amount: parseFloat(txn.amount) },
        { label: "⚡ Split Fee / Surcharge", action: "split_fee", amount: parseFloat(txn.amount), fee_amount: parseFloat(deltaVal) },
        { label: "✕ Reject & Freeze Payment", action: "reject" }
      ]
    };
  }

  const p = diffData.primary;
  const c = diffData.counterpart;
  const v = diffData.variance;
  const ai = diffData.accept_impact;
  const ri = diffData.reject_impact;

  function highlightSnippet(text) {
    if (!text) return '<span style="color: var(--muted); font-style: italic;">No raw text recorded</span>';
    const lines = escapeHtml(text).split('\n');
    return lines.map(line => {
      if (line.includes('DISCREPANCY') || line.includes('Shortage') || line.includes('short-shipped') || line.includes('TOTAL') || line.includes('Total') || line.includes('Subtotal')) {
        return `<span class="diff-line-highlight">${line}</span>`;
      }
      return line;
    }).join('\n');
  }

  const rcLabel = v.root_cause_code === "ERR_QTY_SHORT_SHIPMENT" ? "SHORT-SHIPMENT VARIANCE"
    : v.root_cause_code === "ERR_UNRECORDED_FEE" ? "UNRECORDED FEE / SURCHARGE"
    : v.root_cause_code === "ERR_OCR_UNREADABLE" ? "LOW OCR CONFIDENCE"
    : v.root_cause_code === "ERR_SCHEMA_DRIFT" ? "HEADER SCHEMA DRIFT" : "PRICE & AMOUNT VARIANCE";

  bodyEl.innerHTML = `
    <!-- Dual-Document Side-by-Side Panes -->
    <div class="diff-split-container">
      <div class="diff-doc-pane">
        <div class="diff-doc-head">
          <span class="diff-doc-name">📄 ${escapeHtml(p.doc_name)}</span>
          <span class="diff-doc-amt">₹${escapeHtml(p.amount)}</span>
        </div>
        <div class="diff-doc-meta">
          <span><b>Type:</b> ${escapeHtml(p.source_type).toUpperCase()}</span>
          <span><b>Date:</b> ${escapeHtml(p.txn_date)}</span>
          <span><b>Ref:</b> #${escapeHtml(p.reference_no)}</span>
        </div>
        <div class="diff-doc-body">${highlightSnippet(p.full_preview || p.snippet)}</div>
      </div>

      <div class="diff-doc-pane">
        <div class="diff-doc-head">
          <span class="diff-doc-name">📄 ${escapeHtml(c.doc_name)}</span>
          <span class="diff-doc-amt">₹${escapeHtml(c.amount)}</span>
        </div>
        <div class="diff-doc-meta">
          <span><b>Type:</b> ${escapeHtml(c.source_type).toUpperCase()}</span>
          <span><b>Date:</b> ${escapeHtml(c.txn_date)}</span>
          <span><b>Ref:</b> #${escapeHtml(c.reference_no)}</span>
        </div>
        <div class="diff-doc-body">${highlightSnippet(c.full_preview || c.snippet)}</div>
      </div>
    </div>

    <!-- Center Variance Bridge -->
    <div class="variance-bridge-bar">
      <div class="variance-bridge-delta">
        <span style="font-size: 12px; color: var(--muted); text-transform: uppercase;">Reconciliation Delta:</span>
        <span class="variance-val">Δ ₹${escapeHtml(v.delta)}</span>
        <span class="variance-pct">${escapeHtml(v.variance_percent)}% Variance</span>
      </div>
      <div>
        <span class="badge-root-cause qty">${escapeHtml(rcLabel)}</span>
      </div>
    </div>

    <!-- Financial Impact Consequence Grid -->
    <div class="impact-consequence-grid">
      <div class="impact-box accept">
        <div class="impact-header">
          <span>🟢 WHAT ACCEPTING DOES</span>
          <span style="font-size: 10px; font-family: var(--font-mono);">POST TO GENERAL LEDGER</span>
        </div>
        <div class="impact-detail-row">
          <span class="impact-label">Double-Entry Booking (GL)</span>
          <div class="gl-entry-preview">
            DR 6100 - Vendor Expense / Inventory: ₹${escapeHtml(p.amount)}<br>
            CR 2010 - Accounts Payable / Bank: ₹${escapeHtml(p.amount)}
          </div>
        </div>
        <div class="impact-detail-row">
          <span class="impact-label">GST / Tax Status</span>
          <span class="impact-value">${escapeHtml(ai.itc_treatment || "Claims Input Tax Credit under Section 16(2); discrepancy absorbed in P&L.")}</span>
        </div>
        <div class="impact-detail-row">
          <span class="impact-label">Treasury Directive</span>
          <span class="impact-value" style="color: var(--green); font-weight: 700;">✓ ${escapeHtml(ai.treasury_verdict || "Unblocks AP disbursement; generates approved payment voucher.")}</span>
        </div>
      </div>

      <div class="impact-box reject">
        <div class="impact-header">
          <span>🔴 WHAT REJECTING DOES</span>
          <span style="font-size: 10px; font-family: var(--font-mono);">TREASURY PAYMENT FREEZE</span>
        </div>
        <div class="impact-detail-row">
          <span class="impact-label">Cash Outflow Protection</span>
          <div class="gl-entry-preview" style="color: #ff9e7a;">
            DISBURSEMENT FROZEN IN TREASURY · NO LIABILITY BOOKED
          </div>
        </div>
        <div class="impact-detail-row">
          <span class="impact-label">Vendor Dispute Action</span>
          <span class="impact-value">${escapeHtml(ri.vendor_action || `Dispute Notice served to vendor for ₹${v.delta} variance.`)}</span>
        </div>
        <div class="impact-detail-row">
          <span class="impact-label">Tax Safety Safeguard</span>
          <span class="impact-value" style="color: var(--amber); font-weight: 600;">⚠ ${escapeHtml(ri.tax_safety || "Withholds GST ITC claim to avoid tax disallowance under Rule 36(4).")}</span>
        </div>
      </div>
    </div>

    <!-- Live Execution Actions inside Modal -->
    <div class="diff-modal-actions">
      <button type="button" class="diff-action-btn accept" id="modal-act-accept">✓ Approve & Post GL Entry</button>
      <button type="button" class="diff-action-btn split" id="modal-act-split">⚡ Split Fee / Debit Note (₹${escapeHtml(v.delta)})</button>
      <button type="button" class="diff-action-btn reject" id="modal-act-reject">✕ Reject & Freeze Payment</button>
      <button type="button" class="diff-action-btn dismiss" id="modal-act-dismiss">Dismiss</button>
    </div>
  `;

  // Wire up action buttons
  const cardEl = document.querySelector(`.card[data-id="${txn.id}"]`) || document.querySelector(`#quarantine-list .card`);

  const btnAccept = $("modal-act-accept");
  if (btnAccept) {
    btnAccept.addEventListener("click", async () => {
      btnAccept.disabled = true;
      btnAccept.textContent = "Posting...";
      await applyResolution(cardEl, txn, {
        action: "override",
        amount: parseFloat(p.amount),
        merchant: p.merchant,
        label: "Approved via Side-by-Side Inspector"
      });
      closeDiffModal();
    });
  }

  const btnSplit = $("modal-act-split");
  if (btnSplit) {
    btnSplit.addEventListener("click", async () => {
      btnSplit.disabled = true;
      btnSplit.textContent = "Processing...";
      const isFee = (v.root_cause_code || "").includes("FEE");
      await applyResolution(cardEl, txn, {
        action: isFee ? "split_fee" : "debit_note",
        amount: parseFloat(p.amount),
        fee_amount: parseFloat(v.delta),
        fee_label: isFee ? "Service Fee / Surcharge" : "Shortage Debit Note",
        label: isFee ? "Fee Split via Inspector" : "Debit Note via Inspector"
      });
      closeDiffModal();
    });
  }

  const btnReject = $("modal-act-reject");
  if (btnReject) {
    btnReject.addEventListener("click", async () => {
      btnReject.disabled = true;
      btnReject.textContent = "Rejecting...";
      await applyResolution(cardEl, txn, {
        action: "reject",
        label: "Rejected & Payment Frozen"
      });
      closeDiffModal();
    });
  }

  const btnDismiss = $("modal-act-dismiss");
  if (btnDismiss) {
    btnDismiss.addEventListener("click", closeDiffModal);
  }
}

function addCard(listId, kind, title, right, why, flash = false, txn = null) {
  const el = document.createElement("div");
  el.className = `card ${kind}${flash ? " flash" : ""}`;
  if (txn && txn.id) el.dataset.id = txn.id;
  const isQ = kind === "quarantine" && txn;
  
  const catHtml = (txn && txn.category && txn.category !== "Other") ? ` <span class="cat-badge">${txn.category}</span>` : "";
  const refHtml = txn && txn.reference_no ? `<span class="ref-badge" title="Reference No">#${escapeHtml(txn.reference_no)}</span> ` : "";
  const rc = (txn && txn.root_cause_code) || (txn && txn.discrepancy && txn.discrepancy.root_cause_code);
  let rcBadgeHtml = "";
  if (rc === "ERR_QTY_SHORT_SHIPMENT") {
    rcBadgeHtml = `<span class="badge-root-cause qty">SHORT-SHIPMENT</span>`;
  } else if (rc === "ERR_UNRECORDED_FEE") {
    rcBadgeHtml = `<span class="badge-root-cause fee">UNRECORDED FEE</span>`;
  } else if (rc === "ERR_OCR_UNREADABLE") {
    rcBadgeHtml = `<span class="badge-root-cause ocr">LOW OCR</span>`;
  } else if (rc === "ERR_SCHEMA_DRIFT") {
    rcBadgeHtml = `<span class="badge-root-cause drift">SCHEMA DRIFT</span>`;
  }
  
  el.innerHTML =
    `<div class="row"><span class="merchant">${refHtml}${title}${catHtml}${rcBadgeHtml}</span><span class="amount">${right}</span></div>` +
    `<div class="why">${why}</div>` +
    (isQ ? `<div class="review-actions">
      <button class="review-btn diff-btn" title="View Evidence Diff & Variance Details">🔍 Evidence Diff</button>
      <button class="review-btn approve" title="Approve — move to Posted ledger">✓ Approve</button>
      <button class="review-btn reject"  title="Reject — permanently dismiss">✕ Reject</button>
      <button class="review-btn suggest-btn" title="Ask AI how to resolve this conflict">✨ AI Suggest</button>
    </div>
    <div class="ai-suggestion-box hidden"></div>` : "");
  if (isQ) {
    const diffBtn = el.querySelector(".diff-btn");
    if (diffBtn) diffBtn.addEventListener("click", () => openDiffModal(txn));
    el.querySelector(".approve").addEventListener("click", () => approveCard(el, txn));
    el.querySelector(".reject" ).addEventListener("click", () => rejectCard(el, txn));
    el.querySelector(".suggest-btn").addEventListener("click", (e) => suggestResolve(el, txn, e.target));
  }
  $(listId).appendChild(el);
}

async function suggestResolve(cardEl, txn, suggestBtn) {
  let suggestionBox = cardEl.querySelector(".ai-suggestion-box");
  if (!suggestionBox) return;
  
  suggestBtn.disabled = true;
  suggestBtn.textContent = "Analyzing…";
  suggestionBox.innerHTML = `<div class="tok-dim">Asking AI accountant for advice…</div>`;
  suggestionBox.classList.remove("hidden");

  try {
    const res = await fetch(`${API}/runs/${currentRunId}/quarantine/${txn.id}/suggest`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    
    suggestBtn.textContent = "✨ Suggested";
    
    let buttonsHtml = (data.actions || []).map((act, idx) => {
      return `<button type="button" class="ai-action-btn" data-idx="${idx}">
        ${act.label}
      </button>`;
    }).join("");
    
    const learnedBadge = data.learned_rule
      ? `<span class="badge-root-cause" style="background: rgba(0, 180, 216, 0.2); color: var(--accent); border: 1px solid var(--accent); margin-left: 6px;">🧠 ACTIVE LEARNING RULE</span>`
      : "";

    suggestionBox.innerHTML = `
      <div class="ai-suggestion-title">💡 AI Recommendation ${learnedBadge}</div>
      <div class="ai-suggestion-text">${escapeHtml(data.explanation)}</div>
      <div class="ai-action-buttons">${buttonsHtml}</div>
    `;
    
    // Bind click handlers to action buttons
    suggestionBox.querySelectorAll(".ai-action-btn").forEach(btn => {
      btn.addEventListener("click", async () => {
        const act = data.actions[parseInt(btn.dataset.idx)];
        await applyResolution(cardEl, txn, act);
      });
    });
  } catch (err) {
    suggestBtn.disabled = false;
    suggestBtn.textContent = "✨ AI Suggest";
    suggestionBox.innerHTML = `<div style="color: var(--red)">Failed to load suggestion: ${err.message}</div>`;
  }
}

async function applyResolution(cardEl, txn, act) {
  // Disable all review buttons
  cardEl.querySelectorAll(".review-btn, .ai-action-btn").forEach(b => b.disabled = true);
  
  try {
    const res = await fetch(`${API}/runs/${currentRunId}/quarantine/${txn.id}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(act)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    
    if (act.action === "override" || act.action === "split_fee" || act.action === "debit_note") {
      cardEl.remove();
      ledgerState.quarantined = ledgerState.quarantined.filter((t) => t.id !== (txn?.id));
      
      const primaryTxn = data.resolved_txn || {
        ...txn,
        amount: act.amount ?? txn.amount,
        merchant: act.merchant ?? txn.merchant,
        normalized_merchant: act.merchant ?? txn.normalized_merchant ?? txn.merchant,
        category: act.category ?? txn.category,
        _humanApproved: true
      };
      ledgerState.posted.push(primaryTxn);
      addCard(
        "posted-list",
        "posted",
        primaryTxn.normalized_merchant || primaryTxn.merchant,
        `₹${primaryTxn.amount}`,
        `✓ Resolved: ${act.label || 'Approved'}`,
        false,
        primaryTxn
      );

      if (data.balancing_entry) {
        ledgerState.posted.push(data.balancing_entry);
        addCard(
          "posted-list",
          "posted",
          data.balancing_entry.normalized_merchant || data.balancing_entry.merchant,
          `₹${data.balancing_entry.amount}`,
          `✓ Balancing Entry: ${data.balancing_entry.merchant}`,
          false,
          data.balancing_entry
        );
      }
    } else if (act.action === "reject") {
      Object.assign(cardEl.style, { transition: "opacity .3s, max-height .3s", opacity: "0", maxHeight: "0", overflow: "hidden" });
      setTimeout(() => {
        cardEl.remove();
        ledgerState.quarantined = ledgerState.quarantined.filter((t) => t.id !== (txn?.id));
        refreshSummaryFromState();
      }, 300);
    }
    refreshSummaryFromState();
  } catch (err) {
    alert(`Could not resolve transaction: ${err.message}`);
    cardEl.querySelectorAll(".review-btn, .ai-action-btn").forEach(b => b.disabled = false);
  }
}

function approveCard(cardEl, txn) {
  applyResolution(cardEl, txn, {
    action: "override",
    amount: txn.amount,
    merchant: txn.merchant,
    category: txn.category,
    label: "Approved manual"
  });
}

function rejectCard(cardEl, txn) {
  applyResolution(cardEl, txn, {
    action: "reject"
  });
}

function refreshSummaryFromState() {
  updateSummaryContent({
    total_posted_amount: ledgerState.posted.reduce((s, t) => s + parseFloat(t.amount || 0), 0).toFixed(2),
    posted: ledgerState.posted.length, quarantined: ledgerState.quarantined.length,
    links: document.querySelectorAll("#links-list .card").length,
    documents: document.querySelectorAll(".agent-cell").length,
  });
}

function addTrace(t) {
  metrics.spans++; metrics.latency += t.latency_ms || 0; metrics.cost += t.usd_cost || 0;
  if (typeof t.faithfulness === "number") { metrics.faithSum += t.faithfulness; metrics.faithN++; }
  $("m-spans").textContent   = metrics.spans;
  $("m-latency").innerHTML   = `${metrics.latency}<small>ms</small>`;
  $("m-cost").textContent    = `$${metrics.cost.toFixed(4)}`;
  $("m-faith").textContent   = metrics.faithN ? (metrics.faithSum / metrics.faithN).toFixed(2) : "—";
  const el = document.createElement("div");
  el.className = "trace";
  el.innerHTML = `<span class="span">${t.span}</span> <span class="kv">· ${t.model} · ${t.latency_ms}ms · ` +
    `${t.tokens_in + t.tokens_out} tok · $${(t.usd_cost || 0).toFixed(4)} · faith ${(t.faithfulness ?? 1).toFixed(2)}</span>`;
  const log = $("trace-log");
  log.insertBefore(el, log.firstChild);
}

function showDrift(p) {
  const b = $("drift-banner"); b.classList.remove("hidden");
  b.innerHTML = `⚡ <b>Schema drift detected</b> in ${p.doc} — headers ${JSON.stringify(p.headers)}. ` +
    `Remap confidence ${(p.confidence * 100) | 0}% → <b>${p.action}</b>.`;
}

function showSummary(p) {
  $("summary").classList.remove("hidden");
  $("chat-panel").classList.remove("hidden");
  updateSummaryContent(p); 
  $("export-btn").classList.remove("hidden");
  if ($("dossier-btn")) $("dossier-btn").classList.remove("hidden");
  if ($("dossier-nav-btn")) $("dossier-nav-btn").classList.remove("hidden");
}

function updateSummaryContent(p) {
  const sealHtml = p.audit_seal
    ? `<div class="audit-seal-box">
         <span>🛡️ Cryptographic Ledger Seal (SHA-256 Invariance):</span>
         <span class="seal-hash" title="${escapeHtml(p.audit_seal)}">${escapeHtml(p.audit_seal.slice(0, 24))}…</span>
       </div>`
    : "";
  $("summary-content").innerHTML =
    `<div><b class="ok">₹${p.total_posted_amount}</b><div>posted total</div></div>` +
    `<div><b>${p.posted}</b> posted · <b class="warn">${p.quarantined}</b> quarantined · <b>${p.links}</b> links</div>` +
    `<div>${p.documents} documents reconciled</div>` +
    sealHtml;
}

// ── Export CSV ────────────────────────────────────────────────────────────
$("export-btn").addEventListener("click", () => {
  const cols = ["Status", "Merchant", "Amount (INR)", "Date", "Source", "Confidence", "Reason"];
  const rows = [cols,
    ...ledgerState.posted.map((t) => [t._humanApproved ? "POSTED (Human-approved)" : "POSTED",
      t.merchant||"", t.amount||"", t.txn_date||"", t.source_doc||"", t.confidence ? JSON.stringify(t.confidence) : "", ""]),
    ...ledgerState.quarantined.map((t) => ["QUARANTINED",
      t.merchant||"", t.amount||"", t.txn_date||"", t.source_doc||"", t.confidence ? JSON.stringify(t.confidence) : "", t.quarantine_reason||t.reason||""]),
  ];
  const csv = rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n");
  const a = Object.assign(document.createElement("a"), {
    href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })),
    download: `finproof-ledger-${new Date().toISOString().slice(0, 10)}.csv`,
  });
  a.click(); URL.revokeObjectURL(a.href);
});

function resetPanels() {
  ["agent-grid", "posted-list", "quarantine-list", "links-list", "clusters-list", "trace-log", "chat-history"].forEach((id) => {
    const el = $(id); if (el) el.innerHTML = "";
  });
  $("agent-count").textContent = "0";
  ["drift-banner", "summary", "export-btn", "ai-insights", "chat-panel", "clusters-section", "dossier-nav-btn", "dossier-btn"].forEach((id) => {
    const el = $(id); if (el) el.classList.add("hidden");
  });
  $("ai-narrative").textContent = "";
  ledgerState = { posted: [], quarantined: [] };
  activeClusters = [];
  activeDossier = null;
  metrics = { spans: 0, latency: 0, cost: 0, faithSum: 0, faithN: 0 };
}

function cssEscape(s) { return s.replace(/"/g, '\\"'); }

// ── Provider settings modal ───────────────────────────────────────────────
let providersCatalog = [];
const overlay     = $("settings-overlay");
const openSettings  = () => { overlay.classList.remove("hidden"); loadConfig(); };
const closeSettings = () => { overlay.classList.add("hidden"); cfgMsg("", null); $("cfg-key").value = ""; };

$("settings-btn")  .addEventListener("click", openSettings);
$("mode-badge")    .addEventListener("click", openSettings);
$("settings-close").addEventListener("click", closeSettings);
$("cfg-cancel")    .addEventListener("click", closeSettings);
overlay.addEventListener("click", (e) => { if (e.target === overlay) closeSettings(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !overlay.classList.contains("hidden")) closeSettings(); });

function cfgMsg(text, ok) {
  const m = $("cfg-msg");
  if (!text) { m.classList.add("hidden"); return; }
  m.classList.remove("hidden");
  m.className = "cfg-msg " + (ok === true ? "ok" : ok === false ? "err" : "info");
  m.textContent = text;
}

async function loadConfig() {
  cfgMsg("Loading…", null);
  try {
    const [prov, cfg] = await Promise.all([
      (await fetch(`${API}/providers`)).json(),
      (await fetch(`${API}/config`)).json(),
    ]);
    providersCatalog = prov.providers;
    const sel = $("cfg-provider");
    sel.innerHTML = providersCatalog.map((p) => `<option value="${p.id}">${p.label}</option>`).join("");
    sel.value = cfg.provider;
    sel.onchange = () => paintProvider(sel.value, cfg);
    paintProvider(cfg.provider, cfg); cfgMsg("", null);
  } catch (err) {
    cfgMsg(`Couldn't load configuration: ${err.message}. Is the backend running?`, false);
  }
}

function populateModels(list, selFast, selDeep) {
  const opts = (list || []).map((m) => `<option value="${m.id}">${m.label || m.id}</option>`).join("");
  const fast = $("cfg-fast"), deep = $("cfg-deep");
  fast.innerHTML = deep.innerHTML = opts;
  const ids = (list || []).map((m) => m.id);
  fast.value = ids.includes(selFast) ? selFast : ids[0] || "";
  deep.value = ids.includes(selDeep) ? selDeep : fast.value;
  fast.disabled = deep.disabled = ids.length === 0;
}

function paintProvider(pid, cfg) {
  const p = providersCatalog.find((x) => x.id === pid) || {};
  const { requires_key: needsKey, key_configured: configured } = p;
  const saved = cfg?.models?.[pid] || {};
  populateModels(p.models || [], saved.fast || p.selected_fast || p.default_fast || "", saved.deep || p.selected_deep || p.default_deep || "");
  $("cfg-key").disabled = $("cfg-fetch").disabled = !needsKey;
  $("cfg-key").placeholder = !needsKey ? "no key needed — deterministic mock mode"
    : configured ? "key configured ✓ — paste to replace" : "paste API key…";
  const status = $("cfg-key-status");
  status.textContent = !needsKey ? "" : configured ? "configured ✓" : "not set";
  status.className = "key-status " + (!needsKey ? "" : configured ? "ok" : "warn");
  const docs = $("cfg-key-docs");
  if (needsKey && p.docs_url) { docs.href = p.docs_url; docs.style.display = ""; }
  else { docs.style.display = "none"; }
  if (!needsKey || configured) fetchModels({ silent: true });
}

async function fetchModels({ silent = false } = {}) {
  const provider = $("cfg-provider").value;
  const p = providersCatalog.find((x) => x.id === provider) || {};
  if (!p.requires_key) { populateModels([{ id: "mock", label: "Deterministic mock" }], "mock", "mock"); return; }
  const typedKey = $("cfg-key").value.trim();
  if (!typedKey && !p.key_configured) { if (!silent) cfgMsg("Enter an API key, then fetch its models.", false); return; }
  if (!silent) cfgMsg("Fetching available models…", null);
  try {
    const res = await fetch(`${API}/providers/${provider}/models`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(typedKey ? { api_key: typedKey } : {}),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    if (!data.ok) throw new Error(data.error || "could not list models");
    if (!data.models.length) throw new Error("this key returned no usable models");
    populateModels(data.models, $("cfg-fast").value, $("cfg-deep").value);
    if (!silent) cfgMsg(`Loaded ${data.models.length} models for this key.`, true);
  } catch (err) {
    if (!silent) cfgMsg(`Couldn't fetch models: ${err.message}`, false);
  }
}

function gatherConfig() {
  const provider = $("cfg-provider").value;
  const body = { provider, fast_model: $("cfg-fast").value, deep_model: $("cfg-deep").value || $("cfg-fast").value };
  const key = $("cfg-key").value.trim();
  if (key) body.api_key = key;
  return body;
}

$("cfg-fetch").addEventListener("click", () => fetchModels());

$("cfg-test").addEventListener("click", async () => {
  cfgMsg("Testing connection…", null);
  try {
    const res = await fetch(`${API}/config/test`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: $("cfg-provider").value,
        api_key: $("cfg-key").value.trim() || undefined, model: $("cfg-fast").value.trim() || undefined }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    data.ok ? cfgMsg(`✓ ${data.provider} reachable · ${data.model} · ${data.latency_ms}ms`, true)
            : cfgMsg(`✗ ${data.error}`, false);
  } catch (err) { cfgMsg(`✗ ${err.message}`, false); }
});

$("cfg-save").addEventListener("click", async () => {
  const provider = $("cfg-provider").value;
  const p = providersCatalog.find((x) => x.id === provider) || {};
  const hasKey = $("cfg-key").value.trim() || p.key_configured;
  if (p.requires_key && !hasKey)          return cfgMsg("Add an API key for this provider first.", false);
  if (p.requires_key && !$("cfg-fast").value) return cfgMsg("Fetch models and select one before saving.", false);
  cfgMsg("Saving…", null);
  try {
    const res = await fetch(`${API}/config`, { method: "PUT",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(gatherConfig()) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    cfgMsg(data.mock_mode ? "Saved. Running in deterministic mock mode."
      : `Saved. Live on ${data.provider_label} · ${data.fast_model}.`, true);
    $("cfg-key").value = ""; refreshHealth(); setTimeout(closeSettings, 1100);
  } catch (err) { cfgMsg(`Couldn't save: ${err.message}`, false); }
});

// ── Conversational Query Box ──────────────────────────────────────────────
$("chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("chat-input");
  const q = input.value.trim();
  if (!q) return;
  input.value = "";
  
  const history = $("chat-history");
  const userMsg = document.createElement("div");
  userMsg.className = "chat-msg user";
  userMsg.textContent = q;
  history.appendChild(userMsg);
  history.scrollTop = history.scrollHeight;

  const botMsg = document.createElement("div");
  botMsg.className = "chat-msg assistant";
  botMsg.textContent = "Thinking...";
  history.appendChild(botMsg);
  history.scrollTop = history.scrollHeight;

  try {
    const res = await fetch(`${API}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, run_id: currentRunId })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    
    botMsg.textContent = data.answer;
    
    // Check if chart is returned in the response
    if (data.chart) {
      renderChatChart(data.chart, botMsg);
    }
  } catch (err) {
    botMsg.textContent = `Could not answer: ${err.message}`;
    botMsg.style.color = "var(--red)";
  }
  history.scrollTop = history.scrollHeight;
});

function renderChatChart(chartData, container) {
  const chartEl = document.createElement("div");
  chartEl.className = "chat-chart";
  
  const title = document.createElement("div");
  title.className = "chat-chart-title";
  title.textContent = chartData.type === "bar" ? "Spending Breakdown" : "Financial Visualization";
  chartEl.appendChild(title);
  
  const barsContainer = document.createElement("div");
  barsContainer.className = "chat-chart-bars";
  
  const data = chartData.data || [];
  const maxVal = Math.max(...data.map(d => d.value), 1);
  
  data.forEach(item => {
    const pct = ((item.value / maxVal) * 100).toFixed(0);
    const row = document.createElement("div");
    row.className = "chat-chart-row";
    row.innerHTML = `
      <div class="chat-chart-labels">
        <span class="chat-chart-label">${item.label}</span>
        <span class="chat-chart-value">₹${item.value.toFixed(2)}</span>
      </div>
      <div class="chat-chart-bar-container">
        <div class="chat-chart-bar-value" style="width: 0%"></div>
      </div>
    `;
    barsContainer.appendChild(row);
    
    // Animate the bar width on next frame
    setTimeout(() => {
      const valBar = row.querySelector(".chat-chart-bar-value");
      if (valBar) valBar.style.width = `${pct}%`;
    }, 100);
  });
  
  chartEl.appendChild(barsContainer);
  container.appendChild(chartEl);
  
  // Auto-scroll after chart renders
  setTimeout(() => {
    const history = $("chat-history");
    history.scrollTop = history.scrollHeight;
  }, 150);
}

// Wire up query chips
document.querySelectorAll(".chat-chip").forEach(chip => {
  chip.addEventListener("click", () => {
    const q = chip.dataset.query;
    $("chat-input").value = q;
    $("chat-form").dispatchEvent(new Event("submit"));
  });
});

function escapeHtml(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// ── Procure-to-Pay Lifecycle Matching Clusters ─────────────────────────────
function renderClusters(clusters) {
  const section = $("clusters-section");
  const list = $("clusters-list");
  if (!section || !list) return;
  if (!clusters || !clusters.length) {
    section.classList.add("hidden");
    list.innerHTML = "";
    return;
  }
  section.classList.remove("hidden");
  list.innerHTML = "";

  clusters.forEach((c) => {
    const card = document.createElement("div");
    const isVariance = c.status === "VARIANCE_FLAGGED";
    card.className = `cluster-card ${isVariance ? "variance" : "cleared"}`;

    const typeLabel = c.cluster_type === "4way_match" ? "4-Way Match (Procure-to-Pay)"
      : c.cluster_type === "3way_match" ? "3-Way Match (PO + GRN + Inv)"
      : c.cluster_type === "2way_match" ? "2-Way Match (PO + Inv)" : "Cross-Source Pair";

    const varianceText = isVariance ? `⚠ Discrepancy: ₹${c.net_variance}` : `✓ Cleared ($0.00 Variance)`;

    let legsHtml = "";
    if (c.ordered_amount != null) {
      legsHtml += `<div class="leg-box"><div class="leg-label">1. Approved PO</div><div class="leg-val">₹${c.ordered_amount}</div><div class="leg-status">Approved</div></div>`;
    }
    if (c.received_amount != null) {
      legsHtml += `<div class="leg-box"><div class="leg-label">2. Warehouse GRN</div><div class="leg-val">₹${c.received_amount}</div><div class="leg-status">${isVariance ? '2 Units Short' : 'Received In Full'}</div></div>`;
    }
    if (c.invoiced_amount != null) {
      legsHtml += `<div class="leg-box"><div class="leg-label">3. Tax Invoice</div><div class="leg-val">₹${c.invoiced_amount}</div><div class="leg-status">Billed</div></div>`;
    }
    if (c.settled_amount != null) {
      legsHtml += `<div class="leg-box"><div class="leg-label">4. Bank Feed</div><div class="leg-val">₹${c.settled_amount}</div><div class="leg-status">Settled / Cleared</div></div>`;
    }

    card.innerHTML = `
      <div class="cluster-header">
        <div class="cluster-title">
          <span>${escapeHtml(c.vendor_merchant)}</span>
          <span class="cluster-type-badge">${typeLabel}</span>
          <span class="ref-badge">#${escapeHtml(c.reference_no)}</span>
        </div>
        <div class="cluster-variance-tag">${varianceText}</div>
      </div>
      <div class="cluster-legs">${legsHtml}</div>
    `;
    list.appendChild(card);
  });
}

// ── Statutory Audit Dossier Modal (The External Auditor's Proof Pack) ───────
const dossierOverlay = $("audit-dossier-modal-overlay");
const closeAuditDossierModal = () => { if (dossierOverlay) dossierOverlay.classList.add("hidden"); };

if ($("dossier-modal-close")) $("dossier-modal-close").addEventListener("click", closeAuditDossierModal);
if ($("dossier-modal-dismiss")) $("dossier-modal-dismiss").addEventListener("click", closeAuditDossierModal);
if ($("print-dossier-btn")) $("print-dossier-btn").addEventListener("click", () => window.print());
if (dossierOverlay) dossierOverlay.addEventListener("click", (e) => { if (e.target === dossierOverlay) closeAuditDossierModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && dossierOverlay && !dossierOverlay.classList.contains("hidden")) closeAuditDossierModal(); });

if ($("dossier-btn")) $("dossier-btn").addEventListener("click", openAuditDossierModal);
if ($("dossier-nav-btn")) $("dossier-nav-btn").addEventListener("click", openAuditDossierModal);

async function openAuditDossierModal() {
  let dossier = activeDossier;
  if (!dossier && currentRunId) {
    try {
      const res = await fetch(`${API}/runs/${currentRunId}/audit-dossier`);
      if (res.ok) dossier = await res.json();
    } catch (e) {
      console.warn("Failed fetching audit dossier", e);
    }
  }

  const bodyEl = $("audit-dossier-body");
  if (!bodyEl) return;

  if (!dossier || !dossier.certificate_id) {
    bodyEl.innerHTML = `<div style="padding: 24px; color: var(--muted); text-align: center;">No active reconciliation run to certify. Please run a reconciliation first.</div>`;
    dossierOverlay.classList.remove("hidden");
    return;
  }

  const s = dossier.summary_schedule || {};
  const journals = dossier.adjusting_journals || [];
  const decls = dossier.statutory_declarations || [];

  let journalRowsHtml = journals.map(j => `
    <tr>
      <td><b>${escapeHtml(j.reference_no)}</b></td>
      <td>${escapeHtml(j.account_code)} · ${escapeHtml(j.account_name)}</td>
      <td class="${j.entry_type === 'DEBIT' ? 'entry-debit' : 'entry-credit'}">${escapeHtml(j.entry_type)}</td>
      <td>₹${escapeHtml(j.amount)}</td>
      <td><small>${escapeHtml(j.narration)}</small></td>
    </tr>
  `).join("");

  if (!journals.length) {
    journalRowsHtml = `<tr><td colspan="5" style="text-align: center; color: var(--muted); padding: 12px;">No adjusting journal entries required. All posted transactions matched within tolerance.</td></tr>`;
  }

  let declsHtml = decls.map(d => `<div class="cert-decl-item">✓ ${escapeHtml(d)}</div>`).join("");

  bodyEl.innerHTML = `
    <div class="cert-meta-grid">
      <div class="cert-meta-item">
        <span class="cert-meta-label">Certificate ID</span>
        <span class="cert-meta-val">${escapeHtml(dossier.certificate_id)}</span>
      </div>
      <div class="cert-meta-item">
        <span class="cert-meta-label">Reporting Entity</span>
        <span class="cert-meta-val">${escapeHtml(dossier.entity_name)}</span>
      </div>
      <div class="cert-meta-item">
        <span class="cert-meta-label">Statutory Period</span>
        <span class="cert-meta-val">${escapeHtml(dossier.audit_period)}</span>
      </div>
      <div class="cert-meta-item">
        <span class="cert-meta-label">Compliance Standard</span>
        <span class="cert-meta-val" style="color: var(--accent);">${escapeHtml(dossier.compliance_framework)}</span>
      </div>
    </div>

    <div>
      <div class="cert-section-title">📊 Schedule I: Reconciliation & Clearance Balances</div>
      <table class="cert-table">
        <thead>
          <tr>
            <th>Reconciliation Category</th>
            <th>Document Count</th>
            <th>Aggregate Value (INR)</th>
            <th>General Ledger Status</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Total Inward Submissions (Gross)</td>
            <td>${(s.posted_count || 0) + (s.quarantined_count || 0)} records</td>
            <td>₹${s.total_ingested_amount || '0.00'}</td>
            <td>Pre-Audit Ingested</td>
          </tr>
          <tr>
            <td>Cleared & Posted to General Ledger</td>
            <td>${s.posted_count || 0} records</td>
            <td style="color: var(--green); font-weight: 700;">₹${s.cleared_posted_amount || '0.00'}</td>
            <td>POSTED (Cleared)</td>
          </tr>
          <tr>
            <td>Quarantined Exceptions (Pending HITL Review)</td>
            <td>${s.quarantined_count || 0} exceptions</td>
            <td style="color: var(--amber); font-weight: 700;">₹${s.quarantined_exceptions_amount || '0.00'}</td>
            <td>GATED (Quarantine Lane)</td>
          </tr>
          <tr>
            <td>Net Variance Isolated Across P2P Cycles</td>
            <td>${s.cluster_count || 0} match clusters</td>
            <td style="color: var(--accent); font-weight: 700;">₹${s.net_isolated_variance || '0.00'}</td>
            <td>Adjusted via Debit Note / Surcharges</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div>
      <div class="cert-section-title">📑 Schedule II: Proposed Double-Entry Balancing Adjustments</div>
      <table class="cert-table cert-journal-table">
        <thead>
          <tr>
            <th>Lifecycle Ref</th>
            <th>Account Code & Title</th>
            <th>Type</th>
            <th>Amount</th>
            <th>Statutory Narration</th>
          </tr>
        </thead>
        <tbody>
          ${journalRowsHtml}
        </tbody>
      </table>
    </div>

    <div>
      <div class="cert-section-title">⚖️ Schedule III: Internal Financial Controls & Statutory Declarations</div>
      <div class="cert-declarations">
        ${declsHtml}
      </div>
    </div>

    <div class="cert-signoff-grid">
      <div>
        <span class="cert-meta-label">Audit Certification Verdict</span>
        <div style="font-size: 15px; font-weight: 700; color: ${(s.quarantined_count || 0) > 0 ? 'var(--amber)' : 'var(--green)'}; margin-top: 4px;">
          ${escapeHtml(dossier.auditor_sign_off?.status || 'CERTIFIED')}
        </div>
        <div style="font-size: 11px; color: var(--muted); margin-top: 4px;">
          Certified under SOX 404 & Indian GST Act Rule 36(4).
        </div>
      </div>
      <div>
        <span class="cert-meta-label">Cryptographic Invariance Hash</span>
        <div class="cert-seal-hash">${escapeHtml(dossier.audit_seal_hash)}</div>
        <div style="font-size: 10px; color: var(--faint); margin-top: 4px;">
          SHA-256 Merkle root verifiable by external auditors.
        </div>
      </div>
    </div>
  `;

  dossierOverlay.classList.remove("hidden");
}
