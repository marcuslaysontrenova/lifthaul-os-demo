# LiftHaul OS backend — production image (PostgreSQL-backed).
FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/

ENV APP_ENV=production \
    PORT=8787 \
    PYTHONUNBUFFERED=1
WORKDIR /app/backend

EXPOSE 8787
# Apply schema/migrations against DATABASE_URL, then start the server (fail-closed on
# missing APP_SECRET / DATABASE_URL / CORS_ORIGINS via server.validate_config()).
CMD ["sh", "-c", "python migrate.py && python server.py"]

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:${PORT}/health || exit 1
