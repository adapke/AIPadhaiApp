# AI Pathshala Admin Console
#
# Separate container from the student-facing app. Same Python codebase
# (so it can `from padhai import ...`), but only the admin app is
# exposed and only admin routes are routable.
#
# Why a separate image: lets ops put this behind a different network
# policy (VPN / IP allowlist) and scale it independently. Student app
# may run 10 instances; admin only needs 1.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Minimal system deps — admin doesn't render video, so no ffmpeg/fonts/
# LibreOffice etc. Just enough to talk to Postgres and serve HTTP.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Admin's own requirements — kept minimal and decoupled from the
# student app's requirements.txt so the two have independent dep
# versions and security update cadences.
COPY admin/requirements.txt ./admin-requirements.txt
RUN pip install --upgrade pip \
 && pip install -r admin-requirements.txt

# Admin is fully standalone — no `from padhai import …` anywhere.
# When admin moves to its own repository, this Dockerfile + admin/
# directory transfer as a unit with no source changes.
COPY admin  ./admin

EXPOSE 8001

# Render/Fly/Cloud Run inject $PORT; default to 8001 for local docker run.
CMD ["sh", "-c", "uvicorn admin.app:app --host 0.0.0.0 --port ${PORT:-8001}"]
