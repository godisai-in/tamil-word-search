"""Step 3: shards + words.jsonl -> ONE pickle, ready for later upload.

pickle layout (dict):
  format_version  1
  model           {name, dim, pooling, max_len}
  vector_prep     {centering: 'none'|'lang', per_lang_mean: {...}, l2_normalised: True}
                  -> if centering == 'lang' a search service must apply the SAME centering to query vectors
  index_mapping   the opensearch/words_index.json that matches these docs
  ids             list[str]            document ids (_id)
  docs            list[dict]           lang,text,norm,token_count,headword,collections,dictionaries
  vectors         float16 ndarray (N, dim), row i belongs to ids[i]  (upcast to float32 on upload)

Only unpickle files you produced yourself (pickle can execute code on load).
"""
import argparse
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from embedder import MAX_LEN, MODEL_NAME, finalize  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"

ap = argparse.ArgumentParser()
ap.add_argument("--center", choices=["none", "lang"], default="none",
                help="subtract per-language mean before normalising (default none; see embedder.py)")
ap.add_argument("--sample", action="store_true", help="read out/shards_sample, write out/embeddings_sample.pkl")
args = ap.parse_args()
shard_dir = OUT / ("shards_sample" if args.sample else "shards")
out_path = OUT / ("embeddings_sample.pkl" if args.sample else "embeddings.pkl")

shards = sorted(shard_dir.glob("shard_*.npz"))
assert shards, f"no shards in {shard_dir}"

# pass 1: per-language mean (streaming, so we never hold all raw vectors)
sums, counts, dim = {}, {}, None
for p in shards:
    z = np.load(p)
    dim = z["vecs"].shape[1]
    for lang in ("en", "ta"):
        m = z["langs"] == lang
        if m.any():
            sums[lang] = sums.get(lang, 0) + z["vecs"][m].astype(np.float64).sum(0)
            counts[lang] = counts.get(lang, 0) + int(m.sum())
means = {l: (sums[l] / counts[l]).astype(np.float32) for l in sums}
n_total = sum(counts.values())
print("per-language counts:", counts, flush=True)

# pass 2: centre + normalise + float16 into a preallocated matrix
vectors = np.empty((n_total, dim), dtype=np.float16)
ids = []
langs_all = []
pos = 0
for p in shards:
    z = np.load(p)
    v = finalize(z["vecs"], z["langs"], means if args.center == "lang" else None)
    vectors[pos : pos + len(v)] = v.astype(np.float16)
    ids.extend(z["ids"].tolist())
    langs_all.extend(z["langs"].tolist())
    pos += len(v)
assert pos == n_total and len(set(ids)) == n_total, "duplicate or missing ids"

# docs, in the same order as `ids`
want = set(ids)
by_id = {}
with open(OUT / "words.jsonl", encoding="utf8") as f:
    for line in f:
        d = json.loads(line)
        if d["id"] in want:
            i = d.pop("id")
            by_id[i] = d
docs = [by_id[i] for i in ids]
assert all(d["lang"] == l for d, l in zip(docs, langs_all)), "doc/vector language mismatch"

mapping = json.loads((ROOT / "opensearch/words_index.json").read_text())
assert mapping["mappings"]["properties"]["embedding"]["dimension"] == dim, "mapping dimension != model dimension"

payload = {
    "format_version": 1,
    "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "model": {"name": MODEL_NAME, "dim": dim, "pooling": "mean, special tokens excluded", "max_len": MAX_LEN},
    "vector_prep": {"centering": args.center, "per_lang_mean": means, "l2_normalised": True},
    # per_lang_mean is always stored; it is APPLIED to `vectors` only if centering == "lang"
    "index_mapping": mapping,
    "ids": ids,
    "docs": docs,
    "vectors": vectors,
}
tmp = out_path.with_suffix(".tmp")
with open(tmp, "wb") as f:
    pickle.dump(payload, f, protocol=4)
tmp.rename(out_path)
print(f"wrote {out_path}  ({out_path.stat().st_size / 1e9:.2f} GB, {n_total:,} words, dim {dim}, float16)")
