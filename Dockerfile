# PadhAI deploy image
#
# Renders need system-level binaries: ffmpeg (encode/composite), fontconfig
# + Noto Indic fonts (for non-Latin scripts), and the Piper TTS binary
# (offline neural TTS). Everything below is buildable on any
# Docker-capable platform: Render, Fly.io, AWS Fargate, GCP Cloud Run, etc.
#
# Build:
#   docker build -t padhai .
#
# Run locally:
#   docker run -p 8000:8000 -e ANTHROPIC_API_KEY=... padhai

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PADHAI_CACHE_DIR=/var/padhai/cache \
    PIPER_MODEL=/opt/piper/en-us-amy-low.onnx \
    PIPER_MODEL_HI=/opt/piper/hi_IN-pratham-medium.onnx \
    PIPER_MODEL_TA=/opt/piper/ta-mahendran-low.onnx \
    PIPER_MODEL_KN=/opt/piper/kn-jaya-medium.onnx

WORKDIR /app

# System dependencies. fonts-noto + fonts-indic give us Devanagari / Tamil
# / Telugu / Kannada / etc. glyphs for narration scripts in those languages.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ffmpeg \
        fontconfig \
        fonts-noto-core \
        fonts-noto-cjk \
        fonts-indic \
        curl \
        ca-certificates \
        # LibreOffice headless for PPTX/DOCX → PDF → page-image ingest
        # (used by padhai/ingest.py _pptx_to_images / _docx_to_images).
        # Adds ~400MB to the image but unlocks the PRD §5.1 P1 input types.
        libreoffice-impress \
        libreoffice-writer \
        # psycopg[binary] bundles libpq, so we don't need libpq-dev here
 && rm -rf /var/lib/apt/lists/*

# Piper TTS voice models — soft female English (M1 default) plus Indic
# voices for hi/ta/kn. All come from rhasspy/piper-voices on HuggingFace
# (no auth required, ~60 MB each). Adding more languages later is just
# adding another curl line + PIPER_MODEL_<LANG> env var above.
#
# Why bake these into the image vs. downloading at runtime:
#   - First-render latency: no 60MB download on the hot path.
#   - Render's persistent disk fills slowly without this; baked images
#     start cold-warm.
#   - Sandboxed sub-environments (CI, eval pipelines) may not have HF
#     egress — image bundling makes the deploy self-contained.
RUN mkdir -p /opt/piper /var/padhai/cache \
 && curl -L --fail \
        "https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-en-us-amy-low.tar.gz" \
        -o /tmp/en.tar.gz \
 && tar -xzf /tmp/en.tar.gz -C /opt/piper \
 && rm /tmp/en.tar.gz \
 # Hindi: pratham medium (male, calm, ~63 MB)
 && curl -L --fail \
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/hi/hi_IN/pratham/medium/hi_IN-pratham-medium.onnx" \
        -o /opt/piper/hi_IN-pratham-medium.onnx \
 && curl -L --fail \
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/hi/hi_IN/pratham/medium/hi_IN-pratham-medium.onnx.json" \
        -o /opt/piper/hi_IN-pratham-medium.onnx.json \
 # Tamil: mahendran low (male, ~30 MB)
 && curl -L --fail \
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/ta/ta_IN/mahendran/low/ta_IN-mahendran-low.onnx" \
        -o /opt/piper/ta-mahendran-low.onnx \
 && curl -L --fail \
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/ta/ta_IN/mahendran/low/ta_IN-mahendran-low.onnx.json" \
        -o /opt/piper/ta-mahendran-low.onnx.json \
 # Kannada: jaya medium (female, ~63 MB)
 && curl -L --fail \
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/kn/kn_IN/jaya/medium/kn_IN-jaya-medium.onnx" \
        -o /opt/piper/kn-jaya-medium.onnx \
 && curl -L --fail \
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/kn/kn_IN/jaya/medium/kn_IN-jaya-medium.onnx.json" \
        -o /opt/piper/kn-jaya-medium.onnx.json

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install -r requirements.txt

COPY padhai ./padhai
# `samples/` lives outside the container — *.mp4 files in the repo
# are gitignored (they're demo artifacts generated locally or hosted
# on R2 / CDN for the marketing page). Don't try to bundle them here.

EXPOSE 8000

# Render / Fly / Cloud Run all inject $PORT; default to 8000 for local docker run.
CMD ["sh", "-c", "exec gunicorn padhai.web:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers ${WEB_CONCURRENCY:-2} \
    --timeout 120 \
    --bind 0.0.0.0:${PORT:-8000} \
    --preload \
    --access-logfile - \
    --error-logfile -"]
