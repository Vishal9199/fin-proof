# ──────────────────────────────────────────────────────────────────────────
# FinProof — single-service image for Hugging Face Spaces (Docker SDK).
#
# One container serves BOTH the FastAPI API and the vanilla-JS dashboard from
# the same origin (see the StaticFiles mount in app/main.py). That's all a free
# Space exposes: one HTTPS port. The same image runs on Render / Cloud Run too,
# because we bind ${PORT:-7860} — HF routes to 7860 (declared as `app_port` in
# README.md); platforms that inject $PORT are honored automatically.
#
# Verified on python:3.13-slim — the interpreter the 46 tests + eval gates pass
# on (pandas 3.x / pandera / langgraph 1.x all green).
# ──────────────────────────────────────────────────────────────────────────
FROM python:3.13-slim

# HF Spaces runs containers as a non-root user (uid 1000). Create it up front so
# file ownership and $HOME are sane; nothing is written to the app dir at runtime
# (runs live in memory), uploads spool to the world-writable /tmp.
RUN useradd --create-home --uid 1000 user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LEDGER_FRONTEND_DIR=/app/frontend \
    FIN_PROOF_SAMPLE_DATA_DIR=/app/sample_data

WORKDIR /app

# Install deps first for layer caching. Only the providers you configure at
# runtime are imported (lazily); installing all three keeps the image universal.
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code + the static dashboard it serves.
COPY backend/app ./app
COPY frontend ./frontend
COPY sample_data ./sample_data

USER user
EXPOSE 7860

# Shell form so ${PORT:-7860} is expanded. Boots straight to deterministic mock
# mode — no API key required for the live demo.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}
