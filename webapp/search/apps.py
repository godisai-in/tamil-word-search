import os
import sys
import threading

from django.apps import AppConfig


class SearchConfig(AppConfig):
    name = "search"

    def ready(self):
        # `runserver` without --noreload starts a reloader parent and a serving child;
        # only the serving process should hold the ~2 GB of vectors.
        if "runserver" in sys.argv and "--noreload" not in sys.argv and os.environ.get("RUN_MAIN") != "true":
            return
        if any(c in sys.argv for c in ("check", "collectstatic", "migrate", "test")):
            return
        from django.conf import settings

        from . import engine_singleton

        eng = engine_singleton.get()
        for lang in settings.VECTOR_WARM:
            if eng.vectors.backend == "numpy" and eng.vectors.available:
                threading.Thread(target=eng.vectors.load, args=(lang,), daemon=True).start()
