---
pretty_name: Kalaisol Tamil–English Word Graph
language:
  - ta
  - en
license: cc-by-sa-4.0
task_categories:
  - translation
  - sentence-similarity
  - feature-extraction
size_categories:
  - 1M<n<10M
tags:
  - tamil
  - english
  - dictionary
  - thesaurus
  - lexicon
  - wordnet
  - wiktionary
  - embeddings
  - knowledge-graph
  - word-search
configs:
  - config_name: words
    default: true
    data_files:
      - split: train
        path: out/words.jsonl
  - config_name: tva_glossary
    data_files:
      - split: train
        path: data_raw/tva_glossary.jsonl
---

# Kalaisol Tamil–English Word Graph

A Tamil ↔ English lexical resource: **722k words** (357k English, 365k Tamil) joined by **2.0M typed relations** (translation, synonym, hypernym, …), each relation traced back to the dictionary entry that asserts it, plus **768-dimensional IndicBERT embeddings** for 713k of the words.

It was built to power a word-search tool: type an English or Tamil word, get related words as suggestions, each with its source data. The raw source files and the build pipeline outputs are both included, so you can use the finished graph or rebuild it.

## What is in this repository

| Path | What | Size | Use it for |
|---|---|---|---|
| `out/words.db` | **SQLite graph** (schema v2.1): sources, words, entries, relations, inflections, examples, FTS5 indexes | 726 MB | Querying the finished graph. Start here. |
| `out/words.jsonl` | One row per distinct word (the file behind the dataset viewer) | 196 MB | Word lists, joins, quick analysis |
| `out/embeddings.pkl` | Words + float16 vectors + OpenSearch index mapping | 1.3 GB | Bulk-loading a vector store. **Pickle: see safety note.** |
| `out/vec_en.npy`, `vec_ta.npy`, `vec_*_ids.npy`, `vectors_meta.json` | The same vectors as plain NumPy arrays (float16, rows aligned to the `_ids` files) | 1.1 GB | Loading vectors without pickle |
| `out/*_report.json`, `*.log` | Build statistics and logs | small | Auditing |
| `data_raw/mydictionary/` | Scrape of mydictionary.in: `dictionaries.json` (72 dictionaries), `mydictionary.jsonl` (361k rows), `queries.jsonl`, raw HTML pages | large | Rebuilding, provenance |
| `data_raw/tamil.jsonl` | English Wiktionary, Tamil entries (13.9k entries) | 89 MB | Rebuilding; Tamil morphology and synonyms |
| `data_raw/tva_glossary.jsonl`, `tva_kalanjiyam.jsonl` | Tamil Virtual Academy glossary (6.4k rows) and Kalanjiyam (1.3k rows, OCR) | 1.5 MB | Rebuilding |
| `data_raw/nltk/corpora/` | Princeton WordNet 3.0 and Open Multilingual Wordnet 1.4 (zip) | small | Rebuilding (English relations only; OMW has no Tamil) |

## Quick start

Query the graph (no installs beyond `huggingface_hub`):

```python
import sqlite3
from huggingface_hub import hf_hub_download

path = hf_hub_download("godisai-in/kalaisol-tamil-dataset", "out/words.db", repo_type="dataset")
db = sqlite3.connect(path)

# translations of "bank", with the source that says so
for row in db.execute("""
    SELECT w2.text, s.name, s.license
    FROM word w1
    JOIN relation r  ON r.src_word_id = w1.id AND r.rel = 'translation'
    JOIN word w2     ON w2.id = r.dst_word_id
    JOIN entry e     ON e.id = r.entry_id
    JOIN source s    ON s.id = e.source_id
    WHERE w1.lang = 'en' AND w1.norm = 'bank'
    ORDER BY r.weight DESC LIMIT 10"""):
    print(row)
```

The default configuration loads the word table in the viewer and with `datasets`:

```python
from datasets import load_dataset
words = load_dataset("godisai-in/kalaisol-tamil-dataset", "words", split="train")
print(words[0])
```

Nearest neighbours from the NumPy vectors:

```python
import numpy as np, json
from huggingface_hub import hf_hub_download as dl
get = lambda f: dl("godisai-in/kalaisol-tamil-dataset", f, repo_type="dataset")

vec = np.load(get("out/vec_ta.npy"), mmap_mode="r")      # (364264, 768) float16, unit length
ids = np.load(get("out/vec_ta_ids.npy"))                 # doc ids, same row order
q = np.asarray(vec[0], dtype="float32")
sims = np.asarray(vec, dtype="float32") @ q              # unit vectors: dot product = cosine
top = np.argsort(-sims)[:10]
print(ids[top])                                          # map ids to text via words.jsonl / word.doc_id
```

## Data model

`out/words.db` is a graph. Full DDL: `schema/schema.sql` in the source repository.

```
source ──< entry >── word ──< relation >── word
 (who says)  (what the source  (one node per   (typed, directed; every edge
              says, verbatim)   lang+normalised  cites the entry that asserts it)
                                form)
                         word ──< word_form       (inflections and variants resolving to the word)
                         word ──  word_fts, word_token_fts   (FTS5: trigram and token search)
                         entry ──< example        (Wiktionary usage sentences)
```

| Table | Rows | Notes |
|---|---:|---|
| `source` | 76 | 72 mydictionary.in dictionaries + Wiktionary + TVA glossary + TVA Kalanjiyam + WordNet. Carries `license`, `attribution`, `trust`. |
| `word` | 722,222 | `lang` (`en`/`ta`), display `text`, normalised `norm`, `token_count`, `doc_id` (join key to the vectors), `entry_count` |
| `entry` | 603,475 | One source record, kept verbatim: gloss, domain note, page, URL, `origin_file:line` back to `data_raw` |
| `relation` | 2,013,981 | `src_word_id → dst_word_id`, `rel`, `method`, `weight`, `entry_id` |
| `word_form` | 198,080 | Wiktionary inflections (case, tense, honorific, …) with grammatical `tags` |
| `example` | 1,484 | Usage sentence, translation, romanisation |

Relations by type: translation 1,007,210 · synonym 370,032 · hypernym 290,422 · hyponym 285,349 · related 28,020 · base 11,656 · derived 11,656 · antonym 8,364 · coordinate 1,272.

**`relation.method` tells you how much to trust an edge**, since much of the Tamil side comes from splitting free-text meanings:

| method | edges | meaning | weight |
|---|---:|---|---:|
| `explicit` | 984,715 | the source states the relation (Wiktionary links, WordNet) | 1.0 |
| `whole_gloss` | 540,902 | the whole meaning text is one target | 0.9 |
| `marker` | 1,616 | a marker such as `(மறுவ.)` ("also called") | 0.8 |
| `short_gloss` | 35,382 | a short gloss of ≤3 tokens | 0.7 |
| `list_item` | 451,366 | one item of a `;`/`,` separated list (heuristic, over-splits) | 0.6 |

Words are keyed by `(lang, norm)`: `bank` from ten dictionaries is one node, and an identical edge asserted by N entries is N evidence rows. `word.doc_id = sha1(lang + "\x1f" + norm)[:20]` is stable, so it can be used across rebuilds.

### `words.jsonl` columns

| Column | Type | Description |
|---|---|---|
| `id` | string | the `doc_id` described above |
| `lang` | `en` \| `ta` | script-detected language |
| `text` | string | display form |
| `norm` | string | normalised form used for matching |
| `token_count` | int | whitespace tokens; about two thirds of words are multi-word phrases |
| `headword` | bool | appears as a dictionary headword (not only as a translation target) |
| `collections` | list | which of `mydictionary`, `wiktionary`, `tva`, `wordnet` contain it |
| `dictionaries` | list | which dictionaries contain it |

Normalisation: Unicode NFC, zero-width characters removed, whitespace collapsed, edge punctuation trimmed; English additionally lowercased. Tamil is **not** stemmed; inflections are linked through `word_form` instead.

## Embeddings

| | |
|---|---|
| Model | `ai4bharat/IndicBERTv2-MLM-Sam-TLM` (one model for both languages) |
| Vector | 768 dims, mean-pooled over tokens with special tokens excluded, max length 32, L2-normalised, stored as float16 |
| Coverage | English 348,840 of 357,630 words (97.5%); Tamil 364,264 of 364,592 (99.9%) |
| Index mapping | OpenSearch `knn_vector`, Lucene HNSW, cosine (`m=16`, `ef_construction=128`), included in the pkl |

**Read this before relying on them.** Measured on this data:

* Neighbours **within one language** are *topical*, not synonyms. The first few are often good ("bank" → "banking"); further down they drift to the same general area.
* **Cross-lingual** retrieval is weak: about 20% top-1 for English → Tamil on a held-out set. Use the dictionary relations for translation, not the vectors.
* Mean-centering and int8 quantisation were tested and rejected (no gain, and cosine fell to 0.77 for int8).

## Source statistics

| Source | Rows used | Licence |
|---|---:|---|
| mydictionary.in, 72 dictionaries | 361k entries (595k headword count) | CC BY-SA ×69 (spelled `CC BY-SA` / `CC-BY-SA`), CC BY-SA 4.0 ×1, CC0 1.0 ×1, **none stated ×1** |
| English Wiktionary (Tamil entries) | 13.9k entries, ~27.9k synonym links, ~198k word forms | CC BY-SA 4.0 |
| Princeton WordNet 3.0 | English synsets: synonym, hypernym, hyponym | WordNet License |
| TVA Science Glossary | 6.4k term pairs | not stated |
| TVA Kalanjiyam | 1.3k term pairs, OCR output | not stated |

## Licensing

**The compilation is released under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).** Most of the data comes from CC BY-SA sources (mydictionary.in, Wiktionary), whose share-alike terms apply to derivatives such as this graph and the embeddings. Attribution is required: credit the sources below and keep the provenance in the `source` table, where each row carries its own `license`, `attribution` and URL (every relation points at an entry, and every entry at a source).

| Part | Original licence |
|---|---|
| mydictionary.in dictionaries (72) | CC BY-SA (one CC BY-SA 4.0); Vetri Dictionary is CC0; one dictionary states none (எண்ணியல் அகராதி, 0 headwords, contributes no rows) |
| English Wiktionary (Tamil entries) | CC BY-SA 4.0 |
| Princeton WordNet 3.0 | WordNet License. Keep its copyright notice. |
| TVA Science Glossary, TVA Kalanjiyam | **Not stated by the publisher.** Included in good faith; if you are the rights holder and want them removed, open a discussion on this repository. |
| IndicBERT (embeddings) | MIT, AI4Bharat |

This card is a good-faith summary, not legal advice.

## Known limitations

* **Tamil targets are heuristic.** Tamil comes from splitting dictionary meaning text on `;` and `,`. `list_item` edges (451k) over-split; filter on `method` or `weight` for precision.
* **Pivot synonyms are inferred, not stated.** Synonyms through shared translations need ≥2 distinct pivot words to limit polysemy (`bank` → *வங்கி* / *கரை*). These are computed at query time, not stored.
* **No sense disambiguation.** Dictionary rows rarely say which sense they mean; the `context` field is the only signal.
* **Noisy sources.** TVA Kalanjiyam is OCR output (`trust < 1`); the largest dictionary mixes Tamil, English and Sanskrit text.
* **English coverage is partial and skewed** to technical vocabulary (the dictionaries are mostly terminology lexicons) and WordNet.
* 35,668 words were dropped as unusable (overlong, mixed-script fragments) when building `words.jsonl`.

## Safety note on `embeddings.pkl`

Pickle files can execute code when loaded. Only load `out/embeddings.pkl` if you trust this repository, or use the `.npy` files plus `words.jsonl`, which carry the same data without pickle.

## How it was built

1. `extract_words.py`: normalise and filter all sources → `words.jsonl`
2. `embed_words.py` + `pack_pkl.py`: IndicBERT embeddings → shards → `embeddings.pkl`
3. `build_db.py`: ingest sources, relations, forms and examples → `words.db` (83 s, 0 foreign-key violations)
4. `export_vectors.py`: `.npy` export for numpy-only use

The search application, OpenSearch index mapping, loader and deployment files are in the source repository (`pipeline/`, `schema/`, `opensearch/`, `webapp/`, `deploy/`).

## Citation and attribution

If you use this resource, please credit the underlying sources: mydictionary.in and its dictionary authors, Wiktionary contributors, Princeton University (WordNet), and the Tamil Virtual Academy. Suggested citation for the compilation:

```bibtex
@misc{kalaisol_tamil_word_graph_2026,
  title        = {Kalaisol Tamil--English Word Graph},
  author       = {godisai-in},
  year         = {2026},
  howpublished = {\url{https://huggingface.co/datasets/godisai-in/kalaisol-tamil-dataset}}
}
```
