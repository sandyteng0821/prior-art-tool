FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY api/ api/
COPY run_api.py .

# Default env vars (overridden by .env / docker-compose)
ENV API_HOST=0.0.0.0
ENV API_PORT=8007
ENV DATABASE_PATH=cache/patents.db

EXPOSE 8007

# Ensure default DB directory exists inside container
# (bind mount overlays this; without mount, health check returns null gracefully)
RUN mkdir -p /app/cache

CMD ["python", "run_api.py"]
