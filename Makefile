# QualiZeal Knowledge Fabric — developer & operator entrypoints.
# The platform is stdlib-only Python: no external services required locally.
PY ?= python3
KF_DB ?= ./data/kf.db
PORT ?= 8080
export KF_DB
export PYTHONPATH := .

.PHONY: help up down health test demo seed ask serve mcp demo-reset licences ci

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
	 awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'

up: seed serve ## Bring the platform up locally (seed demo data, then serve)

down: ## Stop and clear local state
	@rm -f $(KF_DB)* ; echo "local state cleared"

health: ## Report platform health
	@$(PY) -c "from knowledge_fabric.app import Platform; p=Platform(db_path='$(KF_DB)'); print('DB ok; model available:', p.model_available())"

test: ## Run the full test suite (maps to Build Plan Section 20)
	@$(PY) -m unittest discover -s tests -p 'test_*.py' -v

demo: ## Run the narrated end-to-end execution demo
	@$(PY) scripts/demo.py

seed: ## Seed synthetic demo tenants (identifier-safety validated)
	@$(PY) -m knowledge_fabric.cli seed

ask: ## make ask Q="your question" [TENANT=acme-assurance USER=asha.asker]
	@$(PY) -m knowledge_fabric.cli ask $(or $(TENANT),acme-assurance) $(or $(USER),asha.asker) "$(Q)"

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
	@$(PY) -c "from knowledge_fabric.app import Platform;from knowledge_fabric.tenants import demo;from knowledge_fabric.ingestion.sync import SyncManager;from knowledge_fabric.connectors import registry;p=Platform(db_path='$(KF_DB)');print('connectors:',registry.available());print('sources:',SyncManager(p).source_health('acme-assurance'))"

demo-reset: down seed ## One-command reset to a clean, seeded, known-good state
	@echo "reset to seeded state"

licences: ## Print the dependency licence posture (I14)
	@cat docs/licences.md

ci: test licences ## What CI runs
	@$(PY) -c "from knowledge_fabric.tenants import demo; assert demo.validate_identifiers()==[]; print('identifier-safety: PASS')"
