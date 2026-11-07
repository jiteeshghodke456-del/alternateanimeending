# syntax=docker/dockerfile:1
# Brand Gate: one container, one URL. Hugging Face Spaces (Docker SDK) listens on 7860.

# ---------- stage 1: build the frontend (skipped gracefully if there's no package.json)
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/ ./
RUN if [ -f package.json ]; then \
      (npm ci --no-audit --no-fund || npm install --no-audit --no-fund) && npm run build; \
    else \
      echo "no frontend/package.json; skipping build"; \
    fi && mkdir -p dist

# ---------- stage 2: Python runtime
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# HF Spaces runs the container as uid 1000
RUN useradd -m -u 1000 user
WORKDIR /home/user/app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY --chown=user:user backend/ backend/
COPY --from=web --chown=user:user /web/dist frontend/dist

ENV DB_PATH=/home/user/data/brandgate.db \
    UPLOAD_DIR=/home/user/data/uploads \
    FRONTEND_DIST=/home/user/app/frontend/dist \
    DEMO_MODE=1 \
    PORT=7860
RUN mkdir -p /home/user/data && chown -R user:user /home/user/data

USER user
WORKDIR /home/user/app/backend
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7860/api/health', timeout=4).status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860", "--proxy-headers", "--forwarded-allow-ips", "*"]
