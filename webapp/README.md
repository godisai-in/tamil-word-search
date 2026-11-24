# Word Search web app (Django, search only)

No users, no sessions, no ratings, no database models. The frontend is Kalaisol-T3F's (same `site.css`,
same layout language: cards, chips, collapsible source lists, autocomplete), reduced to search.

## Run

Prerequisites were built once (see `../pipeline/README.md`): `out/words.db`, `out/vec_*.npy`.

```bash
cd webapp
../.venv/bin/python manage.py runserver --noreload 127.0.0.1:8765
```

Open http://127.0.0.1:8765. Use `--noreload`: with the reloader there are two processes and each would hold ~2.2 GB of vectors.

| env var | default | meaning |
|---|---|---|
| `WORDSEARCH_WARM` | `en,ta` | languages whose vectors load into RAM at start-up (1.1 GB each, ~1-2 s). Empty = load on first use, so the first search in that language pays the load. |
| `WORDSEARCH_DATA` | `../out` | folder with `words.db` and `vec_*.npy` |
| `DJANGO_DEBUG` | `1` | set `0` for anything that is not your own laptop |

## Pages

* `/` search box, examples, counts · `/search/?q=` results · `/suggest/?q=` autocomplete JSON
* `/sources/`: all 76 sources with licence, trust, definition and link counts, and share of the data
* `/perf/` and `/perf.json`: latency per stage (p50/p95/max) for searches since start, memory, vector load times
* Each results page shows its own stage timings at the top and sends a `Server-Timing` header (visible in browser dev tools).

## What a results page contains

0. **Source breakdown** card: which sources the word's evidence comes from, with licence, definition count, link count by relation type.
1. Translations (Tamil for an English word, English for a Tamil word), ranked by source count/trust/method, each expandable to its sources: dictionary, licence, link, page/sense ref, the verbatim text, how the link was derived.
2. Synonyms, antonyms, broader/narrower terms, derived words, related (from Wiktionary and WordNet).
3. *Possibly related*: inferred through shared translations (needs ≥ 2 distinct pivot words); labelled as not stated by any source.
4. Definitions by source (Wiktionary glosses, dictionary meanings, examples).
5. Similar by meaning: IndicBERT vector neighbours, same language. A weak topical signal; labelled as such.
6. Phrases containing the word.

Inflected Tamil forms resolve to their headword (Wiktionary forms); typos get "did you mean" candidates.

## Performance

Measured on the live server, 800 random headwords (200 each: English single / English phrase / Tamil single / Tamil phrase), wall time including HTTP and page render, on a Ryzen 5 5500U laptop with other apps running:

| set | p50 | p95 | max |
|---|---|---|---|
| en single | 55 ms | 76 | 126 |
| en phrase | 52 | 67 | 90 |
| ta single | 61 | 91 | 117 |
| ta phrase | 55 | 68 | 89 |

About 50 ms of a typical request is the brute-force vector scan (350k × 768 float32 dot products); everything SQL-side is under ~20 ms combined. Process memory is ~2.4 GB with both languages loaded. `python manage.py benchmark --n 300` repeats the measurement without HTTP, but it loads its own copy of the vectors, so stop the server first on a low-memory machine.

## Swapping in OpenSearch later

All vector access is in `search/engine.py::VectorIndex.neighbours(word_id, lang, k)`. Replace its body with a kNN query against the index from `../opensearch/words_index.json` (filter `lang`, use the word's `doc_id` to fetch its vector) and the rest of the app is unchanged.
