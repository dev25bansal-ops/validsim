# syntax=docker/dockerfile:1
# =============================================================================
# ValidSim — production image (multi-stage)
# Flat-layout Python project: package `validsim/` at repo root.
# FastAPI app importable as `validsim.api.main:app`; Typer CLI in validsim/cli.py
#
# Stage 1 (builder): install deps into an isolated venv at /opt/venv.
# Stage 2 (runtime): slim base + copied venv, non-root user, healthcheck.
# =============================================================================

# --- Stage 1: builder ----------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Create the venv first; everything installed below lands inside it.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Dependencies first for optimal layer caching: requirements.txt changes far
# less often than source code, so this layer is reused on most rebuilds.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# --- Stage 2: runtime ------------------------------------------------------------
FROM python:3.12-slim

# --- Image metadata -----------------------------------------------------------
LABEL org.opencontainers.image.title="ValidSim" \
      org.opencontainers.image.description="CI/CD-style continuous validation platform for robot foundation models" \
      org.opencontainers.image.version="0.2.0" \
      org.opencontainers.image.maintainer="ValidSim Team <team@validsim.dev>" \
      org.opencontainers.image.licenses="Proprietary"

# --- Runtime hardening / ergonomics -------------------------------------------
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Reuse the dependency layer built in the builder stage; no pip run needed here.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create the dedicated unprivileged user BEFORE copying source, so
# COPY --chown stamps ownership directly (no extra chown -R layer).
RUN groupadd --gid 1001 validsim \
    && useradd --uid 1001 --gid validsim --home-dir /app --no-create-home \
       --shell /usr/sbin/nologin validsim

WORKDIR /app

# --- Application source ---------------------------------------------------------
COPY --chown=validsim:validsim validsim/ ./validsim/
USER validsim

EXPOSE 8000

# --- Healthcheck ------------------------------------------------------------------
# Uses stdlib urllib so no curl/wget needs to be installed in the image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"]

# --- Entrypoint --------------------------------------------------------------------
CMD ["uvicorn", "validsim.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
