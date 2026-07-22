# Deploying FinProof (free, live demo)

FinProof boots to **deterministic mock mode** — no API key, no secrets,
no billing. That makes it ideal for a free public demo: the golden path
(3 posted / 3 quarantined / ₹1,720) runs out of the box, and a viewer can
optionally plug in their own provider key from the dashboard.

The whole app ships as **one image, one origin**: FastAPI serves the API *and*
the vanilla-JS dashboard from the same URL (see the `StaticFiles` mount at the
bottom of [`backend/app/main.py`](../backend/app/main.py)). Free hosts only expose
a single HTTPS port, and single-origin means no CORS and one link to share.

---

## Recommended: Render — free, no credit card

Result: a public URL like `https://fin-proof.onrender.com`.

### 1. Create a Web Service on Render

1. Sign in at <https://render.com> (free; no card required).
2. **New → Web Service**.
3. Connect your GitHub repo (`Vishal9199/fin-proof`).
4. Set **Runtime** to **Docker** and **Dockerfile path** to `./Dockerfile`.
5. Set **Instance type** to **Free**.
6. Click **Create Web Service**.

Render will build the image and give you a `*.onrender.com` URL. It
auto-deploys on every push to `main`.

### 2. (Optional) Gate the live model-config panel

The dashboard lets a visitor paste their own provider key and pick a model. To
require an admin token before any config write, add an environment variable:

- Render → **Environment → Add Environment Variable**
- Name `LEDGER_ADMIN_TOKEN`, value = any strong string.

Leave it unset for a fully open demo (mock mode never needs a key, and the
backend writes keys but never reads them back — `GET /config` only ever returns
`keys_configured`, never the secret).

### Demo-day note — cold start

The free Render tier **sleeps after 15 min idle** and the first request then
takes ~30–60 s to wake. Before you present, open the URL (or hit `/health`)
a minute ahead so it's warm. To avoid sleep during a session, point a free
uptime monitor (e.g. UptimeRobot) at `/health` every 5–10 min.

---

## Alternatives (also free, same image)

The root `Dockerfile` binds `${PORT:-7860}`, so the *same* image runs unchanged
on platforms that inject `$PORT`:

- **Google Cloud Run** (generous free tier, scales to zero; requires a card):
  `gcloud run deploy fin-proof --source . --allow-unauthenticated`.

---

## Run the single-service image locally

```bash
# Option A — straight Python (serves API + dashboard on one port)
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
#   → open http://localhost:8000/        (landing)
#   → open http://localhost:8000/app.html (dashboard)

# Option B — the production image, exactly as the Space runs it
docker build -t fin-proof .
docker run --rm -p 7860:7860 fin-proof
#   → open http://localhost:7860/
```

No `API_BASE`, no CORS config, no env required: the dashboard talks to its own
origin. The only secret you might ever set is `LEDGER_ADMIN_TOKEN`; provider API
keys are entered at runtime from the dashboard and are never committed.
