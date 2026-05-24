"""G1 — DB backend selector.

Today every module opens its own `sqlite3.connect(...)` via a local
`_conn()` helper. That additive-migration pattern is intentional —
it kept v0.6 → v1.0 cheap. But it bakes in SQLite.

This module is the precursor to the Postgres cutover described in
`G1_POSTGRES_MIGRATION.md`. It parses `DATABASE_URL` and tells callers
which engine is configured. Future modules (audit log, J3 curriculum
scorer, J6 question bank) target this abstraction directly. Existing
modules get ported during the dedicated cutover sprint, not now.

Selection rules:
- `DATABASE_URL=postgres://...` (or `postgresql://...`) → Postgres
- `DATABASE_URL=sqlite:///path` → SQLite at that path
- unset → SQLite at `PADHAI_DB_PATH` or `~/.padhai/jobs.db`

The actual psycopg connection isn't constructed here — that's the
job of the cutover sprint when we add psycopg to requirements.txt.
Today this module only **detects** the configured backend so we can
log/route accordingly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


SQLITE = "sqlite"
POSTGRES = "postgres"


@dataclass(frozen=True)
class BackendConfig:
    """Resolved backend + connection info. `dsn` is the original
    URL when Postgres; the SQLite file path when SQLite."""
    backend: str           # 'sqlite' | 'postgres'
    dsn: str               # 'postgres://...' or '/path/to/jobs.db'
    region: str | None     # 'ap-south-1' etc., parsed from Neon-style URLs


def resolve() -> BackendConfig:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url.startswith(("postgres://", "postgresql://")):
        parsed = urlparse(url)
        # Neon-style: ap-south-1.aws.neon.tech → region = 'ap-south-1'
        host = (parsed.hostname or "").lower()
        region = None
        if ".neon.tech" in host or ".aws.neon.tech" in host:
            region = host.split(".")[0]
        return BackendConfig(backend=POSTGRES, dsn=url, region=region)

    # SQLite (default + explicit sqlite:// scheme)
    if url.startswith("sqlite:///"):
        path = url[len("sqlite:///"):]
        # Empty path → fall through to env-var default
        if path:
            return BackendConfig(backend=SQLITE, dsn=path, region=None)

    custom = os.environ.get("PADHAI_DB_PATH")
    path = (
        Path(custom).expanduser() if custom
        else Path.home() / ".padhai" / "jobs.db"
    )
    return BackendConfig(backend=SQLITE, dsn=str(path), region=None)


def is_postgres() -> bool:
    return resolve().backend == POSTGRES


def is_sqlite() -> bool:
    return resolve().backend == SQLITE


def description() -> str:
    """One-line human summary for the startup banner. Avoids
    printing the password from a Postgres URL."""
    cfg = resolve()
    if cfg.backend == POSTGRES:
        parsed = urlparse(cfg.dsn)
        host = parsed.hostname or "?"
        port = parsed.port or 5432
        db = parsed.path.lstrip("/") or "?"
        region_label = f" [{cfg.region}]" if cfg.region else ""
        return f"postgres://{host}:{port}/{db}{region_label}"
    return f"sqlite://{cfg.dsn}"
