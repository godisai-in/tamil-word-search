"""python manage.py benchmark --n 300   -> latency percentiles per stage over real words."""
import random
import time

import numpy as np
from django.core.management.base import BaseCommand

from search import engine_singleton
from search.engine import Perf


class Command(BaseCommand):
    help = "Time the full lookup (every stage) over randomly sampled real words."

    def add_arguments(self, p):
        p.add_argument("--n", type=int, default=300, help="words per language")
        p.add_argument("--seed", type=int, default=1)

    def handle(self, *a, n, seed, **kw):
        eng = engine_singleton.get()
        c = eng.conn()
        rnd = random.Random(seed)
        for lang in ("en", "ta"):
            if eng.vectors.available:
                eng.vectors.load(lang)
        self.stdout.write(f"vectors loaded: {eng.vectors.load_seconds}")
        for label, where in (("single words (headwords)", "entry_count>0 AND token_count=1"),
                             ("phrases (headwords)", "entry_count>0 AND token_count>1")):
            for lang in ("en", "ta"):
                ids = [r[0] for r in c.execute(f"SELECT id FROM word WHERE lang=? AND {where}", (lang,))]
                words = [c.execute("SELECT text FROM word WHERE id=?", (i,)).fetchone()[0] for i in rnd.sample(ids, min(n, len(ids)))]
                for w in words[:5]:                 # warm-up (page cache, branch predictors)
                    eng.lookup(w, Perf())
                rows = []
                for w in words:
                    p = Perf()
                    eng.lookup(w, p)
                    rows.append({"total": p.total(), **p.stages})
                self.stdout.write(f"\n== {lang} {label}: {len(words)} lookups")
                self.stdout.write(f"{'stage':<10}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}  ms")
                for k in ["total", "resolve", "entries", "relations", "inferred", "vector", "phrases"]:
                    v = np.array([r.get(k, 0.0) for r in rows])
                    self.stdout.write(f"{k:<10}{np.percentile(v, 50):>9.1f}{np.percentile(v, 95):>9.1f}{np.percentile(v, 99):>9.1f}{v.max():>9.1f}")
