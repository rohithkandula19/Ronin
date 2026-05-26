# Multi-stage Python build for an Ronin agent.
# Builder stage: install deps with uv (much faster than pip).
# Runtime stage: copy only the resolved venv + source. Smaller image, no toolchain.

FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.5.0

WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev


FROM python:3.12-slim AS runtime

RUN groupadd -r app && useradd -r -g app app

WORKDIR /app
COPY --from=builder --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app
EXPOSE 8000

# Override with your own entrypoint. Default expects app.main:app (FastAPI).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
