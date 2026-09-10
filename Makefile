# QualiZeal Knowledge Fabric — developer & operator entrypoints.
# The platform is stdlib-only Python: no external services required locally.
PY ?= python3
KF_DB ?= ./data/kf.db
PORT ?= 8080
export KF_DB
export PYTHONPATH := .

UV ?= uv

.PHONY: help install up down health test lint fmt notices corpus showcase demo seed ask serve mcp demo-reset licences quality parity ci compose-up compose-down

PROFILE ?= lite
export KF_PROFILE := $(PROFILE)

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
	 awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'

install: ## Create the uv environment with dev tooling (uv sync --extra dev)
	@$(UV) sync --extra dev

up: seed serve ## Bring the platform up locally (seed demo data, then serve)

down: ## Stop and clear local state
	@rm -f $(KF_DB)* ; echo "local state cleared"

health: ## Report platform health
	@$(PY) -c "from knowledge_fabric.app import Platform; p=Platform(db_path='$(KF_DB)'); print('DB ok; model available:', p.model_available())"

test: ## Run the full test suite (uv run pytest -q)
	@$(UV) run pytest -q

lint: ## Ruff lint + format check (must be clean before commit)
	@$(UV) run ruff check . && $(UV) run ruff format --check .

fmt: ## Auto-format the repository with Ruff
	@$(UV) run ruff format .

notices: ## Regenerate THIRD_PARTY_NOTICES.md from the licence manifest
	@$(UV) run python scripts/notices.py

corpus: ## Ingest the QualiZeal corpus into the product fabric (see load-corpus for a folder)
	@$(UV) run python scripts/load_qualizeal_corpus.py $(or $(DIR),corpus)

showcase: ## Build the self-contained static showcase snapshot for GitHub Pages
	@$(UV) run python scripts/build_showcase.py

demo: ## Run the narrated end-to-end execution demo
	@$(PY) scripts/demo.py

seed: ## Seed synthetic demo tenants (identifier-safety validated)
	@$(PY) -m knowledge_fabric.cli seed

ask: ## make ask Q="your question" [TENANT=qualizeal USER=asker.public]
	@$(PY) -m knowledge_fabric.cli ask $(or $(TENANT),qualizeal) $(or $(USER),asker.public) "$(Q)"

serve: ## Serve the HTTP surfaces (Ask console at http://localhost:$(PORT)/)
	@KF_PORT=$(PORT) $(PY) -m knowledge_fabric.surfaces.http_api

mcp: ## Run the MCP agent tool server (JSON-RPC over stdio)
	@$(PY) -m knowledge_fabric.surfaces.agent_mcp

dashboard: ## Build a self-contained telemetry-dashboard snapshot (real seeded data)
	@KF_SNAPSHOT=$(or $(OUT),./data/kf_dashboard_snapshot.html) $(PY) scripts/dashboard_snapshot.py

load-corpus: ## Ingest a folder of QualiZeal .docx into the 'qualizeal' tenant: make load-corpus DIR=path
	@$(PY) scripts/load_qualizeal_corpus.py $(DIR)

doctor: ## AWS/local deployment readiness report: make doctor TARGET=aws|local
	@$(PY) scripts/doctor.py --target $(or $(TARGET),local)

consoles: ## Print the console URLs (Ask / Curator / Admin / Dashboard)
	@echo "Ask: http://localhost:$(PORT)/   Curator: /curator   Admin: /admin   Dashboard: /dashboard"

sync: ## Show connector source health + registry
	@$(PY) -c "from knowledge_fabric.app import Platform;from knowledge_fabric.tenants import demo;from knowledge_fabric.ingestion.sync import SyncManager;from knowledge_fabric.connectors import registry;p=Platform(db_path='$(KF_DB)');print('connectors:',registry.available());print('sources:',SyncManager(p).source_health('qualizeal'))"

demo-reset: down seed ## One-command reset to a clean, seeded, known-good state
	@echo "reset to seeded state"

licences: ## Licence gate (I14): fails on any non-permissive runtime dependency
	@$(PY) scripts/licence_gate.py

quality: ## Answer-quality gate (T28): golden suite over the model-free path
	@$(PY) scripts/quality_gate.py

parity: ## Static parity (T32): the shipped engine.js answers like the server (needs node)
	@$(PY) scripts/parity_check.py

load: ## Load test (T33): the answer path under concurrent load, gated on SLOs
	@$(PY) scripts/load_test.py

ci: test licences quality ## What CI runs
	@$(PY) -c "from knowledge_fabric.tenants import demo; assert demo.validate_identifiers()==[]; print('identifier-safety: PASS')"

compose-up: ## Bring the stack up under a profile: make compose-up PROFILE=lite|full
	@cd deploy/compose && KF_PROFILE=$(PROFILE) docker compose --profile $(PROFILE) up -d

compose-down: ## Tear the stack down
	@cd deploy/compose && docker compose --profile lite --profile full down
