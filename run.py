"""Local dev launcher.

Sets all required environment variables BEFORE importing padhai.web,
so DATABASE_URL is guaranteed visible at module import time.
Runs WITHOUT reload so everything stays in one process — env vars
are 100% guaranteed to propagate.

Usage:
    python run.py
"""
import os

# ── Database ────────────────────────────────────────────────────────────────
os.environ.setdefault("DATABASE_URL",
                      "postgresql://postgres:postgres@localhost:5432/padhaiai")

# ── Auth secrets (dev placeholders — never use in production) ───────────────
os.environ.setdefault("PADHAI_JWT_SECRET",
                      "dev-padhai-secret-change-me-locally-please")
os.environ.setdefault("ADMIN_JWT_SECRET",
                      "dev-admin-secret-change-me-locally-please")

# Import the app AFTER env vars are set so module-level use_postgres() sees them
from padhai.web import app  # noqa: E402

import uvicorn  # noqa: E402

if __name__ == "__main__":
    print(f"[run.py] DATABASE_URL = {os.environ.get('DATABASE_URL', 'NOT SET')}")
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)
