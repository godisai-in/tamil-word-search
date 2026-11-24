import threading

from django.conf import settings

from .engine import Engine
from .osvectors import OpenSearchVectors

_lock = threading.Lock()
_engine = None


def get():
    global _engine
    with _lock:
        if _engine is None:
            vectors = None
            if settings.VECTOR_BACKEND == "opensearch":
                vectors = lambda conn: OpenSearchVectors(      # noqa: E731
                    conn, settings.OPENSEARCH["url"], settings.OPENSEARCH["index"], settings.OPENSEARCH["user"],
                    settings.OPENSEARCH["password"], settings.OPENSEARCH["verify_certs"])
            _engine = Engine(settings.DATA_DIR, vectors)
        return _engine
