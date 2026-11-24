-- Word Search core engine: data schema v2.1 (SQLite >= 3.34, FTS5 with trigram tokenizer)
-- v2 changes vs v1 are listed in README.md ("Changes in v2").
--
-- Model in one line:
--   WORD nodes (en | ta)  --RELATION edges-->  WORD nodes,
--   every edge points at the ENTRY (a source record) that asserts it,
--   every entry belongs to a SOURCE (dictionary / glossary / wordnet ...).
-- So any suggestion can always be explained as "X is related to Y because
-- <source> says so (page / URL / original text)".

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- SOURCE: one row per dictionary / glossary / corpus we ingest.
-- mydictionary.in alone contributes 72 rows (from dictionaries.json).
-- ---------------------------------------------------------------------------
CREATE TABLE source (
    id             INTEGER PRIMARY KEY,
    code           TEXT NOT NULL UNIQUE,   -- stable slug, e.g. 'mydictionary/technical-terms-2002'
    collection     TEXT NOT NULL CHECK (collection IN
                     ('mydictionary','wiktionary','tva','wordnet')),
    name           TEXT NOT NULL,          -- display name, English
    name_ta        TEXT,                   -- display name, Tamil (as published)
    kind           TEXT NOT NULL CHECK (kind IN
                     ('bilingual_dictionary','monolingual_dictionary',
                      'glossary','thesaurus','wordnet')),
    url            TEXT,                   -- landing page of the source
    year           INTEGER,
    license        TEXT,                   -- 'CC BY-SA', 'CC0', 'WordNet', ...
    attribution    TEXT,                   -- text we must show when license requires it
    trust          REAL NOT NULL DEFAULT 1.0 CHECK (trust BETWEEN 0 AND 1),
                                           -- ranking multiplier; e.g. OCR-noisy
                                           -- tva_kalanjiyam gets < 1
    headword_count INTEGER
);

-- ---------------------------------------------------------------------------
-- WORD: one node per (language, normalised form), shared by all sources.
-- 'bank' from 10 dictionaries is ONE word row with 10+ entries/edges.
-- Part-of-speech is NOT here (most dictionary rows have none); it lives on entry.
-- ---------------------------------------------------------------------------
CREATE TABLE word (
    id          INTEGER PRIMARY KEY,
    lang        TEXT NOT NULL CHECK (lang IN ('en','ta')),
    text        TEXT NOT NULL,             -- display form (NFC, original casing/script)
    norm        TEXT NOT NULL,             -- lookup key, see README "Normalisation"
    doc_id      TEXT NOT NULL UNIQUE,      -- sha1(lang 0x1f norm)[:20]: the OpenSearch _id and the key into
                                           -- the embeddings pkl, so SQLite words and vectors always join
    entry_count INTEGER NOT NULL DEFAULT 0,-- number of source entries with this word as headword; 0 = only ever
                                           -- seen as a translation target. Ranks real headwords first.
    token_count INTEGER NOT NULL DEFAULT 1,-- 1 = single word, >1 = phrase / multi-word term.
                                           -- ~2/3 of our ~750k words are phrases ("bank draft"),
                                           -- so search ranks: exact > phrase starting with > phrase containing.
    UNIQUE (lang, norm)
);
CREATE INDEX word_lang_tokens ON word(lang, token_count);

-- Inflected / variant surface forms that should resolve to the same word
-- (Wiktionary `forms`, spelling variants, romanisation). Needed because Tamil
-- is agglutinative: users will type வங்கியில் and expect வங்கி.
-- Wiktionary supplies ~100k real inflections (skip its 'error-unrecognized-form' rows).
CREATE TABLE word_form (
    word_id INTEGER NOT NULL REFERENCES word(id) ON DELETE CASCADE,
    form    TEXT NOT NULL,
    norm    TEXT NOT NULL,
    kind    TEXT NOT NULL CHECK (kind IN
              ('inflection','variant','romanization','plural','stem')),
    tags    TEXT,                           -- Wiktionary form tags, e.g. 'dative singular', 'past first-person'
    PRIMARY KEY (word_id, norm)
);
CREATE INDEX word_form_norm ON word_form(norm);

-- Substring / typo-tolerant lookup (works for Tamil too; no word segmentation needed).
CREATE VIRTUAL TABLE word_fts USING fts5(
    norm, content='word', content_rowid='id', tokenize='trigram'
);

-- Whole-token / token-prefix lookup, for finding a word INSIDE a phrase
-- ('draft' -> 'bank draft'; 'வரைவோ*' -> 'வங்கி வரைவோலை').
-- The default unicode61 tokenizer splits Tamil words at vowel signs and the
-- virama (verified: 'வங்கி' became 'வங','கி'), so every Tamil combining mark
-- (Mn/Mc in U+0B80-0BFF) is declared a token character.
CREATE VIRTUAL TABLE word_token_fts USING fts5(
    norm, content='word', content_rowid='id',
    tokenize="unicode61 remove_diacritics 0 tokenchars 'ஂாிீுூெேைொோௌ்ௗ'"
);

-- ---------------------------------------------------------------------------
-- ENTRY: one source record, at sense granularity. This is the "source data"
-- shown to the user. Kept verbatim; never rewritten by the pipeline.
--   mydictionary / tva : one row per (term, meaning) line
--   wiktionary         : one row per sense
--   wordnet            : one row per synset
-- ---------------------------------------------------------------------------
CREATE TABLE entry (
    id           INTEGER PRIMARY KEY,
    source_id    INTEGER NOT NULL REFERENCES source(id),
    word_id      INTEGER NOT NULL REFERENCES word(id),   -- the headword
    pos          TEXT,                     -- noun|verb|adj|adv|pron|num|name|... (NULL if unknown)
    gloss        TEXT,                     -- meaning / definition, verbatim
    gloss_lang   TEXT CHECK (gloss_lang IN ('en','ta')),
    context      TEXT,                     -- domain/usage: term_note, meaning_note, e.g. 'banking'
    source_ref   TEXT,                     -- page no., wiktionary sense id, synset offset
    source_url   TEXT,                     -- deep link if one exists
    origin_file  TEXT,                     -- data_raw/... file this row came from
    origin_line  INTEGER,                  -- line number (jsonl) -> re-trace to raw data
    quality      REAL NOT NULL DEFAULT 1.0 CHECK (quality BETWEEN 0 AND 1),
    dedupe_key   TEXT NOT NULL UNIQUE      -- hash(source, headword norm, gloss): idempotent re-ingest;
                                           -- also removes the ~14 exact dups and the overlap between
                                           -- mydictionary.jsonl and queries.jsonl
);
CREATE INDEX entry_word   ON entry(word_id);
CREATE INDEX entry_source ON entry(source_id);

-- ---------------------------------------------------------------------------
-- RELATION: directed edge between two words, asserted by one entry.
-- Symmetric types (translation, synonym, antonym, related, coordinate) are
-- stored in BOTH directions at ingest so every lookup is a single indexed
-- scan on src_word_id. hypernym<->hyponym and derived<->base are stored as
-- inverse pairs.
-- The same (src,dst,rel) asserted by N entries = N rows = N pieces of
-- evidence; that count is the main ranking signal.
-- ---------------------------------------------------------------------------
CREATE TABLE relation (
    id          INTEGER PRIMARY KEY,
    src_word_id INTEGER NOT NULL REFERENCES word(id),
    dst_word_id INTEGER NOT NULL REFERENCES word(id),
    rel         TEXT NOT NULL CHECK (rel IN
                  ('translation','synonym','antonym','hypernym','hyponym',
                   'derived','base','related','coordinate')),
    entry_id    INTEGER NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
    method      TEXT NOT NULL CHECK (method IN
                  ('explicit',     -- source states the relation (Wiktionary synonyms, WordNet synsets/pointers)
                   'list_item',    -- one item of a split meaning list: 'கரை; அணைகரை' -> 2 edges
                   'whole_gloss',  -- the entire meaning text is one word/phrase
                   'short_gloss',  -- Wiktionary English gloss <= 3 tokens used as a translation
                   'marker')),     -- parsed from a marker such as (மறுவ.) 'also called'
                                   -- Splitting meanings on ';' ',' is heuristic and also cuts descriptive
                                   -- text, so method lets ranking trust 'explicit' over 'list_item'
                                   -- and lets us re-parse one method without re-ingesting everything.
    weight      REAL NOT NULL DEFAULT 1.0,  -- per-edge confidence set by parser, e.g. lower
                                            -- for the 2nd..nth word of a long meaning list
    CHECK (src_word_id <> dst_word_id),
    UNIQUE (src_word_id, dst_word_id, rel, entry_id)
);
CREATE INDEX relation_src ON relation(src_word_id, rel);
CREATE INDEX relation_dst ON relation(dst_word_id, rel);
CREATE INDEX relation_entry ON relation(entry_id);

-- ---------------------------------------------------------------------------
-- EXAMPLE: usage sentences attached to an entry (Wiktionary: 1.4k senses have
-- them, with English translation and romanisation). Shown with the source data.
-- ---------------------------------------------------------------------------
CREATE TABLE example (
    id          INTEGER PRIMARY KEY,
    entry_id    INTEGER NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    lang        TEXT NOT NULL CHECK (lang IN ('en','ta')),
    translation TEXT,
    romanized   TEXT
);
CREATE INDEX example_entry ON example(entry_id);
