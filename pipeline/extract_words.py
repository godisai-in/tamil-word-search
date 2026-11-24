"""Step 1: collect every distinct (lang, norm) word from all raw sources.
Writes out/words.jsonl (one JSON object per word) + out/words_report.json.
queries.jsonl is deliberately skipped (adds ~150 words per language on top of
mydictionary.jsonl; see schema/README.md)."""
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from textnorm import lang_of, norm, split_meaning, usable, word_id  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data_raw"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

COLLECTIONS = ["mydictionary", "wiktionary", "tva_glossary", "tva_kalanjiyam", "wordnet"]
CBIT = {c: 1 << i for i, c in enumerate(COLLECTIONS)}

words = {}            # (lang, norm) -> [display_text, headword(bool), coll_mask, dict_mask]
dict_names = []       # index -> mydictionary dictionary_en
dict_idx = {}
dropped = Counter()


def add(raw, headword, coll, dmask=0):
    n = norm(raw)
    lang = lang_of(n)
    if lang is None or not usable(n, headword):
        dropped["unusable"] += 1
        return
    k = (lang, n)
    w = words.get(k)
    if w is None:
        words[k] = [raw.strip(), headword, CBIT[coll], dmask]
    else:
        w[1] = w[1] or headword
        w[2] |= CBIT[coll]
        w[3] |= dmask


def add_row(term, meaning, coll, dmask):
    add(term, True, coll, dmask)
    for piece in split_meaning(meaning):
        add(piece, False, coll, dmask)


# --- mydictionary (72 dictionaries) ---------------------------------------
with open(RAW / "mydictionary/mydictionary.jsonl", encoding="utf8") as f:
    for line in f:
        d = json.loads(line)
        name = d["dictionary_en"] or d["dictionary"]
        if name not in dict_idx:
            dict_idx[name] = len(dict_names)
            dict_names.append(name)
        add_row(d["term"], d["meaning"], "mydictionary", 1 << dict_idx[name])

# --- TVA ------------------------------------------------------------------
for fn, coll in (("tva_glossary.jsonl", "tva_glossary"), ("tva_kalanjiyam.jsonl", "tva_kalanjiyam")):
    with open(RAW / fn, encoding="utf8") as f:
        for line in f:
            d = json.loads(line)
            add_row(d["en"], d["ta"], coll, 0)

# --- Wiktionary (Tamil) ---------------------------------------------------
REL = ("synonyms", "antonyms", "derived", "related", "coordinate_terms", "hypernyms", "hyponyms")
with open(RAW / "tamil.jsonl", encoding="utf8") as f:
    for line in f:
        d = json.loads(line)
        add(d["word"], True, "wiktionary")
        for holder in [d] + d["senses"]:
            for k in REL:
                for x in holder.get(k) or []:
                    if x.get("word"):
                        add(x["word"], False, "wiktionary")

# --- WordNet (English lemmas) ---------------------------------------------
zf = zipfile.ZipFile(RAW / "nltk/corpora/wordnet.zip")
for pos in ("noun", "verb", "adj", "adv"):
    for line in zf.read(f"wordnet/index.{pos}").decode().splitlines():
        if not line.startswith(" "):
            add(line.split()[0].replace("_", " "), True, "wordnet")

# --- write ----------------------------------------------------------------
stats = Counter()
with open(OUT / "words.jsonl", "w", encoding="utf8") as out:
    for (lang, n), (text, hw, cm, dm) in sorted(words.items()):
        tc = len(n.split())
        rec = {
            "id": word_id(lang, n),
            "lang": lang,
            "text": text,
            "norm": n,
            "token_count": tc,
            "headword": hw,
            "collections": [c for c in COLLECTIONS if cm & CBIT[c]],
            "dictionaries": [dict_names[i] for i in range(len(dict_names)) if dm >> i & 1],
        }
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        stats[(lang, "phrase" if tc > 1 else "word")] += 1
        stats[(lang, "headword" if hw else "target_only")] += 1

report = {
    "total": len(words),
    "by_lang": {l: sum(v for (ll, _), v in stats.items() if ll == l and _ in ("word", "phrase")) for l in ("en", "ta")},
    "breakdown": {f"{l}.{k}": v for (l, k), v in sorted(stats.items())},
    "dropped_unusable": dropped["unusable"],
    "mydictionary_dictionaries": len(dict_names),
}
(OUT / "words_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
print(json.dumps(report, indent=1, ensure_ascii=False))
