# LiftHaul OS backend — production image (PostgreSQL-backed).
FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
# Package the same governed, versioned fare registry exercised by calculator and booking tests.
# Runtime must fail at import if this file is missing or malformed; no hidden code fallback exists.
COPY data/fare_matrix_registry.json ./data/fare_matrix_registry.json
# The API seeds and validates vehicle categories from the same canonical catalogue
# used by registration, booking, pricing, and the public UI.
COPY vehicle-catalogue.json ./vehicle-catalogue.json
# Package the exact tested public interface in the same immutable release image as
# the API. This prevents Railway/custom domains from serving an unrelated stale UI.
COPY *.html *.css *.js vehicle-catalogue.json ./public/
COPY assets/ ./public/assets/
# Booking location selectors load the governed PSGC hierarchy at runtime. Keep it
# in the same immutable image as the booking UI so production cannot publish a
# form whose region/province/city/barangay controls fail after page load.
COPY data/psgc/ ./public/data/psgc/

ENV APP_ENV=production \
    PORT=8787 \
    PUBLIC_ROOT=/app/public \
    PYTHONUNBUFFERED=1
WORKDIR /app/backend

EXPOSE 8787
# server.py opens DATABASE_URL through db.connect(), which applies and verifies the
# complete idempotent schema before binding the listener. Running migrate.py first
# repeated that full bootstrap and could consume the entire Railway health window.
# Startup still fails closed on schema or configuration errors.
CMD ["python", "server.py"]

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:${PORT}/readyz || exit 1
