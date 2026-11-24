# Tamil Word Search

Type an English or Tamil word and get back related words: translations, synonyms, broader and narrower terms, and words with a similar meaning. Every suggestion shows **which dictionary said so**, so you can check it and see its licence.

It runs on a word graph of **722k words** (357k English, 365k Tamil) joined by **2 million typed links**, built from 76 open sources. The data is published as a Hugging Face dataset (see below).

> **Dataset:** [`godisai-in/kalaisol-tamil-dataset`](https://huggingface.co/datasets/godisai-in/kalaisol-tamil-dataset)
> **Code:** this repository

---

## In plain terms

Think of the whole thing as a very large, very careful word-map.

* Every word (English or Tamil) is a **dot** on the map.
* A **line** between two dots means "these are related": one is a translation of the other, or a synonym, or a broader term (a *bank* is a kind of *institution*).
* Every line is **stamped with its source**: "mydictionary.in, dictionary X, page 12" or "Wiktionary". Nothing is made up without being labelled.
* Each word also has a **meaning fingerprint** (a list of 768 numbers from an AI language model called IndicBERT). Words whose fingerprints are close are *about the same topic*. This powers the "similar by meaning" box.

When you search "bank", the app finds the dot, follows its lines, and shows you what it found, grouped and ranked, with the proof next to each result.

## How the interface works

### What you see

```
┌──────────────────────────────────────────────────────────────┐
│  Tamil Word Search            [ Search ] [ Sources ] [ Perf ]│  ← header
├──────────────────────────────────────────────────────────────┤
│                                                              │
│        ┌────────────────────────────────────────┐            │
│        │  bank                                🔍│            │  ← search box
│        └────────────────────────────────────────┘            │
│          ▾ bank          (suggestions appear                 │
│          ▾ banking         as you type)                      │
│          ▾ வங்கி                                              │
│                                                              │
│   Try:  [ bank ]  [ water ]  [ நீர் ]  [ school ]             │  ← examples
│   722k words · 2.0M links · 76 sources                       │
└──────────────────────────────────────────────────────────────┘
```

### The results page

```
┌──────────────────────────────────────────────────────────────┐
│  Results for "bank"                                          │
├──────────────────────────────────────────────────────────────┤
│  [0] SOURCE BREAKDOWN                                        │
│      where the evidence for this word comes from,            │
│      with licence and number of definitions / links          │
├──────────────────────────────────────────────────────────────┤
│  [1] TRANSLATIONS          (English → Tamil)                 │
│      வங்கி   ▸ 6 sources   ← click to open the evidence      │
│      கரை     ▸ 3 sources      dictionary · licence · page    │
│                               · original text · how derived  │
├──────────────────────────────────────────────────────────────┤
│  [2] SYNONYMS · ANTONYMS · BROADER / NARROWER · DERIVED      │
│      (from Wiktionary and WordNet)                           │
├──────────────────────────────────────────────────────────────┤
│  [3] POSSIBLY RELATED      inferred, not stated by a source  │
├──────────────────────────────────────────────────────────────┤
│  [4] DEFINITIONS           by source, with usage examples    │
├──────────────────────────────────────────────────────────────┤
│  [5] SIMILAR BY MEANING    nearest neighbours (topical only) │
├──────────────────────────────────────────────────────────────┤
│  [6] PHRASES CONTAINING THE WORD                             │
└──────────────────────────────────────────────────────────────┘
```

Also handled for you: inflected Tamil forms resolve to their headword, and typos get "did you mean" suggestions.

### Pages and endpoints

| URL | What it does |
|---|---|
| `/` | Home: search box, examples, counts |
| `/search/?q=word` | Results page |
| `/suggest/?q=wo` | Autocomplete (JSON) |
| `/sources/` | All 76 sources with licence, trust level and share of the data |
| `/perf/`, `/perf.json` | Latency per stage, memory use |
| `/healthz` | Health check for deployment |

## How the pieces fit together

```
   ┌────────────────────┐
   │  Raw dictionaries  │  mydictionary.in (72), Wiktionary,
   │    (data_raw/)     │  Tamil Virtual Academy, WordNet
   └─────────┬──────────┘
             │  pipeline/build_db.py
             ▼
   ┌────────────────────┐        ┌───────────────────────────┐
   │  words.db (SQLite) │        │ pipeline/extract_words.py │
   │  the word graph    │        │ pipeline/embed_words.py   │
   │                    │        │  (IndicBERT, 768-d)       │
   └─────────┬──────────┘        └─────────────┬─────────────┘
             │                                 │  pack_pkl.py
             │                                 ▼
             │                   ┌───────────────────────────┐
             │                   │ embeddings.pkl / *.npy    │
             │                   └─────────────┬─────────────┘
             │                                 │  upload_to_opensearch.py
             │                                 ▼
             │                   ┌───────────────────────────┐
             │                   │ OpenSearch (vector index) │
             │                   └─────────────┬─────────────┘
             ▼                                 ▼
   ┌──────────────────────────────────────────────────────┐
   │        Django web app (webapp/)                      │
   │  graph lookups from SQLite + similar-words from vectors │
   └─────────────────────────┬────────────────────────────┘
                             ▼
                    Your browser  (served behind Caddy)
```

## Hugging Face dataset

The data behind this app is public on Hugging Face so anyone can reuse it:

**[huggingface.co/datasets/godisai-in/kalaisol-tamil-dataset](https://huggingface.co/datasets/godisai-in/kalaisol-tamil-dataset)**

It is **also how we collect and share the details of the dataset**: one place holds the finished graph, the raw sources it was built from, the licence of each source, the known limitations, and the numbers (row counts, coverage). The dataset card documents all of it. The card source lives in `dataset_card/README.md`.

| File in the dataset | What it is | Use it for |
|---|---|---|
| `out/words.db` | The SQLite word graph (726 MB) | Querying relations; start here |
| `out/words.jsonl` | One row per word | Word lists, quick analysis, the dataset viewer |
| `out/vec_en.npy`, `vec_ta.npy` (+ `_ids`) | Meaning vectors, plain NumPy | Similarity search without pickle |
| `out/embeddings.pkl` | Words + vectors + OpenSearch mapping | Bulk-loading a vector store (pickle: trusted use only) |
| `data_raw/` | The raw source files | Rebuilding and provenance |

### What the dataset records about each piece of data

* **Where it came from:** every link points to the dictionary entry that asserts it (`origin_file:line` back to the raw file).
* **Its licence:** each of the 76 sources carries its licence and attribution. The compilation is **CC BY-SA 4.0**.
* **How much to trust it:** every link has a `method` (`explicit`, `whole_gloss`, `marker`, `short_gloss`, `list_item`) and a `weight` from 1.0 down to 0.6.
* **Known gaps:** Tamil targets are partly heuristic, there is no sense disambiguation, and cross-language vector retrieval is weak (about 20% top-1 English → Tamil). Use dictionary relations for translation, vectors only for "same topic".

### Use it in two lines

```python
from datasets import load_dataset
words = load_dataset("godisai-in/kalaisol-tamil-dataset", "words", split="train")
```

```python
import sqlite3
from huggingface_hub import hf_hub_download
db = sqlite3.connect(hf_hub_download("godisai-in/kalaisol-tamil-dataset", "out/words.db", repo_type="dataset"))
```

The dataset card has full query examples and the data model.

## Run it

### With Docker (OpenSearch + web)

```bash
cp .env.example .env        # fill in DJANGO_SECRET_KEY, OPENSEARCH_PASSWORD, SITE_ADDRESS
make check                  # host prerequisites and data files
make up                     # build and start OpenSearch + web
make load                   # one-off: load vectors into OpenSearch
make smoke                  # check the running stack
```

Data files `out/words.db` and `out/embeddings.pkl` are needed first: download them from the Hugging Face dataset above, or build them (next section). Production notes (Caddy, ports) are in [`deploy/README.md`](deploy/README.md).

### Locally, without Docker

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cd webapp
../.venv/bin/python manage.py runserver --noreload 127.0.0.1:8765
```

Open http://127.0.0.1:8765. Details in [`webapp/README.md`](webapp/README.md).

### Rebuild the data yourself

```bash
.venv/bin/python pipeline/build_db.py          # raw sources -> out/words.db
.venv/bin/python pipeline/extract_words.py     # -> out/words.jsonl
.venv/bin/python pipeline/embed_words.py       # embeddings (hours on CPU, resumable)
.venv/bin/python pipeline/pack_pkl.py          # -> out/embeddings.pkl
```

Details in [`pipeline/README.md`](pipeline/README.md) and [`schema/README.md`](schema/README.md).

## Repository layout

```
webapp/      Django search app (views, engine, templates, CSS/JS)
pipeline/    build and embedding scripts
schema/      SQLite schema for the word graph
opensearch/  vector index definition
deploy/      Caddy config and deployment notes
dataset_card/ Hugging Face dataset card source
Dockerfile, docker-compose.yml, Makefile, .env.example
```

## Licence and credits

The data compilation is **CC BY-SA 4.0**; source licences are listed on the `/sources/` page and in the dataset card. WordNet keeps its own licence and notice. Embeddings use AI4Bharat IndicBERT (MIT).

## Developers

* **GoDisAI** ([godisai-in](https://github.com/godisai-in))
* **Syed Jafer K** ([@syedjaferk](https://github.com/syedjaferk))
* **Wijay Global** ([wijayglobal](https://github.com/wijayglobal))
