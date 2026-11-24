import os
import time

from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string

from . import engine_singleton, perfstore
from .engine import Perf

EXAMPLES = ["bank", "வங்கி", "water", "ஆறு", "teacher", "mother", "draft", "நூலகம்"]


def home(request):
    eng = engine_singleton.get()
    return render(request, "search/home.html", {"stats": eng.stats, "examples": EXAMPLES})


def results(request):
    q = request.GET.get("q", "").strip()[:100]
    if not q:
        return redirect("search:home")
    eng = engine_singleton.get()
    perf = Perf()
    ctx = eng.lookup(q, perf)
    lookup_ms = perf.total()
    ctx.update({"q": q, "perf": {"stages": {k: round(v, 1) for k, v in perf.stages.items()}, "lookup": round(lookup_ms, 1)}})
    with perf.stage("render"):
        html = render_to_string("search/results.html", ctx, request=request)
    total = perf.total()
    perfstore.record(request.path, total, perf.stages)
    resp = HttpResponse(html)
    resp["Server-Timing"] = ", ".join(f"{k};dur={v:.1f}" for k, v in perf.stages.items()) + f", total;dur={total:.1f}"
    return resp


def suggest(request):
    items = engine_singleton.get().suggest(request.GET.get("q", "")[:100])
    return JsonResponse({"items": items})


def sources(request):
    rows = engine_singleton.get().source_stats()
    groups = {}
    for r in rows:
        g = groups.setdefault(r["collection"], {"collection": r["collection"], "entries": 0, "relations": 0, "sources": 0})
        g["entries"] += r["entries"]; g["relations"] += r["relations"]; g["sources"] += 1
    top = max((r["entries"] + r["relations"] for r in rows), default=1)
    return render(request, "search/sources.html", {"sources": rows, "groups": sorted(groups.values(), key=lambda g: -g["entries"]), "top": top})


def healthz(request):
    """Liveness/readiness for Docker and Caddy. 503 only if the SQLite data is unusable; a down
    OpenSearch is reported as 'degraded' (search still works, just without similar-by-meaning)."""
    eng = engine_singleton.get()
    try:
        eng.conn().execute("SELECT 1 FROM word LIMIT 1").fetchone()
    except Exception as e:  # noqa: BLE001
        return JsonResponse({"status": "down", "error": repr(e)}, status=503)
    vec = "ok"
    if eng.vectors.backend == "opensearch" and not eng.vectors.ping():
        vec = "down"
    elif eng.vectors.backend == "numpy" and not eng.vectors.available:
        vec = "off"
    return JsonResponse({"status": "ok" if vec in ("ok", "off") else "degraded", "vectors": vec,
                         "backend": eng.vectors.backend})


def _rss_mb():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS"):
                    return round(int(line.split()[1]) / 1024)
    except OSError:
        pass
    return None


def _perf_ctx():
    eng = engine_singleton.get()
    v = eng.vectors
    info = v.info() if hasattr(v, "info") else None
    return {"summary": perfstore.summary(), "stats": eng.stats, "rss_mb": _rss_mb(), "pid": os.getpid(),
            "vectors": {"backend": v.backend, "available": v.available, "loaded": sorted(v.mat), "load_seconds": v.load_seconds,
                        "meta": getattr(v, "meta", {}).get("langs"), "opensearch": info,
                        "last_error": getattr(v, "last_error", None)}}


def perf(request):
    return render(request, "search/perf.html", _perf_ctx())


def perf_json(request):
    return JsonResponse(_perf_ctx())
