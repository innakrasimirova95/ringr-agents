FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /bin/uv

# The virtualenv lives outside /app so the bind mount in docker-compose.yaml does not hide it.
ENV PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-cache
COPY . .
WORKDIR /app/api

# Threaded workers: requests wait on the database, not on CPU. No keep-alive, so a burst
# on reused connections does not pile up on a few workers.
CMD ["gunicorn", "wsgi:application", "--bind", "0.0.0.0:8000", "--worker-class", "gthread", \
     "--workers", "4", "--threads", "8", "--keep-alive", "0", "--reload"]
