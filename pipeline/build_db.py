"""Build out/words.db (SQLite, schema/schema.sql) from every raw source.

  python build_db.py          # ~ a few minutes, writes out/words.db + out/words_db_report.json

Sources -> tables
  mydictionary.jsonl   : entry per row; meaning split into target words -> translation / synonym / related
  tva_*.jsonl          : same, with page refs
  tamil.jsonl (Wikt.)  : entry per sense; explicit synonym/antonym/derived/related/... edges, short-gloss
                         translations, inflections -> word_form, examples
  WordNet (nltk zip)   : entry per (synset, lemma); synonym/antonym/hypernym/hyponym edges (English only)
queries.jsonl is not used (adds ~150 words/language; see schema/README.md).
"""
import hashlib
import json
import re
import sqlite3
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))
from textnorm import lang_of, norm, split_meaning, usable, word_id as doc_id_of  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data_raw"
OUT = ROOT / "out"
DB = OUT / "words.db"
t_start = time.time()

if DB.exists():
    DB.unlink()
db = sqlite3.connect(DB)
db.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=MEMORY; PRAGMA cache_size=-400000;")
db.executescript((ROOT / "schema/schema.sql").read_text())
db.execute("PRAGMA foreign_keys=OFF")  # ids are assigned here, in order; checked at the end

# ----------------------------------------------------------------------------- in-memory state
word_ids = {}            # (lang, norm) -> id
w_text, w_lang, w_norm = [None], [None], [None]    # index = id
forms = {}               # (word_id, form_norm) -> (form, kind, tags)
sources = {}             # code -> id
source_rows = []
entry_keys = set()
entries, relations, examples = [], [], []
next_entry = [1]
stats = Counter()
dict_pairs = {}          # source code -> Counter of (lang term, lang meaning), to classify the dictionary


def get_word(raw, headword):
    n = norm(raw)
    lang = lang_of(n)
    if lang is None or not usable(n, headword):
        return None
    k = (lang, n)
    i = word_ids.get(k)
    if i is None:
        i = len(w_text)
        word_ids[k] = i
        w_text.append(raw.strip())
        w_lang.append(lang)
        w_norm.append(n)
    elif raw.strip() == n and w_text[i] != n:   # prefer the plain lowercase spelling for display
        w_text[i] = n
    return i


def add_source(code, collection, name, kind, trust, url=None, name_ta=None, year=None, license=None, attribution=None):
    if code in sources:
        return sources[code]
    sid = len(sources) + 1
    sources[code] = sid
    source_rows.append((sid, code, collection, name, name_ta, kind, url, year, license, attribution, trust))
    return sid


def add_entry(source_id, word, pos, gloss, gloss_lang, context, ref, url, line, quality=1.0, key_extra=""):
    key = hashlib.sha1(f"{source_id}|{word}|{gloss}|{key_extra}".encode()).hexdigest()[:24]
    if key in entry_keys:
        stats["entry_dup_skipped"] += 1
        return None
    entry_keys.add(key)
    eid = next_entry[0]
    next_entry[0] += 1
    entries.append((eid, source_id, word, pos, gloss, gloss_lang, context, ref, url, None, line, quality, key))
    if len(entries) >= 100000:
        flush()
    return eid


def rel(a, b, kind, entry_id, method, weight=1.0):
    if a != b:
        relations.append((a, b, kind, entry_id, method, weight))
        if len(relations) >= 250000:
            flush()


def pair(a, b, kind, inverse, entry_id, method, weight=1.0, entry_back=None):
    rel(a, b, kind, entry_id, method, weight)
    rel(b, a, inverse, entry_back or entry_id, method, weight)


def flush():
    db.executemany("INSERT INTO entry(id,source_id,word_id,pos,gloss,gloss_lang,context,source_ref,source_url,origin_file,origin_line,quality,dedupe_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", entries)
    db.executemany("INSERT OR IGNORE INTO relation(src_word_id,dst_word_id,rel,entry_id,method,weight) VALUES (?,?,?,?,?,?)", relations)
    db.executemany("INSERT INTO example(entry_id,text,lang,translation,romanized) VALUES (?,?,?,?,?)", examples)
    stats["entries"] += len(entries)
    stats["relations_attempted"] += len(relations)
    entries.clear(); relations.clear(); examples.clear()


# origin_file is constant per source: patch in after the fact (keeps tuples small)
ORIGIN = {}


def targets_of(meaning):
    """[(raw_piece, from_marker)] — items after the '(மறுவ.)' ('also called') marker are flagged."""
    if "(மறுவ.)" in meaning:
        before, after = meaning.split("(மறுவ.)", 1)
        return [(p, False) for p in split_meaning(before)] + [(p, True) for p in split_meaning(after)]
    return [(p, False) for p in split_meaning(meaning)]


def ingest_row(src_id, term, meaning, ctx, ref, url, line, quality=1.0):
    """One dictionary-style row: term -> meaning (list of target words)."""
    t = get_word(term, True)
    if t is None:
        stats["rows_skipped_bad_term"] += 1
        return None, None
    gloss = meaning.strip()
    gl = "ta" if lang_of(gloss) == "ta" else "en"
    eid = add_entry(src_id, t, None, gloss, gl, ctx, ref, url, line, quality)
    if eid is None:
        return t, None
    pieces = targets_of(gloss)
    for i, (raw, marker) in enumerate(pieces):
        d = get_word(raw, False)
        if d is None or d == t:
            continue
        same = w_lang[t] == w_lang[d]
        if not same:
            kind = "translation"
        else:
            kind = "synonym" if marker else "related"
        method = "marker" if marker else ("whole_gloss" if len(pieces) == 1 else "list_item")
        w = 0.8 if marker else max(0.5, 1.0 - 0.1 * i)
        pair(t, d, kind, kind, eid, method, w)
        stats[f"rel_{kind}"] += 2
    return t, eid


# ----------------------------------------------------------------------------- mydictionary
print("mydictionary ...", flush=True)
ORIGIN["mydictionary"] = "data_raw/mydictionary/mydictionary.jsonl"
with open(RAW / "mydictionary/mydictionary.jsonl", encoding="utf8") as f:
    for ln, line in enumerate(f, 1):
        d = json.loads(line)
        name = d["dictionary_en"] or d["dictionary"]
        code = "mydictionary/" + name
        m = re.search(r"\((\d{4})\)", name)
        sid = add_source(code, "mydictionary", name, "bilingual_dictionary", 1.0, d["dictionary_url"],
                         d["dictionary"], int(m.group(1)) if m else None, d.get("license") or None,
                         "mydictionary.in; licensed " + (d.get("license") or "see source"))
        ctx = " · ".join(x for x in (d["term_note"], d["meaning_note"]) if x)[:300] or None
        t, _ = ingest_row(sid, d["term"], d["meaning"], ctx, None, d["dictionary_url"], ln)
        if t is not None:
            dict_pairs.setdefault(code, Counter())[(w_lang[t], "ta" if lang_of(d["meaning"]) == "ta" else "en")] += 1

# ----------------------------------------------------------------------------- TVA
for fn, code, name, trust in (
    ("tva_glossary.jsonl", "tva/glossary", "TVA Science Glossary", 0.9),
    ("tva_kalanjiyam.jsonl", "tva/kalanjiyam", "TVA Kalanjiyam (OCR)", 0.5),
):
    print(fn, "...", flush=True)
    sid = add_source(code, "tva" if "glossary" in code else "tva", name, "glossary", trust,
                     "https://www.tamilvu.org/", None, None, None, "Tamil Virtual Academy; OCR text, may contain errors")
    with open(RAW / fn, encoding="utf8") as f:
        for ln, line in enumerate(f, 1):
            d = json.loads(line)
            ingest_row(sid, d["en"], d["ta"], None, f"p. {d.get('page')}" if d.get("page") else None, None, ln, trust)

# ----------------------------------------------------------------------------- Wiktionary
print("wiktionary ...", flush=True)
wsid = add_source("wiktionary/ta", "wiktionary", "English Wiktionary (Tamil entries)", "monolingual_dictionary", 1.0,
                  "https://en.wiktionary.org/", None, None, "CC BY-SA 4.0",
                  "Wiktionary contributors, CC BY-SA 4.0; data via kaikki.org")
REL_MAP = {"synonyms": ("synonym", "synonym"), "antonyms": ("antonym", "antonym"), "related": ("related", "related"),
           "coordinate_terms": ("coordinate", "coordinate"), "derived": ("derived", "base"),
           "hypernyms": ("hypernym", "hyponym"), "hyponyms": ("hyponym", "hypernym")}
SKIP_FORM_TAGS = {"table-tags", "inflection-template", "class", "error-unrecognized-form", "no-table-tags"}
BAD_GLOSS = re.compile(r"^(alternative|plural|inflection|genitive|dative|first|second|third|past|present|future|"
                       r"misspelling|obsolete|archaic|eye dialect|abbreviation|initialism|romanization|"
                       r"nominative|accusative|alternative form)\b", re.I)
with open(RAW / "tamil.jsonl", encoding="utf8") as f:
    for ln, line in enumerate(f, 1):
        d = json.loads(line)
        head = get_word(d["word"], True)
        if head is None:
            continue
        url = "https://en.wiktionary.org/wiki/" + quote(d["word"]) + "#Tamil"
        first_entry = None
        for s in d["senses"]:
            glosses = s.get("glosses") or []
            gloss = "; ".join(glosses)
            if not gloss:
                continue
            ctx = ", ".join((s.get("topics") or []) + (s.get("tags") or [])[:4]) or None
            eid = add_entry(wsid, head, d.get("pos"), gloss, "en", ctx, s.get("id"), url, ln, 1.0, s.get("id") or "")
            if eid is None:
                continue
            first_entry = first_entry or eid
            for ex in (s.get("examples") or [])[:2]:
                if ex.get("text"):
                    examples.append((eid, ex["text"], "ta", ex.get("english") or ex.get("translation"), ex.get("roman")))
            for k, (fwd, inv) in REL_MAP.items():
                for x in s.get(k) or []:
                    o = get_word(x.get("word", ""), True)
                    if o is not None:
                        pair(head, o, fwd, inv, eid, "explicit")
                        stats[f"wikt_{k}"] += 1
            g0 = re.sub(r"\([^)]*\)", "", glosses[0]).strip()
            if g0 and len(g0.split()) <= 3 and not BAD_GLOSS.match(g0):
                en = get_word(g0, False)
                if en is not None and w_lang[en] == "en":
                    pair(head, en, "translation", "translation", eid, "short_gloss", 0.9)
                    stats["wikt_short_gloss"] += 1
            for key in ("form_of", "alt_of"):
                for x in s.get(key) or []:
                    o = get_word(x.get("word", ""), True)
                    if o is not None and o != head:
                        forms.setdefault((o, norm(d["word"])), (d["word"], "variant", key))
        if first_entry is not None:           # entry-level relations hang on the first sense
            for k, (fwd, inv) in REL_MAP.items():
                for x in d.get(k) or []:
                    o = get_word(x.get("word", ""), True)
                    if o is not None:
                        pair(head, o, fwd, inv, first_entry, "explicit")
                        stats[f"wikt_{k}"] += 1
        for fm in d.get("forms") or []:
            tags = set(fm.get("tags") or [])
            form = fm.get("form") or ""
            if tags & SKIP_FORM_TAGS or not form or form == "-" or "no-table" in form:
                continue
            kind = "romanization" if "romanization" in tags else ("plural" if "plural" in tags else "inflection")
            if kind != "romanization" and lang_of(form) != "ta":
                continue
            n = norm(form)
            if n and n != w_norm[head] and (head, n) not in forms:
                forms[(head, n)] = (form, kind, " ".join(sorted(tags - {"romanization"})) or None)
                stats["forms"] += 1

# ----------------------------------------------------------------------------- WordNet
print("wordnet ...", flush=True)
wn_id = add_source("wordnet/3.0", "wordnet", "Princeton WordNet 3.0", "wordnet", 1.0, "https://wordnet.princeton.edu/",
                   None, 2006, "WordNet License", "Princeton University WordNet 3.0 (BSD-like license)")
zf = zipfile.ZipFile(RAW / "nltk/corpora/wordnet.zip")
POS = {"n": "noun", "v": "verb", "a": "adj", "s": "adj", "r": "adv"}
syn = {}   # (pos_file, offset) -> list of (word_id, entry_id)
raw_ptrs = []
for pf in ("noun", "verb", "adj", "adv"):
    for line in zf.read(f"wordnet/data.{pf}").decode("utf8").splitlines():
        if line.startswith("  "):
            continue
        head, _, gloss = line.partition(" | ")
        tok = head.split()
        offset, ss_type, wcnt = tok[0], tok[2], int(tok[3], 16)
        lemmas = []
        for lem in tok[4: 4 + 2 * wcnt: 2]:
            lem = re.sub(r"\(\w+\)$", "", lem).replace("_", " ")
            wid = get_word(lem, True)
            if wid is not None:
                lemmas.append(wid)
        idx = 4 + 2 * wcnt
        pcnt = int(tok[idx])
        ptrs = [tok[idx + 1 + 4 * j: idx + 5 + 4 * j] for j in range(pcnt)]
        members = []
        gloss = gloss.split(";")[0].strip()
        for pos_i, wid in enumerate(lemmas):
            eid = add_entry(wn_id, wid, POS[ss_type], gloss, "en", None, f"{offset}-{ss_type}", None, None, 1.0, offset + ss_type)
            if eid is not None:
                members.append((wid, eid))
        syn[(offset, ss_type if ss_type != "s" else "a")] = members
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                pair(members[a][0], members[b][0], "synonym", "synonym", members[a][1], "explicit", 1.0, members[b][1])
                stats["wn_synonym"] += 2
        raw_ptrs.append((offset, ss_type, members, ptrs))
for offset, ss_type, members, ptrs in raw_ptrs:
    for sym, off2, p2, st in ptrs:
        tgt = syn.get((off2, p2))
        if not tgt or not members:
            continue
        if sym in ("@", "@i"):
            kind = "hypernym"
        elif sym in ("~", "~i"):
            kind = "hyponym"
        elif sym == "!":
            si, ti = int(st[:2], 16), int(st[2:], 16)
            if si and ti and si <= len(members) and ti <= len(tgt):
                rel(members[si - 1][0], tgt[ti - 1][0], "antonym", members[si - 1][1], "explicit")
                stats["wn_antonym"] += 1
            continue
        else:
            continue
        for a_w, a_e in members[:3]:
            for b_w, _ in tgt[:3]:
                rel(a_w, b_w, kind, a_e, "explicit")
                stats[f"wn_{kind}"] += 1
flush()

# ----------------------------------------------------------------------------- finish
print("writing words / sources / forms ...", flush=True)
for code, c in dict_pairs.items():       # classify each mydictionary book by what its rows look like
    tot = sum(c.values())
    if tot and c[("ta", "ta")] / tot > 0.5:
        db.execute("UPDATE source SET kind='monolingual_dictionary' WHERE id=?", (sources[code],))
db.executemany("INSERT INTO source(id,code,collection,name,name_ta,kind,url,year,license,attribution,trust) VALUES (?,?,?,?,?,?,?,?,?,?,?)", source_rows)
db.executemany("INSERT INTO word(id,lang,text,norm,doc_id,token_count) VALUES (?,?,?,?,?,?)",
               ((i, w_lang[i], w_text[i], w_norm[i], doc_id_of(w_lang[i], w_norm[i]), len(w_norm[i].split()))
                for i in range(1, len(w_text))))
db.executemany("INSERT OR IGNORE INTO word_form(word_id,form,norm,kind,tags) VALUES (?,?,?,?,?)",
               ((w, f, n, k, t) for (w, n), (f, k, t) in forms.items()))
db.execute("UPDATE word SET entry_count=(SELECT COUNT(*) FROM entry WHERE entry.word_id=word.id)")
for code, path in ORIGIN.items():
    db.execute("UPDATE entry SET origin_file=? WHERE source_id IN (SELECT id FROM source WHERE collection=?)", (path, code))
db.execute("UPDATE entry SET origin_file='data_raw/tamil.jsonl' WHERE source_id=?", (wsid,))
db.commit()
print("full-text indexes ...", flush=True)
db.execute("INSERT INTO word_fts(rowid,norm) SELECT id,norm FROM word")
db.execute("INSERT INTO word_token_fts(rowid,norm) SELECT id,norm FROM word")
db.commit()
fk = db.execute("PRAGMA foreign_key_check").fetchall()
db.execute("ANALYZE")
db.commit()

n = lambda t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
report = {
    "seconds": round(time.time() - t_start), "db_mb": round(DB.stat().st_size / 1e6),
    "tables": {t: n(t) for t in ("source", "word", "word_form", "entry", "relation", "example")},
    "words_by_lang": dict(db.execute("SELECT lang,COUNT(*) FROM word GROUP BY lang")),
    "relations_by_type": dict(db.execute("SELECT rel,COUNT(*) FROM relation GROUP BY rel")),
    "relations_by_method": dict(db.execute("SELECT method,COUNT(*) FROM relation GROUP BY method")),
    "foreign_key_violations": len(fk), "counters": dict(stats),
}
(OUT / "words_db_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
print(json.dumps(report, indent=1, ensure_ascii=False))
