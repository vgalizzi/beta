FROM python:3.12.15-slim-trixie AS construction
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /uvx /bin/

ENV UV_LINK_MODE=copy
ENV UV_COMPILE_BYTECODE=1
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project --no-dev

COPY README.md ./
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM python:3.12.15-slim-trixie AS runtime
ENV PYTHONUNBUFFERED=1
RUN useradd --system appuser
COPY --from=construction /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
USER appuser


FROM runtime AS api
EXPOSE 8000
CMD ["uvicorn", "beta.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS worker
CMD ["celery", "-A", "beta.workers.celery_app:app", "worker", "--loglevel=INFO"]
