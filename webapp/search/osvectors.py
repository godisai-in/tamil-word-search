"""OpenSearch kNN backend: same interface as engine.VectorIndex, so the engine does not care which is used.

Index layout: opensearch/words_index.json. Document _id = word.doc_id (sha1 of lang+norm), which is how a
SQLite word row finds its vector document.
"""
import logging
import threading
import time

log = logging.getLogger(__name__)


class OpenSearchVectors:
    backend = "opensearch"
    available = True

    def __init__(self, conn_factory, url, index, user=None, password=None, verify_certs=True, timeout=2, cooldown=15.0):
        from opensearchpy import OpenSearch

        self._conn = conn_factory
        self.index = index
        self.client = OpenSearch(
            url,
            http_auth=(user, password) if user else None,
            verify_certs=verify_certs,
            ssl_show_warn=False,
            timeout=timeout,
            max_retries=0,
        )
        self.mat = {}                 # numpy-backend attributes the perf page reads; unused here
        self.load_seconds = {}
        self.meta = {}
        self.last_error = None
        self.cooldown = cooldown          # after a failure, skip OpenSearch for this long (circuit breaker)
        self._down_until = 0.0
        self._health = (0.0, False)
        self._lock = threading.Lock()

    def load(self, lang):             # nothing to warm: the data lives in OpenSearch
        return None

    def ping(self, max_age=5.0):
        """Cheap, cached reachability check for /healthz."""
        with self._lock:
            ts, ok = self._health
            if time.monotonic() - ts < max_age:
                return ok
        try:
            ok = bool(self.client.ping())
        except Exception as e:        # noqa: BLE001 - any transport error means "down"
            self.last_error = repr(e)
            ok = False
        with self._lock:
            self._health = (time.monotonic(), ok)
        return ok

    def info(self):
        try:
            return {"index": self.index, "docs": self.client.count(index=self.index)["count"]}
        except Exception as e:        # noqa: BLE001
            return {"index": self.index, "error": repr(e)}

    def neighbours(self, word_id, lang, k=12):
        """-> [(word_id, cosine)] nearest words in the same language, excluding the word itself."""
        if time.monotonic() < self._down_until:       # recently failed: do not make every search wait for a timeout
            return []
        try:
            c = self._conn()
            row = c.execute("SELECT doc_id FROM word WHERE id=?", (word_id,)).fetchone()
            if row is None:
                return []
            doc_id = row[0]
            try:
                doc = self.client.get(index=self.index, id=doc_id, _source_includes=["embedding"])
            except Exception as e:    # missing doc (word has no vector) is normal, not an error
                if getattr(e, "status_code", None) == 404:
                    return []
                raise
            res = self.client.search(index=self.index, body={
                "size": k + 1, "_source": False,
                "query": {"knn": {"embedding": {"vector": doc["_source"]["embedding"], "k": k + 1,
                                               "filter": {"term": {"lang": lang}}}}},
            })
            hits = [(h["_id"], 2.0 * h["_score"] - 1.0) for h in res["hits"]["hits"] if h["_id"] != doc_id][:k]
            if not hits:
                return []
            ids = [d for d, _ in hits]
            rows = c.execute("SELECT doc_id, id FROM word WHERE doc_id IN (%s)" % ",".join("?" * len(ids)), ids)
            by_doc = {r[0]: r[1] for r in rows}
            self.last_error = None
            # lucene cosinesimil score = (1 + cos) / 2  ->  cos = 2*score - 1
            return [(by_doc[d], cos) for d, cos in hits if d in by_doc]
        except Exception as e:        # noqa: BLE001 - search must keep working if OpenSearch is down
            self.last_error = repr(e)
            self._down_until = time.monotonic() + self.cooldown
            log.warning("OpenSearch neighbours failed (skipping it for %.0fs): %s", self.cooldown, e)
            return []
