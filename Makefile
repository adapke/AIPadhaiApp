# E2E orchestration. `make e2e` is the one command everything else
# composes from. See SPRINT_E2E.md for the sprint plan.

.PHONY: help setup up down logs ps seed smoke cypress e2e clean test verify verify-ci all-verify docs-check lint security i18n-audit audit coverage gitleaks backup stats iframe-check nightly-ops docker-check

help: ## Show this help
	@echo ""
	@echo "  \033[1mDev loop\033[0m"
	@awk 'BEGIN {FS = ":.*?## "} \
		/^(setup|up|down|logs|ps|clean):.*?## / \
		{printf "    \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "  \033[1mTest + verify (run before pushing)\033[0m"
	@awk 'BEGIN {FS = ":.*?## "} \
		/^(test|verify|verify-ci|all-verify|docs-check|lint|security|coverage|gitleaks|audit|docker-check|seed|smoke|cypress|e2e|i18n-audit):.*?## / \
		{printf "    \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "  \033[1mOps (cron / production)\033[0m"
	@awk 'BEGIN {FS = ":.*?## "} \
		/^(backup|stats|iframe-check|nightly-ops):.*?## / \
		{printf "    \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "  All targets:"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "    \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

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

security: ## Pre-deploy security audit (run before every prod push)
	@echo "==> Pre-deploy security audit (codified SECURITY.md invariants)"
	@python scripts/check_security.py

i18n-audit: ## Hardcoded English UI strings vs i18n key coverage
	@python scripts/audit_i18n.py

audit: ## pip-audit: scan declared deps for known CVEs (requires pip-audit)
	@echo "==> requirements.txt"
	@python -m pip_audit -r requirements.txt $$(./scripts/_pip_audit_ignore_flags.sh 2>/dev/null)
	@echo "==> requirements-optional.txt"
	@python -m pip_audit -r requirements-optional.txt $$(./scripts/_pip_audit_ignore_flags.sh 2>/dev/null)
	@echo "no known vulnerabilities."

coverage: ## pytest with coverage, fail-under floor matches CI (30%)
	@PADHAI_SKIP_DOTENV=1 PADHAI_JWT_SECRET=qa-test-secret-abcdef0123456789abcdef0123456789 \
		PYTHONPATH=. python -m pytest tests/ \
		--cov=padhai --cov-report=term --cov-fail-under=30

gitleaks: ## Secret scan working tree + history (requires gitleaks binary)
	@command -v gitleaks >/dev/null 2>&1 || { \
		echo "gitleaks not installed. Get it from https://github.com/gitleaks/gitleaks"; \
		exit 1; \
	}
	@gitleaks detect --source . --no-banner --redact --config .gitleaks.toml

verify: ## Quick pre-PR check: lint + invariant guards + pytest + structural bench (~20s on a warm cache)
	@echo "==> ruff (F E I B UP SIM RUF ARG + B904)"
	@python -m ruff check padhai/ admin/ tests/ scripts/
	@echo "==> model-id guard (no literal claude-* outside padhai/models.py)"
	@python scripts/check_model_constants.py
	@echo "==> router registry guard (files match _ROUTER_NAMES)"
	@python scripts/check_router_registry.py
	@echo "==> pytest"
	@PADHAI_SKIP_DOTENV=1 PADHAI_JWT_SECRET=qa-test-secret-abcdef0123456789abcdef0123456789 \
		PYTHONPATH=. python -m pytest tests/ -q --tb=line
	@echo "==> accuracy bench (structural mode — no API key needed)"
	@PADHAI_DB_PATH=/tmp/qa_bench.db PYTHONPATH=. python -X utf8 \
		scripts/run_accuracy_bench.py --mode=structural --min-pass-rate=0.5
	@echo ""
	@echo "verify green — safe to push."

docs-check: ## prod-129 — Fast docs-only gate: verify provider walkthroughs + structure tests. ~1s.
	@PADHAI_SKIP_DOTENV=1 \
		PADHAI_JWT_SECRET=docs-check-secret-abcdef0123456789abcdef0123456789 \
		PYTHONPATH=. python -m pytest \
		tests/test_provider_docs.py tests/test_pr_template.py \
		-q --tb=line

all-verify: ## prod-102 — Aggregate: verify + audit + coverage. One command for a clean pre-release push.
	@echo ""
	@echo "==> Step 1/3: make verify (lint + invariant guards + pytest + bench)"
	@$(MAKE) --no-print-directory verify
	@echo ""
	@echo "==> Step 2/3: make audit (pip-audit — requires network)"
	@$(MAKE) --no-print-directory audit || echo "[warn] audit step skipped or failed; continuing"
	@echo ""
	@echo "==> Step 3/3: make coverage (pytest + coverage gate)"
	@$(MAKE) --no-print-directory coverage || echo "[warn] coverage step skipped or failed; continuing"
	@echo ""
	@echo "all-verify done — review the per-step output above before pushing."

verify-ci: ## prod-64 — CI-friendly verify (Linux/macOS). Skips Windows-specific bootstrap; uses $TMPDIR-aware paths.
	@echo "==> ruff (F E I B UP SIM RUF ARG + B904)"
	@python -m ruff check padhai/ admin/ tests/ scripts/
	@echo "==> model-id guard"
	@python scripts/check_model_constants.py
	@echo "==> router registry guard"
	@python scripts/check_router_registry.py
	@echo "==> pytest"
	@PADHAI_SKIP_DOTENV=1 \
		PADHAI_JWT_SECRET=ci-test-secret-abcdef0123456789abcdef0123456789 \
		PYTHONPATH=. python -m pytest tests/ -q --tb=line
	@echo "==> accuracy bench (structural)"
	@PADHAI_DB_PATH=$${TMPDIR:-/tmp}/qa_bench_ci.db PYTHONPATH=. python -X utf8 \
		scripts/run_accuracy_bench.py --mode=structural --min-pass-rate=0.5
	@echo ""
	@echo "verify-ci green."
	@echo ""
	@echo "Note: live-mode bench is a separate workflow."
	@echo "Set ANTHROPIC_API_KEY in CI secrets to run it; structural-only here."

clean: ## Remove local QA artifacts (DBs, logs)
	rm -f qa_*.db qa_*.log qa_*.err /tmp/padhai_*.db /tmp/qa_*.db

nightly-ops: ## prod-91 — Run nightly ops bundle (backup + iframe-check + stats). Set AUTO_DEMOTE=1 to demote broken rows.
	@chmod +x scripts/nightly_ops.sh 2>/dev/null || true
	@bash scripts/nightly_ops.sh
	@echo ""
	@echo "Cron template:"
	@echo "  23 3 * * * cd $${PWD} && AUTO_DEMOTE=1 ./scripts/nightly_ops.sh >> /var/log/padhai-nightly.log 2>&1"

iframe-check: ## prod-82 — Walk verified concept videos + report iframe-block changes. Set AUTO_DEMOTE=1 to demote broken rows.
	@PYTHONPATH=. python scripts/check_verified_iframes.py \
		$${AUTO_DEMOTE:+--auto-demote} --sleep-ms $${IFRAME_SLEEP_MS:-200}

stats: ## prod-78 — Print curator-workflow stats JSON to stdout. Use --days=N via STATS_DAYS env.
	@PYTHONPATH=. python scripts/print_curator_stats.py \
		--days $${STATS_DAYS:-30} --pretty

backup: ## prod-69 — Run online SQLite backup (safe under concurrent writes). Wraps scripts/backup_sqlite.sh.
	@if [ ! -x scripts/backup_sqlite.sh ]; then \
		echo "==> chmod +x scripts/backup_sqlite.sh"; \
		chmod +x scripts/backup_sqlite.sh; \
	fi
	@./scripts/backup_sqlite.sh
	@echo ""
	@echo "Backup written under $${PADHAI_BACKUP_DIR:-$$HOME/.padhai/backups}/"
	@echo "Restore steps:"
	@echo "  1. Stop the server                  (bash scripts/stop_local.sh)"
	@echo "  2. gunzip the desired snapshot      (gunzip jobs_<ts>.db.gz)"
	@echo "  3. Replace \$$PADHAI_DB_PATH         (defaults to ~/.padhai/jobs.db)"
	@echo "  4. Restart                          (bash scripts/run_local.sh)"
	@echo ""
	@echo "Cron template (hourly):"
	@echo "  17 * * * * $${PWD}/scripts/backup_sqlite.sh >> /var/log/padhai-backup.log 2>&1"

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
