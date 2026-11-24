"""Search-only Django project: no database, no users, no sessions, no forms.
All data comes from out/words.db and out/vec_*.npy via search.engine."""
import os
import secrets
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
REPO = BASE_DIR.parent
sys.path.insert(0, str(REPO / "pipeline"))          # shared text normaliser (textnorm.py)

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or secrets.token_urlsafe(50)   # nothing signed, so a per-process key is fine
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",") if h.strip()]
# Behind Caddy (TLS terminates there): trust its X-Forwarded-Proto, and allow the public origin for CSRF-checked requests.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

# Vector search: "numpy" = in-process brute force from out/vec_*.npy (no services), "opensearch" = kNN index.
VECTOR_BACKEND = os.environ.get("WORDSEARCH_VECTOR_BACKEND", "numpy")
OPENSEARCH = {
    "url": os.environ.get("OPENSEARCH_URL", "https://localhost:9200"),
    "index": os.environ.get("OPENSEARCH_INDEX", "words"),          # an alias is fine: see upload --alias
    "user": os.environ.get("OPENSEARCH_USER") or None,
    "password": os.environ.get("OPENSEARCH_PASSWORD") or None,
    "verify_certs": os.environ.get("OPENSEARCH_VERIFY_CERTS", "1") == "1",
}

DATA_DIR = Path(os.environ.get("WORDSEARCH_DATA", REPO / "out"))
# Load these languages' vectors into RAM at start-up (~1.1 GB each). Empty = load on first use.
VECTOR_WARM = [x for x in os.environ.get("WORDSEARCH_WARM", "en,ta").split(",") if x]

INSTALLED_APPS = ["django.contrib.staticfiles", "django.contrib.humanize", "search"]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "search.middleware.SecurityHeaders",
]
try:                                    # production: serve /static/ from the app itself (no extra web server)
    import whitenoise  # noqa: F401

    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
    if not DEBUG:
        STORAGES = {
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
        }
except ImportError:
    pass
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
}]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {}
LANGUAGE_CODE = "en"
USE_TZ = True
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO")},
    "loggers": {"opensearch": {"level": "CRITICAL"}, "urllib3": {"level": "ERROR"}},   # our own log line says it once
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SILENCED_SYSTEM_CHECKS = ["security.W004", "security.W008", "security.W012", "security.W016", "security.W018"]
