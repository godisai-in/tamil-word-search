"""In-memory ring buffer of recent request timings (per process; resets on restart)."""
import threading
from collections import deque

import numpy as np

_buf = deque(maxlen=1000)
_lock = threading.Lock()


def record(path, total_ms, stages):
    with _lock:
        _buf.append({"path": path, "total": total_ms, **stages})


def summary():
    with _lock:
        rows = list(_buf)
    if not rows:
        return []
    keys = ["total"] + sorted({k for r in rows for k in r if k not in ("path", "total")})
    out = []
    for k in keys:
        v = np.array([r[k] for r in rows if k in r])
        out.append({"stage": k, "n": len(v), "p50": float(np.percentile(v, 50)), "p95": float(np.percentile(v, 95)),
                    "max": float(v.max()), "mean": float(v.mean())})
    return out
