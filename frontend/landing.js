// FinProof — landing page animation engine. Vanilla, no dependencies.
// Everything degrades gracefully under prefers-reduced-motion.

const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

// Fire a callback once, the first time an element scrolls into view.
function onceInView(el, cb, threshold = 0.25) {
  if (!("IntersectionObserver" in window)) return cb();
  const io = new IntersectionObserver(
    (entries) => entries.forEach((e) => { if (e.isIntersecting) { cb(); io.disconnect(); } }),
    { threshold }
  );
  io.observe(el);
}

// ── Nav: solidify on scroll ───────────────────────────────────────────
const nav = $("#nav");
const onScroll = () => nav.classList.toggle("scrolled", window.scrollY > 20);
onScroll();
window.addEventListener("scroll", onScroll, { passive: true });

// ── Scroll reveal ─────────────────────────────────────────────────────
(() => {
  const els = $$(".reveal");
  if (reduced || !("IntersectionObserver" in window)) {
    els.forEach((e) => e.classList.add("in-view"));
    return;
  }
  const io = new IntersectionObserver(
    (entries) => entries.forEach((e) => {
      if (e.isIntersecting) { e.target.classList.add("in-view"); io.unobserve(e.target); }
    }),
    { threshold: 0.16 }
  );
  els.forEach((e) => io.observe(e));
})();

// ── Typewriter code blocks ───────────────────────────────────────────
function setupType(pre) {
  const dataEl = $(`script.code-data[data-name="${pre.dataset.typed}"]`);
  if (!dataEl) return;
  let segments;
  try { segments = JSON.parse(dataEl.textContent); } catch { return; }

  const makeSpans = () => segments.map((s) => {
    const el = document.createElement("span");
    el.className = "tok-" + (s.c || "dim");
    pre.appendChild(el);
    return el;
  });

  if (reduced) {
    pre.innerHTML = "";
    const spans = makeSpans();
    segments.forEach((s, i) => (spans[i].textContent = s.t));
    return;
  }

  // Time-based typing so the pace is readable regardless of frame rate.
  const cps = 20 * parseFloat(pre.dataset.speed || "3"); // characters per second
  onceInView(pre, () => {
    pre.innerHTML = "";
    const spans = makeSpans();
    const caret = document.createElement("span");
    caret.className = "caret";
    pre.appendChild(caret);
    const chars = [];
    segments.forEach((s, i) => { for (const ch of s.t) chars.push([i, ch]); });
    let idx = 0, last = performance.now(), acc = 0;
    (function frame(now) {
      acc += ((now - last) / 1000) * cps;
      last = now;
      while (acc >= 1 && idx < chars.length) {
        spans[chars[idx][0]].textContent += chars[idx][1];
        idx++; acc -= 1;
      }
      if (idx < chars.length) requestAnimationFrame(frame);
      else caret.remove();
    })(last);
  }, 0.4);
}
$$(".type-target").forEach(setupType);

// ── Count-up metric tiles ───────────────────────────────────────────
function setupCount(el) {
  const target = parseFloat(el.dataset.count);
  const decimals = parseInt(el.dataset.decimals || "0", 10);
  const prefix = el.dataset.prefix || "";
  const suffix = el.dataset.suffix || "";
  const fmt = (v) =>
    prefix + v.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) + suffix;

  if (reduced) { el.textContent = fmt(target); return; }
  onceInView(el, () => {
    const dur = 1100, start = performance.now();
    (function frame(t) {
      const p = Math.min(1, (t - start) / dur);
      el.textContent = fmt(target * (1 - Math.pow(1 - p, 3)));
      if (p < 1) requestAnimationFrame(frame);
      else el.textContent = fmt(target);
    })(start);
  }, 0.5);
}
$$(".msv[data-count]").forEach(setupCount);

// ── Track toggle (Engineer ↔ Tech Lead) ────────────────────────────────
(() => {
  const toggle = $(".toggle");
  if (!toggle) return;
  const pill = $(".toggle-pill", toggle);
  const btns = $$(".toggle-btn", toggle);

  const movePill = (btn) => {
    if (!btn) return;
    pill.style.width = btn.offsetWidth + "px";
    pill.style.transform = `translateX(${btn.offsetLeft - 5}px)`;
  };
  const activate = (name) => {
    btns.forEach((b) => b.classList.toggle("active", b.dataset.toggle === name));
    $$("[data-track]").forEach((t) => t.classList.toggle("hidden", t.dataset.track !== name));
    movePill(btns.find((b) => b.dataset.toggle === name));
    // The newly-shown track's reveal elements should appear immediately.
    $$(`[data-track="${name}"] .reveal`).forEach((r) => r.classList.add("in-view"));
  };

  btns.forEach((b) => b.addEventListener("click", () => activate(b.dataset.toggle)));
  const init = () => movePill(btns.find((b) => b.classList.contains("active")));
  // Fonts can shift button widths; settle the pill once they're ready.
  init();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(init);
  window.addEventListener("resize", init);
})();

// ── Matrix code rain (hero) ──────────────────────────────────────────
(() => {
  const canvas = $("#rain");
  if (!canvas || reduced) return; // reduced motion → static gradient backdrop only
  const ctx = canvas.getContext("2d");
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const fontSize = 15;
  const glyphs = "01₹$ EXTRACT VERIFY RECONCILE POSTED 0123456789".split("");
  let cols = 0, drops = [];

  function resize() {
    const w = canvas.offsetWidth, h = canvas.offsetHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    cols = Math.floor(w / fontSize);
    drops = Array.from({ length: cols }, () => Math.random() * -50);
  }
  resize();
  window.addEventListener("resize", resize);

  function draw() {
    const w = canvas.offsetWidth, h = canvas.offsetHeight;
    ctx.fillStyle = "rgba(8,9,12,0.09)";
    ctx.fillRect(0, 0, w, h);
    ctx.font = `${fontSize}px "JetBrains Mono", monospace`;
    for (let i = 0; i < cols; i++) {
      const ch = glyphs[(Math.random() * glyphs.length) | 0];
      const x = i * fontSize, y = drops[i] * fontSize;
      ctx.fillStyle = Math.random() > 0.985 ? "rgba(120,240,160,.95)" : "rgba(74,222,128,.32)";
      ctx.fillText(ch, x, y);
      if (y > h && Math.random() > 0.975) drops[i] = Math.random() * -20;
      drops[i]++;
    }
  }

  let active = false, lastDraw = 0;
  const STEP_MS = 75; // throttle redraws → calmer, slower rain
  const tick = (t) => {
    if (!active) return;
    if (!t || t - lastDraw >= STEP_MS) { draw(); lastDraw = t || 0; }
    requestAnimationFrame(tick);
  };
  const setActive = (on) => { if (on && !active) { active = true; requestAnimationFrame(tick); } else if (!on) active = false; };
  setActive(true);

  const hero = $("#top");
  if (hero && "IntersectionObserver" in window) {
    new IntersectionObserver((es) => es.forEach((e) => setActive(e.isIntersecting))).observe(hero);
  }
})();
