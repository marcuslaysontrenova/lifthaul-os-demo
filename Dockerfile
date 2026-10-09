# LiftHaul OS backend — production image (PostgreSQL-backed).
FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
# The API seeds and validates vehicle categories from the same canonical catalogue
# used by registration, booking, pricing, and the public UI.
COPY vehicle-catalogue.json ./vehicle-catalogue.json
# Package the exact tested public interface in the same immutable release image as
# the API. This prevents Railway/custom domains from serving an unrelated stale UI.
COPY *.html *.css *.js vehicle-catalogue.json ./public/
COPY assets/ ./public/assets/

ENV APP_ENV=production \
    PORT=8787 \
    PUBLIC_ROOT=/app/public \
    PYTHONUNBUFFERED=1
WORKDIR /app/backend

EXPOSE 8787
# Apply schema/migrations against DATABASE_URL, then start the server (fail-closed on
# missing APP_SECRET / DATABASE_URL / CORS_ORIGINS via server.validate_config()).
CMD ["sh", "-c", "python migrate.py && python server.py"]

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:${PORT}/readyz || exit 1
