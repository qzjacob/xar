# --- stage 1: build the React SPA (web/) ---------------------------------
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci || npm install
COPY web/ ./
RUN npm run build

# --- stage 2: python runtime --------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# Build essentials for psycopg/onnxruntime wheels are not needed (binary wheels),
# but keep a minimal toolchain + libgomp for onnxruntime (fastembed).
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash libgomp1 ca-certificates && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

# Include [market]: Yahoo/yfinance is the only no-key structured provider
# (free global prices + fundamentals, incl. CN A-shares) and powers the backtest,
# so it ships turnkey. Keyed providers (Finnhub/FMP/Polygon) need no extra deps.
# [futu]: futu-api client — inert unless XAR_ENABLE_FUTU=true + OpenD reachable
# (point FUTU_OPEND_HOST at the host, e.g. host.docker.internal).
RUN pip install --upgrade pip && pip install ".[market,futu]"

# Pre-download the default embedding model so first run is fast/offline.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')" || true

# Compiled React SPA from stage 1 (served by FastAPI at / ; legacy UI at /legacy).
COPY --from=web /web/dist /app/webdist
ENV XAR_WEB_DIST=/app/webdist

EXPOSE 8000
CMD ["xar", "serve", "--host", "0.0.0.0", "--port", "8000"]
