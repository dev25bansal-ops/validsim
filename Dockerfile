# syntax=docker/dockerfile:1
# =============================================================================
# ValidSim — production image
# Flat-layout Python project: package `validsim/` at repo root.
# FastAPI app importable as `validsim.api.main:app`; Typer CLI in validsim/cli.py
# =============================================================================

FROM python:3.12-slim

# --- Image metadata -----------------------------------------------------------
LABEL org.opencontainers.image.title="ValidSim" \
      org.opencontainers.image.description="CI/CD-style continuous validation platform for robot foundation models" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.maintainer="ValidSim Team <team@validsim.dev>" \
      org.opencontainers.image.licenses="Proprietary"

# --- Runtime hardening / ergonomics -------------------------------------------
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# --- Dependencies first for optimal layer caching ------------------------------
# requirements.txt changes far less often than source code, so this layer
# is reused on most rebuilds.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# --- Application source ---------------------------------------------------------
COPY validsim/ ./validsim/

# --- Non-root user ---------------------------------------------------------------
# Create a dedicated unprivileged user AFTER the copy so a single chown
# covers the whole app tree (site-packages in /usr/local stays world-readable).
RUN groupadd --gid 1001 validsim \
    && useradd --uid 1001 --gid validsim --home-dir /app --no-create-home \
       --shell /usr/sbin/nologin validsim \
    && chown -R validsim:validsim /app
USER validsim

EXPOSE 8000

# --- Healthcheck ------------------------------------------------------------------
# Uses stdlib urllib so no curl/wget needs to be installed in the image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"]

# --- Entrypoint --------------------------------------------------------------------
CMD ["uvicorn", "validsim.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
