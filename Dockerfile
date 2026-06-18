# ScoutReel — web image for Render (or any Docker host).
# Web app only: no background jobs, no Gmail, no API keys required. Ships with
# the sanitized public library at data/library.db (see scripts/make_demo_db.py).
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# install deps first so this layer caches across code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app code + bundled public library (.dockerignore excludes the private DB,
# secrets, venv, tests — see that file)
COPY . .

# Render injects $PORT; default 8000 for a local `docker run`.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
