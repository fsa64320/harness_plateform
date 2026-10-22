FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /usr/local/bin/uv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
RUN useradd --system --uid 10001 --create-home harness
WORKDIR /app
COPY --from=build --chown=harness:harness /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER harness
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn harness.main:create_app --factory --host 0.0.0.0 --port 8000"]
