FROM python:3.12-slim

WORKDIR /app

# Build context = repo root (railway.toml lo configura así)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Solo los archivos del gateway
COPY gateway/ .

# Railway inyecta $PORT dinámicamente
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
