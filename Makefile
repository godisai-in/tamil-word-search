# Shortcuts for the Docker deployment. `make help` lists them.
.DEFAULT_GOAL := help
COMPOSE ?= docker compose

help:            ## show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-8s %s\n", $$1, $$2}'

check:           ## host prerequisites (vm.max_map_count, .env, data files)
	@v=$$(sysctl -n vm.max_map_count); if [ "$$v" -ge 262144 ]; then echo "ok   vm.max_map_count=$$v"; else echo "FIX  vm.max_map_count=$$v, need >= 262144: sudo sysctl -w vm.max_map_count=262144"; fi
	@if [ -f .env ]; then echo "ok   .env"; else echo "FIX  .env missing: cp .env.example .env"; fi
	@if [ -f out/words.db ]; then echo "ok   out/words.db"; else echo "FIX  out/words.db missing (python pipeline/build_db.py)"; fi
	@if [ -f out/embeddings.pkl ]; then echo "ok   out/embeddings.pkl"; else echo "FIX  out/embeddings.pkl missing (see pipeline/README.md)"; fi

up:              ## build and start opensearch + web
	$(COMPOSE) up -d --build

load:            ## one-off: load vectors into OpenSearch and switch the alias
	$(COMPOSE) --profile load run --rm loader

down:            ## stop everything (data volumes are kept)
	$(COMPOSE) down

logs:            ## follow logs
	$(COMPOSE) logs -f --tail=100

ps:              ## service status and health
	$(COMPOSE) ps

smoke:           ## check the running stack on its loopback port (then curl your public domain too)
	@port=$$(grep -E '^WEB_PORT=' .env | cut -d= -f2); port=$${port:-8010}; \
	echo "healthz:"; curl -sS http://127.0.0.1:$$port/healthz; echo; \
	echo "search 'bank' has a similar-by-meaning section (1 = vectors working):"; \
	curl -sS "http://127.0.0.1:$$port/search/?q=bank" | grep -c 'similar by meaning' || true

.PHONY: help check up load down logs ps smoke
