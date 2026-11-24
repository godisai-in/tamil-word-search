# Embedding + OpenSearch pipeline

```
data_raw/*  ──extract_words.py──▶ out/words.jsonl           713,594 words (en 348,862 / ta 364,732)
out/words.jsonl ──embed_words.py──▶ out/shards/*.npz        raw vectors, resumable, ~2.5-3.5 h on this laptop CPU
out/shards ──pack_pkl.py──▶ out/embeddings.pkl              ONE file: docs + float16 vectors + mapping + recipe
out/embeddings.pkl ──upload_to_opensearch.py──▶ OpenSearch  later, idempotent
```

Run with the project venv (`.venv`, transformers 4.46.3 — the global transformers 5.x needs torch ≥ 2.6):

```bash
.venv/bin/python pipeline/extract_words.py                                   # ~25 s
.venv/bin/python pipeline/embed_words.py --threads 8                         # resumable; re-run to continue
.venv/bin/python pipeline/pack_pkl.py                                        # --center lang to enable centering
.venv/bin/python pipeline/upload_to_opensearch.py --dry-run                  # no network
OPENSEARCH_USER=… OPENSEARCH_PASSWORD=… \
.venv/bin/python pipeline/upload_to_opensearch.py --host https://HOST:9200 --index words_v1 --create
```

## Decisions

* **Model:** `ai4bharat/IndicBERTv2-MLM-Sam-TLM` (MIT, 768-d, covers English + Tamil). One model for both languages, so one vector space.
* **Document id** = `sha1(lang \x1f norm)[:20]`, deterministic. Re-uploading overwrites instead of duplicating, and it does not depend on SQLite row ids.
* **Text embedded** = `norm` (same normaliser as ingest and queries: `textnorm.py`).
* **Filters on extraction:** headwords ≤ 8 tokens; words split out of a meaning field ≤ 4 tokens (longer ones are descriptions); 4+-token strings mixing Tamil and Latin letters dropped.
* **Vectors stored float16** in the pkl (1.1 GB instead of 2.2 GB); upcast to float32 on upload.
* **`pkl` is trusted-input only** — unpickle only files you built.

## Measured, and what it means

* Speed: ~70-90 words/s on a Ryzen 5 5500U (no AVX-512, shared with the desktop). More threads was *slower* (12 < 8). int8 dynamic quantisation was 1.7× faster but changed vectors too much (cosine to fp32 averaged 0.77), so it is not used.
* **Quality warning.** IndicBERT is a masked-language model, not a sentence encoder. On 30 common English↔Tamil word pairs, raw mean-pooled vectors retrieved the right translation top-1 only ~20% of the time (en→ta; chance 3%) and ~63% ta→en. Generic per-language centering did not help. Treat these vectors as a *signal*, not the relation engine. The dictionary edges (see `schema/`) remain the source of truth for translations and synonyms. If cross-lingual quality matters, the next options are a learned alignment fitted on the ~350k dictionary pairs, or a true sentence encoder such as LaBSE.

## Query shape (search side, later)

```json
{ "size": 10,
  "query": { "knn": { "embedding": { "vector": [ ... 768 floats ... ], "k": 10,
      "filter": { "term": { "lang": "ta" } } } } } }
```
The query vector must come from `embedder.Embedder.embed_raw` + `finalize(..., means)` using the `vector_prep` stored in the pkl.
