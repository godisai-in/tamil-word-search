"""Step 2: embed every word in out/words.jsonl -> out/shards/shard_XXXXX.npz
(raw mean-pooled vectors + ids + langs). Resumable: finished shards are skipped,
and each shard is written atomically, so Ctrl-C / reboot never corrupts progress.

  python embed_words.py --limit 3000        # evenly spaced sample -> out/shards_sample/
  python embed_words.py                     # full run             -> out/shards/
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from embedder import MODEL_NAME, Embedder  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"

ap = argparse.ArgumentParser()
ap.add_argument("--model", default=MODEL_NAME)
ap.add_argument("--batch", type=int, default=128)
ap.add_argument("--shard-size", type=int, default=20000)
ap.add_argument("--threads", type=int, default=os.cpu_count())
ap.add_argument("--limit", type=int, help="embed an evenly spaced sample of N words (benchmark / smoke test)")
args = ap.parse_args()

words = []
with open(OUT / "words.jsonl", encoding="utf8") as f:
    for line in f:
        d = json.loads(line)
        words.append((d["id"], d["lang"], d["norm"]))

# Sort by length so each batch has near-equal token counts (little padding = fast).
# (len, id) makes the order -- and therefore the shard boundaries -- deterministic.
words.sort(key=lambda w: (len(w[2]), w[0]))
if args.limit:
    step = max(1, len(words) // args.limit)
    words = words[::step][: args.limit]
shard_dir = OUT / ("shards_sample" if args.limit else "shards")
shard_dir.mkdir(exist_ok=True)

n_shards = (len(words) + args.shard_size - 1) // args.shard_size
emb = Embedder(args.model, threads=args.threads)
print(f"{len(words):,} words, {n_shards} shards, dim={emb.dim}, threads={args.threads}", flush=True)

done_words = 0
t0 = time.time()
for s in range(n_shards):
    path = shard_dir / f"shard_{s:05d}.npz"
    chunk = words[s * args.shard_size : (s + 1) * args.shard_size]
    if path.exists():
        done_words += len(chunk)
        continue
    vecs = []
    for i in range(0, len(chunk), args.batch):
        vecs.append(emb.embed_raw([w[2] for w in chunk[i : i + args.batch]]))
    tmp = path.with_suffix(".tmp.npz")
    np.savez(
        tmp,
        ids=np.array([w[0] for w in chunk]),
        langs=np.array([w[1] for w in chunk]),
        vecs=np.vstack(vecs),
    )
    tmp.rename(path)
    done_words += len(chunk)
    el = time.time() - t0
    rate = (done_words - 0) / el if el else 0
    print(f"shard {s + 1}/{n_shards}  {done_words:,}/{len(words):,}  {rate:,.0f} words/s  elapsed {el / 60:.1f} min", flush=True)
print("done", flush=True)
