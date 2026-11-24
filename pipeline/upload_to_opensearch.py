"""Step 4 (later): upload out/embeddings.pkl to OpenSearch.

  export OPENSEARCH_USER=admin OPENSEARCH_PASSWORD=...   # read from env, never from argv
  python upload_to_opensearch.py --host https://localhost:9200 --index words_v1 --alias words --create
  python upload_to_opensearch.py --dry-run                # no network; shows the first action

Idempotent: _id is the deterministic word id, so re-running overwrites, never duplicates.
--alias words : after a successful load, atomically point alias `words` at --index. The web app queries the
alias, so a new model/data version is loaded into words_v2 and switched over with no downtime
(alias name must not equal an existing concrete index name).
"""
import argparse
import os
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ap = argparse.ArgumentParser()
ap.add_argument("--pkl", default=str(ROOT / "out/embeddings.pkl"))
ap.add_argument("--host", default=os.environ.get("OPENSEARCH_URL", "https://localhost:9200"))
ap.add_argument("--index", default="words_v1")
ap.add_argument("--alias", help="alias to point at --index once the load is verified")
ap.add_argument("--create", action="store_true", help="create the index from the pkl's mapping if missing")
ap.add_argument("--recreate", action="store_true", help="DELETE the index first, then create it")
ap.add_argument("--no-verify-certs", action="store_true", help="for a local self-signed dev cluster only")
ap.add_argument("--batch", type=int, default=500)
ap.add_argument("--dry-run", action="store_true")
args = ap.parse_args()

with open(args.pkl, "rb") as f:          # only ever load a pkl you built yourself
    P = pickle.load(f)
ids, docs, vectors = P["ids"], P["docs"], P["vectors"]
dim = P["model"]["dim"]
assert len(ids) == len(docs) == len(vectors)
assert P["index_mapping"]["mappings"]["properties"]["embedding"]["dimension"] == dim


def actions():
    for i, d, v in zip(ids, docs, vectors):
        yield {"_op_type": "index", "_index": args.index, "_id": i,
               "_source": {**d, "embedding": v.astype("float32").tolist()}}


if args.dry_run:
    a = next(actions())
    a["_source"]["embedding"] = a["_source"]["embedding"][:4] + ["..."]
    print(f"{len(ids):,} docs, dim {dim}, model {P['model']['name']}")
    print("first action:", a)
    sys.exit(0)

from opensearchpy import OpenSearch, helpers  # noqa: E402  (only needed for a real upload)

auth = (os.environ["OPENSEARCH_USER"], os.environ["OPENSEARCH_PASSWORD"]) if "OPENSEARCH_USER" in os.environ else None
client = OpenSearch(args.host, http_auth=auth, use_ssl=args.host.startswith("https"),
                verify_certs=not args.no_verify_certs, ssl_show_warn=False, timeout=300)

if args.recreate and client.indices.exists(args.index):
    client.indices.delete(args.index)
if (args.create or args.recreate) and not client.indices.exists(args.index):
    client.indices.create(args.index, body=P["index_mapping"])
assert client.indices.exists(args.index), f"index {args.index} missing: pass --create"

# faster bulk load: no refresh while loading
client.indices.put_settings(index=args.index, body={"index": {"refresh_interval": "-1"}})
ok = 0
for success, info in helpers.streaming_bulk(client, actions(), chunk_size=args.batch, max_retries=3, raise_on_error=False):
    if success:
        ok += 1
        if ok % 20000 == 0:
            print(f"{ok:,}/{len(ids):,}", flush=True)
    else:
        print("FAILED:", info, file=sys.stderr)
client.indices.put_settings(index=args.index, body={"index": {"refresh_interval": "1s"}})
client.indices.refresh(args.index)
count = client.count(index=args.index)["count"]
print(f"indexed {ok:,}/{len(ids):,}; index count = {count:,}")
if ok != len(ids) or count < len(ids):
    sys.exit(f"INCOMPLETE LOAD: {ok:,} indexed, {count:,} in index, {len(ids):,} expected; alias not switched")
if args.alias:
    acts = [{"add": {"index": args.index, "alias": args.alias}}]
    if client.indices.exists_alias(name=args.alias):
        acts = [{"remove": {"index": old, "alias": args.alias}} for old in client.indices.get_alias(name=args.alias) if old != args.index] + acts
    client.indices.update_aliases(body={"actions": acts})
    print(f"alias {args.alias} -> {args.index}")
