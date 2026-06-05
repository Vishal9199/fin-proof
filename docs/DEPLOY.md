# Deploying Ledger Sentinel (free, live demo)

Ledger Sentinel boots to **deterministic mock mode** — no API key, no secrets,
no billing. That makes it ideal for a free public demo: the golden path
(3 posted / 3 quarantined / ₹1,720) runs out of the box, and a viewer can
optionally plug in their own provider key from the dashboard.

The whole app ships as **one image, one origin**: FastAPI serves the API *and*
the vanilla-JS dashboard from the same URL (see the `StaticFiles` mount at the
bottom of [`backend/app/main.py`](backend/app/main.py)). Free hosts only expose
a single HTTPS port, and single-origin means no CORS and one link to share.

---

## Recommended: Hugging Face Spaces (Docker) — free, no credit card

Result: a public URL like `https://<your-hf-username>-ledger-sentinel.hf.space`.

### 1. Create the Space
1. Sign in at <https://huggingface.co> (free; no card).
2. **New → Space**.
3. **Owner**: you · **Space name**: `ledger-sentinel`.
4. **License**: MIT · **SDK**: **Docker** → *Blank* template.
5. **Hardware**: *CPU basic* (free) · **Visibility**: *Public*.
6. **Create Space**. You now have an empty Space git repo.

The Space reads its config from the YAML front-matter at the top of
[`README.md`](README.md) (`sdk: docker`, `app_port: 7860`) and builds the
root [`Dockerfile`](Dockerfile) — both are already in this repo, so there is
nothing to edit.

### 2. Push this repo to the Space
The Space is its own git repo. Add it as a second remote and push `main`.
Authenticate with a **write** access token from
<https://huggingface.co/settings/tokens> (use the token as the git password, or
run `pip install huggingface_hub && hf auth login` once).

```bash
# from the repo root (the folder containing Dockerfile + README.md)
git remote add hf https://huggingface.co/spaces/<your-hf-username>/ledger-sentinel
git push hf main
```

That's it. The Space starts building immediately (watch the **Logs** tab). First
build takes a few minutes (installs the pinned deps); afterwards it's live at the
`*.hf.space` URL shown on the Space page. `.venv` is git-ignored, so nothing
heavy is pushed.

### 3. (Optional) Gate the live model-config panel
The dashboard lets a visitor paste their own provider key and pick a model. To
require an admin token before any config write, add a Space **secret**:

- Space → **Settings → Variables and secrets → New secret**
- Name `LEDGER_ADMIN_TOKEN`, value = any strong string.

Leave it unset for a fully open demo (mock mode never needs a key, and the
backend writes keys but never reads them back — `GET /config` only ever returns
`keys_configured`, never the secret).

### Demo-day note — cold start
A free Space **sleeps after idle** and the first request then takes ~30–60 s to
wake. Before you present, open the URL once (or hit `…hf.space/health`) a minute
ahead so it's warm. To avoid sleep entirely during a session, point a free
uptime monitor (e.g. UptimeRobot) at `…/health` every 5–10 min.

---

## Alternatives (also free, same image)

The root `Dockerfile` binds `${PORT:-7860}`, so the *same* image runs unchanged
on platforms that inject `$PORT`:

- **Render** (no credit card; spins down after 15 min idle, ~30–60 s cold
  start): New → Web Service → connect the GitHub repo → Runtime **Docker** →
  Dockerfile path `./Dockerfile` → Instance type **Free**. Public
  `*.onrender.com` URL, auto-deploys on every push to `main`.
- **Google Cloud Run** (generous free tier, scales to zero; requires a card):
  `gcloud run deploy ledger-sentinel --source . --allow-unauthenticated`.

---

## Run the single-service image locally

```bash
# Option A — straight Python (serves API + dashboard on one port)
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
#   → open http://localhost:8000/        (landing)
#   → open http://localhost:8000/app.html (dashboard)

# Option B — the production image, exactly as the Space runs it
docker build -t ledger-sentinel .
docker run --rm -p 7860:7860 ledger-sentinel
#   → open http://localhost:7860/
```

No `API_BASE`, no CORS config, no env required: the dashboard talks to its own
origin. The only secret you might ever set is `LEDGER_ADMIN_TOKEN`; provider API
keys are entered at runtime from the dashboard and are never committed.
