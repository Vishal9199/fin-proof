// FinProof — dependency-free dashboard. No framework, no build step.
// Run executes server-side on POST /reconcile; SSE streams results live;
// polling /runs/{id} is the fallback if SSE is blocked.

const API = location.protocol === "file:" || !location.hostname ? "http://localhost:8000" : "";
const $ = (id) => document.getElementById(id);

let selectedFiles = [];
let metrics = { spans: 0, latency: 0, cost: 0, faithSum: 0, faithN: 0 };
let ledgerState = { posted: [], quarantined: [] };

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

// ── Load sample data button ───────────────────────────────────────────────
$("load-sample-btn").addEventListener("click", async () => {
  const btn = $("load-sample-btn");
  btn.disabled = true; btn.textContent = "Loading…";
  try {
    const res = await fetch(`${API}/sample-files`);
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
    setTimeout(() => { btn.textContent = "⚡ Load sample data"; btn.disabled = false; }, 2000);
  } catch (err) {
    btn.textContent = "⚡ Load sample data"; btn.disabled = false;
    showError(`Could not load sample files: ${err.message}. Drop sample_data/ folder instead.`);
  }
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
}

let currentRunId = null;

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
  on("run.completed", (p) => { settled = true; showSummary(p); es.close(); $("run-btn").disabled = false; });
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
  ledgerState = { posted: r.posted || [], quarantined: r.quarantined || [] };
  (r.links || []).forEach((l) => {
    if (l.kind === "duplicate") addCard("links-list", "duplicate", "DUPLICATE", `${(l.score * 100) | 0}%`, l.detail);
    if (l.kind === "anomaly")   addCard("links-list", "anomaly",   "ANOMALY",   `${(l.score * 100) | 0}%`, l.detail, true);
  });
  (r.posted     || []).forEach((t) => addCard("posted-list",    "posted",    t.normalized_merchant || t.merchant, `₹${t.amount}`,   "Posted to ledger", false, t));
  (r.quarantined|| []).forEach((t) => addCard("quarantine-list","quarantine",t.normalized_merchant || t.merchant, `⚠ ₹${t.amount}`, t.quarantine_reason || t.reason || "Quarantined", false, t));
  
  if (r.narrative) {
    $("ai-insights").classList.remove("hidden");
    $("ai-narrative").textContent = r.narrative;
  }
  
  showSummary({ total_posted_amount: r.total_posted_amount, posted: (r.posted||[]).length,
    quarantined: (r.quarantined||[]).length, links: (r.links||[]).length, documents: r.documents });
}

// ── Renderers ────────────────────────────────────────────────────────────
function upsertCell(doc, state, p = {}) {
  let cell = document.querySelector(`[data-doc="${cssEscape(doc)}"]`);
  if (!cell) {
    cell = document.createElement("div");
    cell.className = "agent-cell"; cell.dataset.doc = doc;
    cell.innerHTML = `<span class="dot"></span><div class="doc">${doc}</div><div class="meta"></div>`;
    $("agent-grid").appendChild(cell);
    $("agent-count").textContent = $("agent-grid").children.length;
  }
  cell.querySelector(".dot").className = "dot " + state;
  if (state === "done") {
    cell.classList.add("done");
    cell.querySelector(".meta").innerHTML =
      `<span>${p.worker || ""}</span><span>${p.latency_ms ?? 0}ms</span>` +
      `<span>${p.count ?? 1} txn</span><span>faith ${(p.faithfulness ?? 1).toFixed(2)}</span>`;
  }
}

function addCard(listId, kind, title, right, why, flash = false, txn = null) {
  const el = document.createElement("div");
  el.className = `card ${kind}${flash ? " flash" : ""}`;
  if (txn && txn.id) el.dataset.id = txn.id;
  const isQ = kind === "quarantine" && txn;
  
  const catHtml = (txn && txn.category && txn.category !== "Other") ? ` <span class="cat-badge">${txn.category}</span>` : "";
  
  el.innerHTML =
    `<div class="row"><span class="merchant">${title}${catHtml}</span><span class="amount">${right}</span></div>` +
    `<div class="why">${why}</div>` +
    (isQ ? `<div class="review-actions">
      <button class="review-btn approve" title="Approve — move to Posted ledger">✓ Approve</button>
      <button class="review-btn reject"  title="Reject — permanently dismiss">✕ Reject</button>
    </div>` : "");
  if (isQ) {
    el.querySelector(".approve").addEventListener("click", () => approveCard(el, txn));
    el.querySelector(".reject" ).addEventListener("click", () => rejectCard(el, txn));
  }
  $(listId).appendChild(el);
}

function approveCard(cardEl, txn) {
  cardEl.remove();
  ledgerState.quarantined = ledgerState.quarantined.filter((t) => t.id !== (txn?.id));
  const t = txn || {};
  ledgerState.posted.push({ ...t, _humanApproved: true });
  addCard("posted-list", "posted", t.merchant || "(approved)", `₹${t.amount || 0}`, "✓ Human-approved from quarantine");
  refreshSummaryFromState();
}

function rejectCard(cardEl, txn) {
  Object.assign(cardEl.style, { transition: "opacity .3s, max-height .3s", opacity: "0", maxHeight: "0", overflow: "hidden" });
  setTimeout(() => {
    cardEl.remove();
    ledgerState.quarantined = ledgerState.quarantined.filter((t) => t.id !== (txn?.id));
    refreshSummaryFromState();
  }, 300);
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
}

function updateSummaryContent(p) {
  $("summary-content").innerHTML =
    `<div><b class="ok">₹${p.total_posted_amount}</b><div>posted total</div></div>` +
    `<div><b>${p.posted}</b> posted · <b class="warn">${p.quarantined}</b> quarantined · <b>${p.links}</b> links</div>` +
    `<div>${p.documents} documents reconciled</div>`;
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
  ["agent-grid", "posted-list", "quarantine-list", "links-list", "trace-log", "chat-history"].forEach((id) => ($(id).innerHTML = ""));
  $("agent-count").textContent = "0";
  ["drift-banner", "summary", "export-btn", "ai-insights", "chat-panel"].forEach((id) => $(id).classList.add("hidden"));
  $("ai-narrative").textContent = "";
  ledgerState = { posted: [], quarantined: [] };
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
  } catch (err) {
    botMsg.textContent = `Could not answer: ${err.message}`;
    botMsg.style.color = "var(--red)";
  }
  history.scrollTop = history.scrollHeight;
});
