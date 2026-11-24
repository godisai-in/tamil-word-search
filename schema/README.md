# Word Search – data schema (v2)

Files: [`schema.sql`](schema.sql) (DDL, tested on SQLite 3.37) · [`related.sql`](related.sql) (the suggestion query, tested on a hand-built graph).

## Collection size (measured, normalised)

~**349k English** + ~**403k Tamil** distinct words (~751k `word` rows); about 2/3 are multi-word phrases (113k single-token English, 120k single-token Tamil). Expected: ~0.5M `entry` rows and a few million `relation` rows, which SQLite handles comfortably.

## Changes in v2.1

`word.doc_id` (join key to OpenSearch/vectors) and `word.entry_count` (headword ranking) added when the SQLite DB was first built (`pipeline/build_db.py`).

## Changes in v2

| Change | Reason (from the numbers) |
|---|---|
| `word_token_fts` (new FTS5 table, Tamil combining marks as token chars) | Most words are phrases; users search "draft" and expect "bank draft". Stock `unicode61` split `வங்கி` into `வங`,`கி`, so the Tamil marks are declared token characters (tested). |
| `word` index on `(lang, token_count)` | Rank exact word > phrase starting with > phrase containing. |
| `relation.method` (`explicit, list_item, whole_gloss, short_gloss, marker`) | Tamil targets come from splitting meaning text on `;` `,`, which is heuristic and over-splits. Ranking now trusts `explicit` (Wiktionary, WordNet) over `list_item`, and one method can be re-parsed without re-ingesting. |
| `word_form.tags` | Keep Wiktionary's grammatical tags (dative, past, honorific…) on the ~100k inflections. |
| `example` table | Wiktionary usage sentences with translation and romanisation, shown beside the source data. |
| `queries.jsonl` dropped from ingest | Redundant with `mydictionary.jsonl`. |
| `related.sql` weighs by `method` | Verified: the WordNet synonym `shore` now outranks list-split translations for `bank`. |

## Model

```
source ──< entry >── word ──< relation >── word
 (who says)  (what the source  (a node per      (typed, directed edge;
              says, verbatim)   lang+normalised   each one cites its entry)
                                form)
                          word ──< word_form      (inflections / variants → same word)
                          word ── word_fts        (trigram index for fuzzy / substring)
```

* **word** – one node per `(lang, norm)` shared by all sources. `bank` from 10 dictionaries is one node.
* **entry** – one source record, kept verbatim (meaning text, domain note, page, URL, `origin_file:line` back to `data_raw`). This is the "source data" shown with a suggestion.
* **relation** – `src → dst` with a type (`translation, synonym, antonym, hypernym, hyponym, derived, base, related, coordinate`) pointing at the entry that asserts it. Same edge from N entries = N evidence rows = the main ranking signal.
* **source** – one row per dictionary (72 from mydictionary.in + Wiktionary + TVA glossary + TVA Kalanjiyam + WordNet), carrying license, attribution text and a `trust` multiplier.

## Why this shape

| Finding in the data | Consequence |
|---|---|
| OMW 1.4 has **no Tamil** | WordNet can only give en↔en relations. Tamil relatedness comes from the dictionaries. |
| ~350k en→ta dictionary rows, **plus Wiktionary with ~27k explicit Tamil synonym links on 5.7k headwords** (also 11.7k derived, 3.4k related, 204 antonyms) | Wiktionary is the high-precision Tamil↔Tamil source; pivoting through English translations is a lower-confidence supplement that extends coverage. Wiktionary edges should outrank pivot edges. |
| Pivoting through a polysemous word makes false synonyms (`bank` → வங்கி / கரை) | Pivot-only candidates need ≥ 2 distinct pivot words (in `related.sql`). Verified on the test graph. |
| Licenses differ (CC BY-SA ×69, CC0, CC-BY-SA 4.0, one empty, WordNet) | License + attribution live on `source` and are returned with every evidence row. |
| Same term repeated across dictionaries, plus `queries.jsonl` overlapping `mydictionary.jsonl` | `entry.dedupe_key` makes ingest idempotent and collapses dups. |
| Tamil is agglutinative | Wiktionary `forms` carry ~100k usable inflections (case forms, tense, honorific, etc.; skip the 105k `error-unrecognized-form` entries) → `word_form`. This is real morphology data, so most inflected input resolves without a stemmer. `word_fts` covers the rest. |
| TVA Kalanjiyam is OCR-noisy (visible garbage in `note`) | `source.trust < 1` and `entry.quality` push it down in ranking. |

## Raw data → tables

| Raw file | Rows produced |
|---|---|
| `mydictionary/dictionaries.json` | `source` ×72 (name, URL, license, headword count) |
| `mydictionary/mydictionary.jsonl` (361k) | `entry` per row (`gloss`=`meaning`, `context`=`term_note`+`meaning_note`); `meaning` split on `;` `,` into target words → `translation` edges (en→ta, both directions). A `(மறுவ.)` marker in a meaning ("also called") → `synonym` edges between the Tamil terms. |
| `mydictionary/queries.jsonl` (678k rows) | **Not ingested.** Measured: it adds ~150 English and ~150 Tamil words beyond `mydictionary.jsonl`. Use it only as a cross-check / to backfill gaps. |
| `tamil.jsonl` (Wiktionary, 13.9k entries / 11.2k headwords) | `entry` per sense (`pos`, English `gloss`, sense id, `topics`/`categories` → `context`, first example + translation can go in `gloss` extras later). Sense- and entry-level `synonyms, antonyms, derived, related, coordinate_terms, hypernyms, hyponyms` → edges (`derived` also gives the inverse `base`). `form_of`/`alt_of` (1.7k senses) → `word_form(kind='variant')` or `base` edges. `forms` → `word_form`. ~16k of 28.8k English glosses are ≤3 tokens → `translation` edges ta→en; longer glosses stay as definitions only. |
| `tva_glossary.jsonl` (6.4k), `tva_kalanjiyam.jsonl` (1.3k) | `entry` with `source_ref`=`page`; en↔ta `translation` edges. Kalanjiyam gets low trust. |
| `nltk/…/wordnet.zip` | `entry` per synset; `synonym` (lemmas in one synset), `hypernym`/`hyponym` edges, en only. |
| `nllb-ct2/` | Not stored. A translation model, not data. Possible later fallback for words with no dictionary hit; its output would be flagged `machine` and never mixed in silently. |

## Normalisation (`word.norm`)

* Both: Unicode NFC, strip zero-width chars (ZWJ/ZWNJ and the like), collapse whitespace, trim punctuation.
* English: lowercase, NFKC.
* Tamil: NFC only. No stemming in `norm`; that goes in `word_form`.
* Tamil `norm` must be tested against the OCR-noisy rows before ingest.

## Query flow

1. Detect language by script (U+0B80–0BFF → `ta`, else `en`), normalise.
2. Resolve to a word: `word.norm` → `word_form.norm` → `word_fts` (fuzzy; offered as "did you mean").
3. Run `related.sql` with `:word_id`. Each result row has `text, lang, score, relations, n_sources` and an `evidence` JSON array. Each evidence object has the source name, license, URL, page ref, verbatim gloss and domain context, plus the second leg for pivots. That array is the "source data" for the suggestion.

## Deliberately not in this version

* **No sense table / WSD.** Dictionary rows rarely say which sense they mean; the `context` text (domain) is the only sense signal. The polysemy guard is a stopgap. If suggestions for common words are noisy, the next step is clustering a word's entries by domain/gloss into senses.
* **No materialised suggestion table.** The query is computed live. Add a cache only if latency demands it.
* **Storage is SQLite.** It's one file and handles this size (~0.4M entries, low millions of edges). The DDL is portable to Postgres, where FTS5 would become `pg_trgm`.
