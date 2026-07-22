# ──────────────────────────────────────────────────────────────────────────
# FinProof — single-service image (deployed on Render).
#
# One container serves BOTH the FastAPI API and the vanilla-JS dashboard from
# the same origin (see the StaticFiles mount in app/main.py). One HTTPS port,
# one URL. The same image also runs on Cloud Run and locally via docker compose,
# because we bind ${PORT:-7860} — platforms that inject $PORT are honored
# automatically.
#
# Verified on python:3.13-slim — the interpreter the 80 tests + eval gates pass
# on (pandas 3.x / pandera / langgraph 1.x all green).
# ──────────────────────────────────────────────────────────────────────────
FROM python:3.13-slim

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
