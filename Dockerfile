# syntax=docker/dockerfile:1
# One image, two jobs: the web app (default CMD) and the one-shot OpenSearch loader (see docker-compose.yml).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# code only: pipeline/ gives the shared text normaliser (textnorm.py) and upload_to_opensearch.py
COPY pipeline/ /app/pipeline/
COPY opensearch/ /app/opensearch/
COPY webapp/ /app/webapp/

# hashed, pre-compressed static files for WhiteNoise (no data needed at build time)
RUN cd webapp && DJANGO_DEBUG=0 DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER app

ENV DJANGO_DEBUG=0 \
    WORDSEARCH_DATA=/data \
    WORDSEARCH_VECTOR_BACKEND=opensearch \
    WORDSEARCH_WARM=

WORKDIR /app/webapp
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:8000/healthz', headers={'Host': 'localhost'}), timeout=4)"

# No --preload: each worker opens its own SQLite connections (they must not be inherited across fork).
# Workers x threads = concurrent searches. In OpenSearch mode a worker is ~300 MB; in numpy mode it holds
# ~2.2 GB of vectors, so use GUNICORN_WORKERS=1 there.
CMD ["sh", "-c", "exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-2} --threads ${GUNICORN_THREADS:-4} --timeout 30 --graceful-timeout 20 --max-requests 5000 --max-requests-jitter 500 --access-logfile -"]
