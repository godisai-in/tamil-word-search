"""The search engine: resolve a query to a word, gather evidence-backed relations, vector neighbours.

Reads out/words.db (schema/schema.sql) read-only, plus out/vec_*.npy. No Django ORM, no users.
Every stage is timed through a Perf object so the UI can show where the milliseconds go.
"""
import json
import re
import sqlite3
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from textnorm import norm  # noqa: E402  (pipeline/ is on sys.path, see config.settings)

TAMIL = re.compile(r"[஀-௿]")
METHOD_FACTOR = {"explicit": 1.0, "whole_gloss": 0.9, "marker": 0.8, "short_gloss": 0.7, "list_item": 0.6}
REL_ORDER = ["synonym", "antonym", "hypernym", "hyponym", "derived", "base", "coordinate", "related"]
REL_LABEL = {
    "synonym": ("ஒத்த சொற்கள்", "Synonyms"), "antonym": ("எதிர்ச்சொற்கள்", "Antonyms"),
    "hypernym": ("பொதுச்சொற்கள்", "Broader terms"), "hyponym": ("சிறப்புச்சொற்கள்", "Narrower terms"),
    "derived": ("வழிச்சொற்கள்", "Derived words"), "base": ("மூலச்சொல்", "Derived from"),
    "coordinate": ("இணைச்சொற்கள்", "Sibling terms"), "related": ("தொடர்புடையவை", "Related"),
}


def lang_of_query(q):
    return "ta" if TAMIL.search(q) else "en"


class Perf:
    """Per-request stage timings (milliseconds)."""

    def __init__(self):
        self.t0 = time.perf_counter()
        self.stages = {}

    @contextmanager
    def stage(self, name):
        t = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] = self.stages.get(name, 0.0) + (time.perf_counter() - t) * 1000

    def total(self):
        return (time.perf_counter() - self.t0) * 1000


class VectorIndex:
    """Brute-force cosine search. Vectors are unit-norm, so cosine == dot product.
    float32 resident per language (~1.1 GB each) measured at ~45 ms for 350k rows."""

    backend = "numpy"

    def __init__(self, out_dir):
        self.dir = Path(out_dir)
        self._lock = threading.Lock()
        self.mat = {}
        self.ids = {}
        self.row_of = {}
        self.load_seconds = {}
        self.available = (self.dir / "vectors_meta.json").exists()
        if self.available:
            self.meta = json.loads((self.dir / "vectors_meta.json").read_text())
            for lang in ("en", "ta"):
                ids = np.load(self.dir / f"vec_{lang}_ids.npy")
                self.ids[lang] = ids
                row_of = np.full(int(ids.max()) + 1, -1, dtype=np.int32)
                row_of[ids] = np.arange(len(ids), dtype=np.int32)
                self.row_of[lang] = row_of

    def load(self, lang):
        with self._lock:
            if lang in self.mat:
                return self.mat[lang]
            t = time.perf_counter()
            src = np.load(self.dir / f"vec_{lang}.npy", mmap_mode="r")
            m = np.empty(src.shape, dtype=np.float32)
            for i in range(0, len(src), 50000):       # chunked so the peak is one extra chunk, not 2x
                m[i:i + 50000] = src[i:i + 50000]
            self.mat[lang] = m
            self.load_seconds[lang] = round(time.perf_counter() - t, 2)
            return m

    def neighbours(self, word_id, lang, k=12):
        """-> [(word_id, cosine)] most similar words in the same language, excluding the word itself."""
        if not self.available or word_id >= len(self.row_of[lang]) or self.row_of[lang][word_id] < 0:
            return []
        m = self.load(lang)
        row = int(self.row_of[lang][word_id])
        sims = m @ m[row]
        sims[row] = -1.0
        top = np.argpartition(-sims, k)[:k]
        top = top[np.argsort(-sims[top])]
        return [(int(self.ids[lang][i]), float(sims[i])) for i in top]


class Engine:
    def __init__(self, out_dir, vectors=None):
        """`vectors`: None -> in-process numpy index from out_dir; or a callable(conn_factory) -> backend."""
        self.dir = Path(out_dir)
        self.db_path = self.dir / "words.db"
        self._local = threading.local()
        self._stats_lock = threading.Lock()
        self._source_stats = None
        self.vectors = vectors(self.conn) if callable(vectors) else VectorIndex(self.dir)
        c = self.conn()
        self.stats = {
            "words": c.execute("SELECT COUNT(*) FROM word").fetchone()[0],
            "words_en": c.execute("SELECT COUNT(*) FROM word WHERE lang='en'").fetchone()[0],
            "words_ta": c.execute("SELECT COUNT(*) FROM word WHERE lang='ta'").fetchone()[0],
            "entries": c.execute("SELECT COUNT(*) FROM entry").fetchone()[0],
            "relations": c.execute("SELECT COUNT(*) FROM relation").fetchone()[0],
            "sources": c.execute("SELECT COUNT(*) FROM source").fetchone()[0],
            "db_mb": round(self.db_path.stat().st_size / 1e6),
        }

    def conn(self):
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA cache_size=-200000")
            c.execute("PRAGMA mmap_size=1073741824")
            self._local.c = c
        return c

    # ------------------------------------------------------------------ resolving the query
    def resolve(self, q, lang):
        """-> (word_row | None, how, candidates). `how` says how we matched, shown to the user."""
        c = self.conn()
        n = norm(q)
        if not n:
            return None, None, []
        w = c.execute("SELECT * FROM word WHERE lang=? AND norm=?", (lang, n)).fetchone()
        if w:
            return w, "exact", []
        f = c.execute("""SELECT w.*, f.form, f.tags FROM word_form f JOIN word w ON w.id=f.word_id
                         WHERE f.norm=? AND w.lang=? ORDER BY w.entry_count DESC LIMIT 1""", (n, lang)).fetchone()
        if f:
            return f, "form", []
        cands = c.execute("""SELECT * FROM word WHERE lang=? AND norm>=? AND norm<? 
                             ORDER BY entry_count>0 DESC, token_count, length(norm) LIMIT 10""",
                          (lang, n, n + "￿")).fetchall()
        how = "prefix"
        if not cands and len(n) >= 3:
            cands = self._fts("word_fts", f'"{n.replace(chr(34), "")}"', lang, 10)
            how = "contains"
        if cands:
            return None, how, cands
        return None, None, []

    def _fts(self, table, match, lang, limit):
        return self.conn().execute(
            f"""SELECT w.* FROM {table} f JOIN word w ON w.id=f.rowid WHERE f.{'norm'} MATCH ? AND w.lang=?
                ORDER BY w.entry_count>0 DESC, w.token_count, length(w.norm) LIMIT ?""", (match, lang, limit)).fetchall()

    def suggest(self, q, limit=8):
        n = norm(q)
        if len(n) < 2:
            return []
        lang = lang_of_query(n)
        rows = self.conn().execute(
            """SELECT text FROM word WHERE lang=? AND norm>=? AND norm<? 
               ORDER BY entry_count>0 DESC, token_count, length(norm) LIMIT ?""", (lang, n, n + "￿", limit)).fetchall()
        return [r["text"] for r in rows]

    def phrases_with(self, word, lang, limit=12):
        """Multi-word terms that contain this word as a token ('draft' -> 'bank draft')."""
        n = word["norm"].replace('"', "")
        return self.conn().execute(
            """SELECT w.* FROM word_token_fts f JOIN word w ON w.id=f.rowid
               WHERE f.norm MATCH ? AND w.lang=? AND w.token_count>1 AND w.id<>?
               ORDER BY w.entry_count>0 DESC, w.token_count, length(w.norm) LIMIT ?""",
            (f'"{n}"', lang, word["id"], limit)).fetchall()

    # ------------------------------------------------------------------ evidence
    def entries_of(self, word_id, limit=40):
        return [dict(r) for r in self.conn().execute(
            """SELECT e.id, e.pos, e.gloss, e.gloss_lang, e.context, e.source_ref, e.source_url, e.quality,
                      s.name AS source, s.license, s.url AS source_home, s.trust, s.collection
               FROM entry e JOIN source s ON s.id=e.source_id WHERE e.word_id=?
               ORDER BY s.trust*e.quality DESC, s.name LIMIT ?""", (word_id, limit))]

    def examples_for(self, entry_ids):
        if not entry_ids:
            return {}
        out = defaultdict(list)
        q = ",".join("?" * len(entry_ids))
        for r in self.conn().execute(f"SELECT entry_id,text,translation,romanized FROM example WHERE entry_id IN ({q})", entry_ids):
            out[r["entry_id"]].append(dict(r))
        return out

    def _edges(self, word_id, rels=None, limit=6000):
        sql = """SELECT r.rel, r.dst_word_id, r.method, r.weight, w.text, w.lang, w.entry_count,
                        e.gloss, e.context, e.source_ref, e.source_url, e.quality, h.text AS headword,
                        s.name AS source, s.license, s.url AS source_home, s.trust
                 FROM relation r JOIN word w ON w.id=r.dst_word_id
                 JOIN entry e ON e.id=r.entry_id JOIN word h ON h.id=e.word_id JOIN source s ON s.id=e.source_id
                 WHERE r.src_word_id=?"""
        args = [word_id]
        if rels:
            sql += " AND r.rel IN (%s)" % ",".join("?" * len(rels))
            args += rels
        sql += " LIMIT ?"
        return self.conn().execute(sql, args + [limit]).fetchall()

    @staticmethod
    def _group(rows, via=None):
        """rows -> targets sorted by score; each target carries its evidence list."""
        g = {}
        for r in rows:
            t = g.setdefault(r["dst_word_id"], {"id": r["dst_word_id"], "text": r["text"], "lang": r["lang"],
                                                "score": 0.0, "evidence": [], "contexts": {}, "sources": set()})
            ev = r["weight"] * r["trust"] * r["quality"] * METHOD_FACTOR.get(r["method"], 0.6)
            t["score"] += ev
            t["sources"].add(r["source"])
            if r["context"]:
                for part in r["context"].split(" · "):
                    t["contexts"][part] = t["contexts"].get(part, 0) + 1
            t["evidence"].append({"source": r["source"], "license": r["license"], "url": r["source_url"] or r["source_home"],
                                  "ref": r["source_ref"], "gloss": r["gloss"], "context": r["context"],
                                  "method": r["method"], "headword": r["headword"], "via": via})
        out = sorted(g.values(), key=lambda t: -t["score"])
        for t in out:
            t["n_sources"] = len(t["sources"])
            t["evidence"].sort(key=lambda e: e["method"] != "explicit")
            t["contexts"] = [k for k, _ in sorted(t["contexts"].items(), key=lambda kv: -kv[1])[:3]]
        return out

    def source_stats(self):
        """Every source with its entry / relation / word counts. ~1 s once, then cached."""
        with self._stats_lock:
            if self._source_stats is None:
                c = self.conn()
                rel = dict(c.execute("SELECT e.source_id, COUNT(*) FROM relation r JOIN entry e ON e.id=r.entry_id GROUP BY e.source_id"))
                ent = {r[0]: (r[1], r[2]) for r in c.execute(
                    "SELECT source_id, COUNT(*), COUNT(DISTINCT word_id) FROM entry GROUP BY source_id")}
                rows = []
                for s in c.execute("SELECT * FROM source ORDER BY id"):
                    n_ent, n_words = ent.get(s["id"], (0, 0))
                    rows.append({**dict(s), "entries": n_ent, "headwords": n_words, "relations": rel.get(s["id"], 0)})
                self._source_stats = sorted(rows, key=lambda r: -(r["entries"] + r["relations"]))
            return self._source_stats

    # ------------------------------------------------------------------ the whole page
    def lookup(self, q, perf):
        lang = lang_of_query(q)
        out = {"q": q, "lang": lang, "word": None, "how": None, "form": None, "candidates": [], "entries": [],
               "translations": [], "relations": [], "inferred": [], "breakdown": [], "similar": [], "phrases": [], "counts": {}}
        with perf.stage("resolve"):
            w, how, cands = self.resolve(q, lang)
        out["how"], out["candidates"] = how, [dict(c) for c in cands]
        if w is None:
            return out
        out["word"] = dict(w)
        if how == "form":
            out["form"] = {"form": w["form"], "tags": w["tags"]}
        wid = w["id"]

        with perf.stage("entries"):
            out["entries"] = self.entries_of(wid)
            ex = self.examples_for([e["id"] for e in out["entries"]])
            for e in out["entries"]:
                e["examples"] = ex.get(e["id"], [])[:2]

        with perf.stage("relations"):
            edges = self._edges(wid)
            out["counts"]["edges"] = len(edges)
            by_rel = defaultdict(list)
            for r in edges:
                by_rel[r["rel"]].append(r)
            out["translations"] = self._group(by_rel.get("translation", []))
            for rel in REL_ORDER:
                if by_rel.get(rel):
                    ta_label, en_label = REL_LABEL[rel]
                    out["relations"].append({"rel": rel, "ta": ta_label, "en": en_label, "items": self._group(by_rel[rel])})

        with perf.stage("breakdown"):
            bd = {}
            for r in edges:
                b = bd.setdefault(r["source"], {"source": r["source"], "license": r["license"], "url": r["source_home"],
                                               "relations": 0, "entries": 0, "by_rel": defaultdict(int)})
                b["relations"] += 1
                b["by_rel"][r["rel"]] += 1
            for en in out["entries"]:
                b = bd.setdefault(en["source"], {"source": en["source"], "license": en["license"], "url": en["source_home"],
                                                "relations": 0, "entries": 0, "by_rel": defaultdict(int)})
                b["entries"] += 1
            for b in bd.values():
                b["by_rel"] = sorted(b["by_rel"].items(), key=lambda kv: -kv[1])
                b["total"] = b["relations"] + b["entries"]
            out["breakdown"] = sorted(bd.values(), key=lambda b: -b["total"])

        with perf.stage("inferred"):
            direct = {t["id"] for t in out["translations"]} | {t["id"] for g in out["relations"] for t in g["items"]} | {wid}
            pivots = defaultdict(list)
            for t in out["translations"][:15]:
                for r in self._edges(t["id"], ["translation"], limit=400):
                    if r["dst_word_id"] not in direct:
                        pivots[r["dst_word_id"]].append((t["text"], r))
            inferred = []
            for dst, items in pivots.items():
                if len({v for v, _ in items}) >= 2:         # polysemy guard: >= 2 distinct pivot words
                    grp = self._group([r for _, r in items])[0]
                    grp["score"] *= 0.5
                    grp["pivots"] = sorted({v for v, _ in items})
                    inferred.append(grp)
            out["inferred"] = sorted(inferred, key=lambda t: -t["score"])[:12]
            out["counts"]["pivot_candidates"] = len(pivots)

        with perf.stage("vector"):
            nn = self.vectors.neighbours(wid, lang, 14)
            if nn:
                ids = [i for i, _ in nn]
                rows = {r["id"]: r for r in self.conn().execute(
                    "SELECT * FROM word WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)}
                known = direct | {t["id"] for t in out["inferred"]}
                out["similar"] = [{"id": i, "text": rows[i]["text"], "score": s, "also_related": i in known}
                                  for i, s in nn if i in rows][:12]

        with perf.stage("phrases"):
            out["phrases"] = [dict(r) for r in self.phrases_with(w, lang)]
        return out
