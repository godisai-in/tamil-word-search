"""Step 5: pkl + words.db -> per-language float16 .npy files the web app can map in.

  out/vec_en.npy / vec_ta.npy         float16 (n, 768), L2-normalised, rows grouped by language
  out/vec_en_ids.npy / vec_ta_ids.npy int32  words.db word.id for each row
  out/vectors_meta.json               model, dim, centering, coverage

Words in the DB with no vector (e.g. English lemmas only seen as a short Wiktionary gloss) simply
get no "similar by meaning" section.
"""
import json
import pickle
import sqlite3
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"

with open(OUT / "embeddings.pkl", "rb") as f:     # our own file
    P = pickle.load(f)
ids, V = P["ids"], P["vectors"]
db = sqlite3.connect(OUT / "words.db")
by_doc = {d: (i, l) for d, i, l in db.execute("SELECT doc_id,id,lang FROM word")}
meta = {"model": P["model"], "vector_prep": {k: v for k, v in P["vector_prep"].items() if k != "per_lang_mean"}, "langs": {}}
for lang in ("en", "ta"):
    rows, wids = [], []
    for r, d in enumerate(ids):
        hit = by_doc.get(d)
        if hit and hit[1] == lang:
            rows.append(r)
            wids.append(hit[0])
    order = np.argsort(wids)
    rows = np.array(rows)[order]
    wids = np.array(wids, dtype=np.int32)[order]
    np.save(OUT / f"vec_{lang}.npy", V[rows])
    np.save(OUT / f"vec_{lang}_ids.npy", wids)
    n_db = db.execute("SELECT COUNT(*) FROM word WHERE lang=?", (lang,)).fetchone()[0]
    meta["langs"][lang] = {"vectors": int(len(wids)), "db_words": n_db, "coverage": round(len(wids) / n_db, 4)}
    print(lang, meta["langs"][lang])
(OUT / "vectors_meta.json").write_text(json.dumps(meta, indent=1))
