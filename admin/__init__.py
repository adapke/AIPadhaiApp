"""AI Pathshala Admin Console — separate FastAPI service.

Run locally with:
    uvicorn admin.app:app --port 8001

Deploy as a separate Render service. Reuses padhai.* for DB / auth / cache.
"""

__version__ = "0.8.0"
