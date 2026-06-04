# Contributing to AIPadhaiApp

This document is the operator's manual for working in this repo. If
something here disagrees with `CLAUDE.md`, treat that file as the
deeper reference and update this one. CLAUDE.md is the
"why everything is structured this way" guide; this file is
"how to actually get work done day-to-day."

---

## Dev setup (5 minutes)

```bash
# 1. Clone + Python deps
git clone <this-repo> && cd AIPadhaiApp
python -m pip install -r requirements.txt
python -m pip install -r requirements-test.txt

# 2. JWT secret (server refuses to start without it)
python -c 'import secrets; print(secrets.token_urlsafe(48))'
# Paste result into .env as PADHAI_JWT_SECRET=...

# 3. Anthropic key (optional — heuristic fallbacks work without it)
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env

# 4. Pre-commit hook (recommended)
pip install pre-commit
pre-commit install

# 5. Run it
bash scripts/run_local.sh    # Linux/macOS
# or
powershell -ExecutionPolicy Bypass -File scripts/run_local.ps1   # Windows
```

`curl http://localhost:8000/healthz` should return JSON with
`status: ok`.

## Full E2E stack (Docker)

```bash
make e2e
```

Brings up Postgres + MinIO + Liquibase + the app, seeds demo
accounts (`riya@demo.local` / `parent@demo.local` / etc.), runs the
HTTP smoke + Cypress full-flow spec, tears down. See `SPRINT_E2E.md`
for the architecture.

Without Docker: `make docker-check` validates the compose stack
syntactically. `make test` runs the pytest + QA harness regression
suite locally.

---

## The Makefile is the command reference

```
make help          # list every target
make setup         # .env from template + fresh JWT/admin secrets
make up            # docker-compose up + wait for /healthz
make seed          # populate demo accounts
make smoke         # HTTP e2e smoke (8 steps)
make cypress       # full-flow Cypress spec
make e2e           # up + seed + smoke + cypress
make test          # pytest + 4 QA harnesses + accuracy bench
make docker-check  # validate compose without Docker installed
make clean         # remove local QA artifacts
make down          # docker-compose down -v
```

---

## Code-organisation conventions

| Surface | Where |
|---|---|
| FastAPI app | `padhai/web.py` — single instance; ~14k lines (down from 16k) |
| Route extractions | `padhai/routers/<name>.py` — registered via `padhai/routers/__init__.py:_ROUTER_NAMES`. Late-import `web` for shared globals. See `multipage.py` as the canonical pattern. |
| Auth | `padhai/auth.py` — bcrypt, JWT, `SQLiteUserRepository` + `PostgresUserRepository` |
| AI calls | All 6 surfaces use `padhai/llm_call.py:call_claude()`. New surfaces should too — the wrapper handles SDK import, token + cost accounting, `llm_obs.record_call`, and (optionally) the daily cap. |
| Shared SQLite path | `padhai/db.py:sqlite_path()`. Modules call `from . import db as _db; _db.sqlite_path()`. Never hand-roll the env-lookup again. |
| Module migrations | Each module defines `migrate()` + a `SCHEMA` constant; `web.py` lifespan startup calls every module's `migrate()`. New module = add a `migrate()` call to the lifespan. |
| Tests | `tests/` for pytest; `tests/fixtures/golden_answers.json` for the accuracy bench; `cypress/e2e/` for UI specs; `scripts/qa_*.py` for re-runnable QA harnesses. |

---

## Lint + quality gates

Three things gate every PR:

1. **Ruff** (`F + E + I + B` ruleset) — `ruff check padhai/ admin/ tests/ scripts/`. Must be "All checks passed!". Pre-commit runs this on `git commit`.
2. **Pytest** — `make test` must show 37/37.
3. **Accuracy bench** — `scripts/run_accuracy_bench.py --mode=structural` runs 102 golden answers via the stub runner. Live mode (real Claude) runs nightly + on every push to main, with `min-pass-rate=0.75`.

If pre-commit blocks your commit:
- Ruff fixed the issue → re-stage + retry
- Ruff exited non-zero → check the message, fix the line, retry
- Genuine bypass needed → `git commit --no-verify` but document **why** in the commit message

### Ruff phased ruleset

Live as **blocking** in `pyproject.toml [tool.ruff.lint] select`:
- `F` (pyflakes) — undefined names, broken format strings
- `E` (pycodestyle errors) — minus E501/E701/E702/E401/E402/E741 (codebase-pattern ignores)
- `I` (isort) — import sorting
- `B` (bugbear) — minus B008 (FastAPI `Depends()` pattern) + B904 (344 existing exception-chain sites, triage planned)

Still **advisory** (advisable to run `ruff --fix --select X` when touching a file):
- `UP` (pyupgrade) — `Optional[X]` → `X | None`
- `SIM` (simplify) — readability wins

To promote the next category to blocking: get `ruff check --select <X>` to zero findings, edit `select` in `pyproject.toml`, document the move in CLAUDE.md §16.

---

## Commit message style

```
<area>: <imperative verb> <subject>     ← summary (<70 chars)
                                        ← blank line
<details ≤80 chars/line>
```

`<area>` examples: `feat`, `fix`, `refactor`, `polish`, `polish-2`,
`e2e-real`, `sprint(e2e)`. The "polish" labels mark sprints that
finish established patterns (call_claude migrations, router
extractions, lint gate tightening).

Body should explain **why**, not just what. Include:
- The verification: what test/harness confirms the change works.
- Side-effects: any bugs caught while landing the main work
  (these have been highlights — the lint gate paid for itself
  by catching a real `NameError` the first time it ran).

Always end with:
```
Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```
…when AI helped write the change.

---

## When you find a bug while doing something else

If it's a one-line fix + safe + has a test for it → land it in the
current commit, mention it in the body under a `Side-effects:` line.

If it's bigger → drop a note in CLAUDE.md §16 ("Still pending /
next up") and pick it up in the next sprint. The §16 list is the
project's running backlog.

---

## Where to add a new feature

1. New route → think hard before adding to `web.py`. Prefer a new
   `padhai/routers/<name>.py` with the late-import pattern.
2. New Claude call → use `llm_call.call_claude()`. Cost tracking
   + cap pre-flight + error normalization come free.
3. New module table → add `migrate()`, register in the web.py
   lifespan, add a Postgres mirror in `db/changesets/002_*.sql` if
   the production deployment needs it (most do).
4. New test → if it can use `pytest` + `TestClient`, that's the
   right home. If it needs a live server (multi-process,
   compose stack), add a script under `scripts/qa_*.py` instead.
5. New env var → document in CLAUDE.md §4 table.
6. New CI workflow → put it in `.github/workflows/`. Trigger
   discipline: PR + push to main for fast lanes; nightly cron for
   slow / paid (Anthropic) lanes; `workflow_dispatch` for manual.

---

## Where to ask for help

- Most "how does this work" answers live in CLAUDE.md. It's
  ~600 lines but indexed; ctrl-F is your friend.
- For "why" questions on a specific module, the docstring at the
  top of the file is usually authoritative.
- For "is this safe?" → run `make test` first, then ask.
