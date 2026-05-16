# syntax=docker/dockerfile:1
FROM python:3.13-slim

# uv for fast, reproducible installs from the committed lockfile.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    SPOTIFY_CACHE_PATH=/data/.cache

WORKDIR /app

# Install dependencies first (cached unless the lockfile changes).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# App code.
COPY main.py queue_logic.py timer_logic.py spotify_ids.py ./
COPY assets ./assets

# Token cache lives on a volume so a redeploy keeps the operator
# signed in; run as non-root.
RUN useradd --create-home app \
    && mkdir -p /data \
    && chown -R app:app /app /data
USER app
VOLUME ["/data"]

EXPOSE 8050

# Single worker (the app keeps a shared, file-backed token cache and
# is light); threads cover the 1s interval callbacks.
CMD ["uv", "run", "--no-sync", "gunicorn", \
     "-w", "1", "--threads", "4", "-b", "0.0.0.0:8050", "main:server"]
