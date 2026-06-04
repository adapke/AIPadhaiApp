"""Apply the PadhAI Postgres schema.

Usage:
    DATABASE_URL=postgresql://user:pass@host:5432/padhai python -m scripts.migrate

Idempotent — every CREATE in the schema uses IF NOT EXISTS, so re-running
on an existing database is a no-op. Run on every deploy."""

from __future__ import annotations

import sys

from padhai.db import PostgresJobStore, get_db_url


def main() -> int:
    dsn = get_db_url()
    if not dsn:
        # First-time deploys won't have Postgres provisioned yet; the
        # web service still boots fine on SQLite + local disk in that
        # case. Make this a noop so it can run as a Render preDeploy
        # without blocking the very first deploy.
        print("DATABASE_URL not set — skipping schema migration "
              "(this is fine on a SQLite-only deploy)")
        return 0
    print(f"applying schema to {dsn.split('@')[-1]}")
    store = PostgresJobStore(dsn)
    store.init_schema()
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
