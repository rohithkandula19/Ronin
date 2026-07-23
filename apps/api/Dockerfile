FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.5.0
WORKDIR /app

# Copy the whole workspace (build context = repo root) so uv can resolve the
# ronin-api member and its workspace-path dependencies.
COPY . .

# Sync ONLY the ronin-api package + its deps (fastapi, uvicorn, sqlalchemy,
# cryptography, and the ronin-* workspace packages) into /app/.venv.
# NOTE: `uv sync` at the workspace ROOT installs nothing — the root project has
# no dependencies of its own — so the package must be named explicitly, or the
# image ships without uvicorn and boots with "uvicorn: not found".
RUN uv sync --frozen --no-dev --package ronin-api

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["sh", "-c", "uvicorn csk_api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
