# E2E orchestration. `make e2e` is the one command everything else
# composes from. See SPRINT_E2E.md for the sprint plan.

.PHONY: help setup up down logs ps seed smoke cypress e2e clean test verify lint

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Copy .env from template (only if missing) + generate fresh secrets
	@if [ ! -f .env ]; then \
		cp .env.docker.example .env; \
		echo "PADHAI_JWT_SECRET=$$(python -c 'import secrets;print(secrets.token_urlsafe(48))')" >> .env; \
		echo "ADMIN_JWT_SECRET=$$(python -c 'import secrets;print(secrets.token_urlsafe(48))')" >> .env; \
		echo "ADMIN_BOOTSTRAP_TOKEN=$$(python -c 'import secrets;print(secrets.token_urlsafe(32))')" >> .env; \
		echo "Created .env from template + generated fresh secrets."; \
		echo "Edit it to add ANTHROPIC_API_KEY if you want real Claude calls."; \
	else \
		echo ".env already exists — not touching."; \
	fi

up: setup ## docker-compose up -d + wait for app healthcheck
	docker-compose up -d
	@echo "Waiting for app /healthz..."
	@for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do \
		if curl -sf http://localhost:8000/healthz > /dev/null 2>&1; then \
			echo "App ready at http://localhost:8000"; \
			exit 0; \
		fi; \
		sleep 3; \
	done; \
	echo "Timeout waiting for app — docker-compose logs:"; \
	docker-compose logs --tail=80; \
	exit 1

down: ## docker-compose down -v (wipes volumes too)
	docker-compose down -v

logs: ## tail compose logs
	docker-compose logs -f --tail=100

ps: ## docker-compose ps
	docker-compose ps

seed: ## Run seed_demo.py against the running stack
	@docker-compose exec -T app python scripts/seed_demo.py --base-url http://localhost:8000

smoke: ## HTTP e2e smoke against the running stack
	@docker-compose exec -T app python scripts/e2e_smoke.py --base-url http://localhost:8000

cypress: ## Run the Cypress full-flow spec (host-side, against compose)
	npx cypress run --spec cypress/e2e/17-e2e-full-flow.cy.js

e2e: up seed smoke cypress ## Bring up stack, seed, smoke, Cypress
	@echo ""
	@echo "E2E green. Tear down with: make down"

test: ## Run pytest + 4 QA harnesses against ad-hoc local server
	PADHAI_SKIP_DOTENV=1 PADHAI_JWT_SECRET=qa-test-secret-abcdef0123456789abcdef0123456789 \
		PYTHONPATH=. python -m pytest tests/ -q --tb=line
	PYTHONPATH=. python -X utf8 scripts/qa_rag_surfaces.py
	PYTHONPATH=. python -X utf8 scripts/qa_daily_cap.py
	PYTHONPATH=. python -X utf8 scripts/qa_alert_ui.py
	PADHAI_DB_PATH=/tmp/qa_bench.db PYTHONPATH=. python -X utf8 scripts/run_accuracy_bench.py --mode=structural

lint: ## Ruff lint (all 9 enforced categories)
	python -m ruff check padhai/ admin/ tests/ scripts/

verify: ## Quick pre-PR check: lint + pytest + structural bench + model-id guard (~20s on a warm cache)
	@echo "==> ruff (F E I B UP SIM RUF ARG + B904)"
	@python -m ruff check padhai/ admin/ tests/ scripts/
	@echo "==> model-id guard (no literal claude-* outside padhai/models.py)"
	@python scripts/check_model_constants.py
	@echo "==> pytest"
	@PADHAI_SKIP_DOTENV=1 PADHAI_JWT_SECRET=qa-test-secret-abcdef0123456789abcdef0123456789 \
		PYTHONPATH=. python -m pytest tests/ -q --tb=line
	@echo "==> accuracy bench (structural mode — no API key needed)"
	@PADHAI_DB_PATH=/tmp/qa_bench.db PYTHONPATH=. python -X utf8 \
		scripts/run_accuracy_bench.py --mode=structural --min-pass-rate=0.5
	@echo ""
	@echo "verify green — safe to push."

clean: ## Remove local QA artifacts (DBs, logs)
	rm -f qa_*.db qa_*.log qa_*.err /tmp/padhai_*.db /tmp/qa_*.db

docker-check: ## Validate docker-compose.yml + Dockerfile.dev without running Docker
	@echo "==> docker-compose.yml YAML:"
	@python -c "import yaml; yaml.safe_load(open('docker-compose.yml'))" \
		&& echo "    OK" || (echo "    FAIL"; exit 1)
	@echo "==> Dockerfile.dev parseable:"
	@if [ -f Dockerfile.dev ]; then \
		head -1 Dockerfile.dev | grep -q '^FROM' && echo "    OK starts with FROM" \
		|| (echo "    FAIL no FROM"; exit 1); \
	else echo "    FAIL Dockerfile.dev missing"; exit 1; fi
	@echo "==> db/changesets/master.xml + included files:"
	@if [ -f db/changesets/master.xml ] && \
	   [ -f db/changesets/001_core_schema.sql ] && \
	   [ -f db/changesets/002_module_tables.sql ]; then \
		echo "    OK 3 changeset files present"; \
	else echo "    FAIL changeset files missing"; exit 1; fi
	@echo "==> services declared:"
	@python -c "import yaml; d=yaml.safe_load(open('docker-compose.yml')); print('    ' + ', '.join(sorted(d['services'].keys())))"
	@echo "==> volumes declared:"
	@python -c "import yaml; d=yaml.safe_load(open('docker-compose.yml')); print('    ' + ', '.join(sorted(d.get('volumes') or {})))"
	@echo "compose stack ready — run 'make up' on a Docker host"
