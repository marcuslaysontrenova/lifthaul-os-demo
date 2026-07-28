# RGO OS backend — portable container (Docker is the primary deploy method)
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 APP_ENV=production
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8787
# migrate then start; PORT/DATABASE_URL/APP_SECRET/CORS_ORIGINS supplied at runtime
CMD ["sh", "-c", "python migrate.py && python server.py"]
