# Deploying Word Search (Docker Compose + OpenSearch, behind your host Caddy)

```
internet ──443──▶ Caddy (on the host: TLS, gzip, headers)
                    │  reverse_proxy 127.0.0.1:8010   (loopback only)
                    ▼
              ┌─ docker compose ───────────────────────────────────────┐
              │  web (Django + gunicorn) ──▶ opensearch (kNN vectors)   │
              │        │                      on an internal network,   │
              │        └──▶ words.db           never published          │
              │             (SQLite, read-only: words, entries, relations, sources)
              └─────────────────────────────────────────────────────────┘
```

| File | Purpose |
|---|---|
| `docker-compose.yml` | the stack: `opensearch`, `web`, and the one-shot `loader` (profile `load`) |
| `Dockerfile`, `requirements.txt` | one image for the web app and the loader |
| `deploy/Caddyfile` | site block for your host Caddy (checked with `caddy validate`) |
| `deploy/Caddyfile.examples` | www redirect, basic auth, several domains, behind a CDN, rate limiting, maintenance page |
| `deploy/compose.debug.yml` | exposes OpenSearch on `127.0.0.1:9200` for debugging only |
| `.env.example` | every setting, commented; secrets are required |
| `Makefile` | `make check / up / load / smoke / ps / logs / down` |

## What the server needs

| | minimum | comfortable |
|---|---|---|
| RAM | 6 GB | 8 GB |
| Disk | 10 GB | 20 GB (SQLite 0.7 GB, pkl 1.3 GB, index ~3 GB, images) |
| CPU | 2 cores | 4 |

RAM split: OpenSearch heap `OS_HEAP` (2 GB) + ~2.5 GB page cache for the 713k × 768 vector index (`OS_MEM_LIMIT` caps the container at 5 GB) + web ≤ 1 GB (`WEB_MEM_LIMIT`). The **loader** briefly needs ~3 GB to unpickle `embeddings.pkl`. Needs Docker Engine with the Compose v2 plugin, and Caddy 2 on the host.

OpenSearch needs this host setting (this machine already has it; persist it on the server):

```bash
sudo sysctl -w vm.max_map_count=262144
echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-opensearch.conf
```

## First deployment

1. **Copy the repo and two data files** to the server (`out/words.db`, `out/embeddings.pkl`; built by `pipeline/`, see `pipeline/README.md`):
   ```bash
   rsync -avP --exclude data_raw --exclude out --exclude .venv ./ you@server:word_search/
   rsync -avP out/words.db out/embeddings.pkl you@server:word_search/out/
   ```
2. **Configure**
   ```bash
   cp .env.example .env
   python3 -c "import secrets; print(secrets.token_urlsafe(50))"    # -> DJANGO_SECRET_KEY
   # set OPENSEARCH_PASSWORD (8+ chars, upper+lower+digit+symbol) and SITE_ADDRESS (your domain)
   make check
   ```
3. **Start** `make up`, then wait until `make ps` shows `opensearch` healthy (about a minute).
4. **Load the vectors** (one-off, tens of minutes: OpenSearch builds the HNSW graph for 713k documents): `make load`.
   It creates `words_v1` from the mapping stored in the pkl, bulk-indexes, verifies the count, then points the alias `words` at it. Search works before this finishes, just without "similar by meaning".
5. **Caddy**: put `deploy/Caddyfile` into `/etc/caddy/Caddyfile` (or `import` it), set your domain and the port, then
   ```bash
   caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile && sudo systemctl reload caddy
   ```
   DNS A/AAAA for the domain must point at the server, ports 80 and 443 open. Caddy gets the certificate itself.
6. **Check** `make smoke` (loopback), then `curl -sS https://<domain>/healthz` and open `https://<domain>/perf/`: it should say *Vector backend: opensearch* with the document count.

`SITE_ADDRESS` in `.env` and the domain in the Caddyfile must be the same name, or Django answers 400 *Invalid HTTP_HOST*.

## Running without OpenSearch

Set `WORDSEARCH_VECTOR_BACKEND=numpy` on `web`, mount `out/vec_*.npy` and `out/vectors_meta.json` next to `words.db` in `/data`, and use `GUNICORN_WORKERS=1` plus a bigger `WEB_MEM_LIMIT` (~3 GB): each worker would otherwise hold the 2.2 GB of vectors. Latency is similar (~50 ms vector scan). OpenSearch pays off with several workers or hosts.

## Updating

| Change | What to do |
|---|---|
| New `words.db` | copy the file, `docker compose restart web` |
| New embeddings (other model, more words) | set `OS_INDEX_VERSION=words_v2` in `.env`, `make load`. The alias moves only after the count check passes, so users never see a half-loaded index. Delete the old index afterwards (via `deploy/compose.debug.yml`, `curl -u admin:… -X DELETE`) |
| New code | `make up` (rebuilds, restarts `web`) |
| OpenSearch version | change `OPENSEARCH_VERSION`, `make up`; read the release notes first, and be ready to re-run `make load` |

## Backups

Everything in OpenSearch is **derived** from `words.db` + `embeddings.pkl`, so back those two files up (and `.env`). The index can always be rebuilt with `make load`.

## Failure behaviour

* OpenSearch down or slow: translations, relations and definitions still work; only "similar by meaning" disappears. After one failure the app skips OpenSearch for 15 s, so a hung cluster does not slow every request. `/healthz` reports `degraded` (HTTP 200); `/perf/` shows the error. `web` does not depend on OpenSearch being healthy to start.
* `words.db` missing or unreadable: `/healthz` returns 503 and Caddy stops routing to the app.
* Both containers restart on failure (`unless-stopped`); gunicorn recycles workers every ~5000 requests; logs rotate (10 MB × 3 per container).

## Security notes

* Only Caddy is public. The app is bound to `127.0.0.1`; OpenSearch has no published port and sits on an `internal` Docker network. Compose refuses to start without `DJANGO_SECRET_KEY`, `OPENSEARCH_PASSWORD` and `SITE_ADDRESS`.
* The OpenSearch link uses the image's self-signed demo certificate with verification off (`OPENSEARCH_VERIFY_CERTS=0`), because it never leaves the private network. If OpenSearch moves to another host, give it a real certificate and set this to `1`.
* `web`: non-root, read-only filesystem, all capabilities dropped, `no-new-privileges`, memory and PID limits. The app has no login, no sessions and no writes.
* It connects to OpenSearch as `admin`. For stricter setups create a read-only user for `web` and keep `admin` for the loader.
* Responses carry a strict CSP, `nosniff` and a same-origin referrer policy; the Caddyfile adds HSTS. Enable HSTS only once the domain serves HTTPS correctly.
* Source data is CC BY-SA (mydictionary.in, Wiktionary), WordNet, and TVA glossaries whose redistribution rights are not confirmed. Each result links to its source; check the TVA terms before going public (`Caddyfile.examples` shows a basic-auth gate).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `opensearch` exits: *vm.max_map_count … too low* | the sysctl above |
| `opensearch` exits: *memory locking requested … unable* | host forbids memlock: set `bootstrap.memory_lock: "false"` in the compose file |
| `opensearch` unhealthy for minutes, or restarts | first start generates certs and is slow (`docker compose logs opensearch`); repeated restarts are usually the memory cap: raise `OS_MEM_LIMIT` and `OS_HEAP` together |
| Loader dies with `Killed` | out of memory while unpickling: more RAM or swap during `make load` only |
| 400 *Invalid HTTP_HOST* | the domain is not `SITE_ADDRESS` in `.env` (or in `DJANGO_ALLOWED_HOSTS`) |
| 502/503 from Caddy | `web` unhealthy or wrong port: `make ps`, `docker compose logs web`; `/healthz` 503 means `words.db` is missing from the mount; check `WEB_PORT` equals the port in the Caddyfile |
| "Similar by meaning" missing | `/perf/` says why; usually the loader has not run or the alias `words` does not exist yet: `make load` |
| Slow first searches after a restart | SQLite page cache is cold; it warms within a few searches |
