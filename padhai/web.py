"""FastAPI service that exposes the PadhAI pipeline over HTTP.

Endpoints:
    GET  /              service metadata
    GET  /health        readiness probe
    GET  /tiers         supported language/level/theme enums + active provider

    POST /lessons       multipart upload (image + form fields) →
                        cache hit: 200 with MP4 stream
                        cache miss: 202 with {job_id, status_url, video_url}

    GET  /jobs/{id}     job status (queued|running|succeeded|failed + result/error)
    GET  /jobs/{id}/video  download the rendered MP4 once the job has succeeded

Why async: real renders take 1-3 min (cartoon) to 10+ min (Wav2Lip /
hosted M4). Render's free / starter HTTP timeout is ~60s, so /lessons
must return immediately and clients poll for completion.

The video-cache short-circuit (padhai/cache.py) is checked synchronously
before any job is created. Popular pages cost nothing and return in <10ms."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

# Load .env from the project root before any os.environ reads. Tests can
# disable this so a local developer DATABASE_URL does not leak into
# SQLite-only smoke runs.
if os.environ.get("PADHAI_SKIP_DOTENV", "0") not in ("1", "true", "yes"):
    try:
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(
            Path(__file__).resolve().parent.parent / ".env",
            override=False,
        ) or _load_dotenv(override=False)  # fallback: auto-discover from CWD
    except ImportError:
        pass

import logging
import re as _re

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

_log = logging.getLogger("padhai.web")

from .auth import (
    AuthUser,
    PostgresUserRepository,
    SQLiteUserRepository,
    hash_password,
    issue_token,
    make_current_user_dependency,
    resolve_provider_for_tier,
    verify_password,
)
from .cache import Cache
from .ingest import ingest as ingest_source
from .db import PostgresJobStore, get_db_url, use_postgres
from .jobs import Job, JobRunner, JobStore
from .pedagogy import BOARD_GUIDANCE, LEVEL_GUIDANCE, SUPPORTED_LANGUAGES, generate_lesson
from .personalization import (
    OUTPUT_DIMENSIONS,
    USER_TYPE_TONE,
    VIDEO_MODE_TEMPLATES,
    PersonalizationProfile,
    apply_regenerate,
    build_profile,
    detect_sensitive_domain,
)
from . import moderation as _moderation
from . import dpdp as _dpdp
from . import sso as _sso
from . import uploads as _uploads
from . import observability as _obs
from . import schema_v2 as _schema_v2
from . import branding as _branding
from . import audit as _audit
from . import cdn as _cdn
from . import db_backend as _db_backend
from . import push as _push
from . import queue_backend as _queue_backend
from . import saml as _saml
from . import scim as _scim
from . import residency as _residency
from . import streaks as _streaks
from . import math_render as _math_render
from . import diagram_generator as _dgen
from . import custom_domains as _customdom
from . import soc2 as _soc2
from . import region as _region
from . import question_bank as _qbank
from . import curriculum_scorer as _scorer
from . import voice_sarvam as _sarvam
from . import indic_polish as _indic
from . import countries as _countries
from . import coaching as _coaching
from . import mastery as _mastery
from . import preschool as _preschool
from . import procurement as _procurement
from . import rate_limit as _rl
from . import feature_flags as _flags
from . import llm_obs as _llm_obs
from . import tutor as _tutor
from . import llm_cache as _llm_cache
from . import essay_grader as _essay
from . import practice_test as _practice
from . import live_classes as _live
from . import doubt_clearing as _doubt
from . import analytics as _analytics
from . import math_vision as _math_vision
from . import mock_interview as _mock_iv
from . import mock_test_events as _mock_te
from . import forums as _forums
from . import family_plans as _family
from . import study_buddies as _buddies
from . import teacher_publishing as _pub
from . import content_market as _cmkt
from . import mentorship as _mentor
from . import nep_alignment as _nep
from . import diksha as _diksha
from . import customer_success as _cs
from . import state_partnerships as _states
from . import corporate as _corp
from . import sales_pipeline as _sales
from . import tutor_marketplace as _tmkt
from . import question_pack_market as _qpmkt
from . import vouchers as _vouchers
from . import university_partners as _univ
from . import affiliates as _affiliates
from . import digilocker as _digilocker
from . import citations as _citations
from . import exam_taxonomy as _exam_tax
from . import accuracy_bench as _accbench
from . import mock_engine as _mock_eng
from . import readiness as _readiness
from . import tutor_grounding as _tutor_grd
from . import retrieval as _retrieval
from . import daily_plan as _daily_plan
from . import moderation_queue as _modq
from . import dashboards as _dashboards
from . import expert_review as _expert_review
from . import spaced_repetition as _srs
from . import socratic_tutor as _socratic
from . import research_tools as _research
from . import marketplace_quality as _mq
from . import offline_packs as _offline
from . import messaging as _messaging
from . import audio_recap as _audio_recap
from . import adaptive_packs as _adaptive
from . import step_math as _step_math
from . import navigation as _navigation
from . import student_home as _student_home
from . import home_ui as _home_ui


# v2.0.1 — size caps on user-supplied input for public preview
# endpoints. Picked well above realistic lesson use (a quadratic
# formula is ~60 chars; a Mermaid graph for a water cycle is ~500).
# Anything bigger is a bug or an abuse attempt; reject before
# allocating render time.
_MATH_LATEX_MAX = 2000
_DIAGRAM_SPEC_MAX = 8000
_LESSON_TEXT_MAX = 20000
from .render import render_lesson
from .storage import LocalDiskStorage, get_storage
from .talking_head import get_provider as get_talking_head_provider
from .themes import REGISTRY as THEME_REGISTRY, theme_for_level


# ---- module-level singletons ----

cache = Cache()

_OUTPUT_DIR = Path(
    os.environ.get(
        "PADHAI_OUTPUT_DIR",
        str(Path.home() / ".padhai" / "outputs"),
    )
)
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
if "PADHAI_DB_PATH" not in os.environ and "PADHAI_OUTPUT_DIR" in os.environ:
    os.environ["PADHAI_DB_PATH"] = str(_OUTPUT_DIR / "padhai.sqlite3")
_DB_PATH = Path(
    os.environ.get(
        "PADHAI_DB_PATH",
        str(Path.home() / ".padhai" / "jobs.db"),
    )
)
_WORKERS = int(os.environ.get("PADHAI_WORKERS", "1"))

def _build_job_store():
    db_url = get_db_url()
    if db_url:
        return PostgresJobStore(db_url)
    return JobStore(_DB_PATH)


store = _build_job_store()
object_storage = get_storage()

# Auth is only wired when DATABASE_URL is configured (Postgres) — the
# users table lives there. In SQLite dev mode the dependency returns
# None and POST /lessons treats every request as anonymous (M1 tier).
_pg_store = store if isinstance(store, PostgresJobStore) else None
_user_repo = None
_repo_lock = threading.Lock()


def _get_user_repo():
    """Lazy getter for the user repo.

    - When DATABASE_URL is set: returns PostgresUserRepository.
    - When DATABASE_URL is absent: falls back to SQLiteUserRepository
      so that signup/login work in a fresh dev checkout without Postgres.

    Protected by _repo_lock to prevent concurrent requests from each
    creating their own PostgresJobStore and leaking connection pools."""
    global _user_repo, _pg_store
    if _user_repo is not None:
        return _user_repo
    with _repo_lock:
        if _user_repo is not None:  # re-check inside lock
            return _user_repo
        db_url = os.environ.get("DATABASE_URL")
        _log.debug("_get_user_repo: DATABASE_URL=%s", "SET" if db_url else "MISSING")
        if not db_url:
            # Dev / single-server mode: SQLite-backed auth
            _user_repo = SQLiteUserRepository("padhai.db")
            _log.info("_get_user_repo: using SQLiteUserRepository (no DATABASE_URL)")
            return _user_repo
        try:
            if _pg_store is None:
                _pg_store = PostgresJobStore(db_url)
            _user_repo = PostgresUserRepository(_pg_store.pool)
            _log.info("_get_user_repo: initialised PostgresUserRepository ok")
        except Exception as e:
            _log.error("_get_user_repo: FAILED: %s", e)
            return None
        return _user_repo


current_user = make_current_user_dependency(_get_user_repo)


def _video_storage_key(image_bytes: bytes, language: str, level: str,
                       theme_name: str, provider_name: str,
                       render_mode: str) -> str:
    """Deterministic R2/S3 key for the rendered MP4. Identical to the
    cache key used by padhai/cache.py so a video uploaded once can be
    located by any other process holding the same inputs."""
    import hashlib
    h = hashlib.sha256()
    for c in (image_bytes, language.encode(), level.encode(),
              theme_name.encode(), provider_name.encode(),
              render_mode.encode()):
        h.update(c)
        h.update(b"\x00")
    return f"videos/{h.hexdigest()}.mp4"


def _render_explainer_video(job: Job) -> dict:
    """Worker branch for topic-based explainer videos. No image input —
    we already have the Explainer JSON in the payload (the /explain
    endpoint cached it). Convert to a Lesson, run the same render
    pipeline, surface a stable `lesson_id` so flashcards / recap / chat
    can all key off the same explainer afterwards."""
    from .pedagogy import explainer_to_lesson, MODEL
    import json as _json

    p = job.payload
    language = p["language"]
    level = p["level"]
    topic = p["topic"]
    explainer = p["explainer"]
    theme_override = p.get("theme")
    teacher = p.get("teacher", True)
    render_mode = p.get("render_mode", "animated")

    # Default explainer videos to BINOCS (bright, cheerful, kid-friendly
    # like the Dr. Binocs YouTube show), unless the caller asked for
    # something else or this is a NEET/JEE-level explainer where the
    # cleaner whiteboard theme reads better.
    if theme_override:
        chosen_theme = theme_for_level(level, theme_override)
    elif level == "neet_jee":
        chosen_theme = theme_for_level(level)
    else:
        chosen_theme = THEME_REGISTRY["binocs"]

    provider = get_talking_head_provider() if teacher else None
    provider_name = provider.name if provider else "none"

    out_path = _OUTPUT_DIR / f"{job.id}.mp4"

    profile_dict = p.get("profile_json")
    dimensions = tuple(profile_dict.get("output_dimensions", [1280, 720])) \
        if profile_dict else (1280, 720)

    # Synthetic image_bytes so the existing cache keys work
    synthetic_bytes = _json.dumps(
        {"topic": topic, "language": language, "level": level},
        sort_keys=True,
    ).encode("utf-8")
    lesson_id = cache.lesson_key(synthetic_bytes, language, level, MODEL)

    storage_key = _video_storage_key(
        synthetic_bytes, language, level,
        chosen_theme.name, provider_name, render_mode,
    )
    store.set_progress(job.id, "analyzing_document", 5)

    if object_storage.exists(storage_key):
        store.set_progress(job.id, "complete", 100)
        return {
            "video_url": object_storage.url(storage_key),
            "storage_key": storage_key,
            "lesson_id": lesson_id,
            "cache_hit": True,
            "source": "explainer",
            "topic": topic,
        }
    if cache.get_video(
        synthetic_bytes, language, level,
        chosen_theme.name, provider_name, render_mode,
        out_path,
    ):
        url = object_storage.put(storage_key, out_path)
        store.set_progress(job.id, "complete", 100)
        return {
            "video_url": url,
            "storage_key": storage_key,
            "lesson_id": lesson_id,
            "cache_hit": True,
            "source": "explainer",
            "topic": topic,
        }

    store.set_progress(job.id, "creating_script", 20)
    lesson = explainer_to_lesson(explainer, language, level)
    cache.put_lesson(synthetic_bytes, language, level, MODEL, lesson)

    store.set_progress(job.id, "creating_storyboard", 35)
    store.set_progress(job.id, "generating_voice", 50)
    store.set_progress(job.id, "rendering_video", 70)
    render_lesson(
        lesson, out_path,
        cache=cache,
        theme=chosen_theme,
        render_mode=render_mode,
        show_teacher=teacher,
        include_quiz=False,
        talking_head_provider=provider,
        dimensions=dimensions,
    )
    store.set_progress(job.id, "uploading", 92)
    cache.put_video(
        synthetic_bytes, language, level,
        chosen_theme.name, provider_name, render_mode,
        out_path,
    )
    url = object_storage.put(storage_key, out_path)
    store.set_progress(job.id, "complete", 100)
    return {
        "video_url": url,
        "storage_key": storage_key,
        "lesson_id": lesson_id,
        "cache_hit": False,
        "source": "explainer",
        "topic": topic,
    }


def _render_worker(job: Job) -> dict:
    """Run inside the worker thread. Returns {"output_path": "...",
    "cache_hit": bool} to be stored as the job's result."""
    p = job.payload
    if p.get("kind") == "explainer":
        return _render_explainer_video(job)
    image_path = Path(p["image_path"])
    language = p["language"]
    level = p["level"]
    theme_override = p.get("theme")
    teacher = p.get("teacher", True)
    include_quiz = p.get("include_quiz", True)
    render_mode = p.get("render_mode", "animated")
    board_hint = p.get("board_hint")  # injected by create_lesson when board/exam supplied
    profile_dict = p.get("profile_json")  # v2 path — present when via /api/v2
    dimensions = tuple(profile_dict.get("output_dimensions", [1280, 720])) \
        if profile_dict else (1280, 720)
    target_duration = profile_dict.get("duration_seconds") if profile_dict else None
    profile_addendum = profile_dict.get("prompt_addendum") if profile_dict else None

    chosen_theme = theme_for_level(level, theme_override)
    provider = get_talking_head_provider() if teacher else None
    provider_name = provider.name if provider else "none"

    out_path = _OUTPUT_DIR / f"{job.id}.mp4"
    image_bytes = image_path.read_bytes()

    storage_key = _video_storage_key(
        image_bytes, language, level,
        chosen_theme.name, provider_name, render_mode,
    )
    from .pedagogy import MODEL
    lesson_id = cache.lesson_key(image_bytes, language, level, MODEL)

    store.set_progress(job.id, "analyzing_document", 5)

    if object_storage.exists(storage_key):
        image_path.unlink(missing_ok=True)
        store.set_progress(job.id, "complete", 100)
        return {
            "video_url": object_storage.url(storage_key),
            "storage_key": storage_key,
            "lesson_id": lesson_id,
            "cache_hit": True,
        }

    if cache.get_video(
        image_bytes, language, level,
        chosen_theme.name, provider_name, render_mode,
        out_path,
    ):
        url = object_storage.put(storage_key, out_path)
        image_path.unlink(missing_ok=True)
        store.set_progress(job.id, "complete", 100)
        return {
            "video_url": url,
            "storage_key": storage_key,
            "lesson_id": lesson_id,
            "cache_hit": True,
        }

    try:
        store.set_progress(job.id, "understanding_topic", 15)
        store.set_progress(job.id, "creating_script", 25)
        lesson = generate_lesson(
            image_path, language, level, cache=cache,
            target_duration_seconds=target_duration,
            profile_addendum=profile_addendum,
            video_mode=(profile_dict.get("video_mode", "teaching")
                       if profile_dict else "teaching"),
            board_hint=board_hint,
            user_id=p.get("user_id"),
            source_upload_id=p.get("upload_id"),
            source_page_number=p.get("page_number"),
        )
        store.set_progress(job.id, "creating_storyboard", 40)
        store.set_progress(job.id, "generating_voice", 55)
        store.set_progress(job.id, "rendering_video", 70)
        render_lesson(
            lesson, out_path,
            cache=cache,
            theme=chosen_theme,
            render_mode=render_mode,
            show_teacher=teacher,
            include_quiz=include_quiz,
            talking_head_provider=provider,
            dimensions=dimensions,
        )
        if include_quiz:
            store.set_progress(job.id, "preparing_quiz", 85)
        store.set_progress(job.id, "uploading", 92)
        cache.put_video(
            image_bytes, language, level,
            chosen_theme.name, provider_name, render_mode,
            out_path,
        )
        url = object_storage.put(storage_key, out_path)
        store.set_progress(job.id, "complete", 100)
        return {
            "video_url": url,
            "storage_key": storage_key,
            "lesson_id": lesson_id,
            "cache_hit": False,
        }
    finally:
        image_path.unlink(missing_ok=True)


def _web_handles_payload(payload: dict) -> bool:
    """The web service's in-process pool runs cartoon + hosted-API jobs.
    GPU-bound Wav2Lip jobs are left queued for the GPU worker
    (padhai/gpu_worker.py) running on a Spot instance to claim."""
    return payload.get("talking_head_provider") != "wav2lip"


runner = JobRunner(
    store,
    worker_fn=_render_worker,
    max_workers=_WORKERS,
    job_filter=_web_handles_payload,
)


# ----------------------------------------------------------------------------
# Provider key validation — called from the lifespan startup hook.
# ----------------------------------------------------------------------------

# Each entry: env var → (prefix or None, min_len, label)
# `prefix` is checked only when set; some keys (Bhashini, Razorpay) have no
# stable prefix so we just enforce a minimum length.
_PROVIDER_KEY_SPECS = {
    "ANTHROPIC_API_KEY":   ("sk-ant-", 32, "Anthropic / Claude"),
    "HEYGEN_API_KEY":      (None,      24, "HeyGen avatar"),
    "DID_API_KEY":         (None,      24, "D-ID avatar"),
    "TAVUS_API_KEY":       (None,      24, "Tavus avatar"),
    "SYNTHESIA_API_KEY":   (None,      24, "Synthesia avatar"),
    "ELEVENLABS_API_KEY":  (None,      24, "ElevenLabs TTS"),
    "SARVAM_API_KEY":      (None,      16, "Sarvam.ai Indic TTS"),
    "BHASHINI_API_KEY":    (None,      16, "Bhashini Indic TTS"),
    "OPENAI_API_KEY":      ("sk-",     32, "OpenAI"),
    "RAZORPAY_KEY_ID":     ("rzp_",    16, "Razorpay key_id"),
    "RAZORPAY_KEY_SECRET": (None,      16, "Razorpay key_secret"),
    "MSG91_AUTH_KEY":      (None,      16, "MSG91 SMS"),
    "GUPSHUP_API_KEY":     (None,      16, "Gupshup SMS/WhatsApp"),
    "TWILIO_ACCOUNT_SID":  ("AC",      32, "Twilio account SID"),
    "LIVEKIT_API_KEY":     (None,      8,  "LiveKit live video"),
    "DAILY_API_KEY":       (None,      24, "Daily.co live video"),
    "S3_ACCESS_KEY_ID":    (None,      8,  "S3 / Cloudflare R2"),
}


def _validate_provider_keys() -> None:
    """Check each configured provider key has the expected prefix + length.
    In production (APP_ENV=production) we raise RuntimeError to fail-fast.
    In dev we emit a warning and keep going so local work isn't blocked."""
    is_prod = (os.environ.get("APP_ENV") or "").strip().lower() == "production"
    problems: list[str] = []
    for env_var, (prefix, min_len, label) in _PROVIDER_KEY_SPECS.items():
        raw = (os.environ.get(env_var) or "").strip()
        if not raw:
            continue   # unset is fine — feature just disabled
        if len(raw) < min_len:
            problems.append(
                f"{env_var} ({label}): too short ({len(raw)} chars, "
                f"expected ≥{min_len})"
            )
            continue
        if prefix and not raw.startswith(prefix):
            problems.append(
                f"{env_var} ({label}): expected prefix {prefix!r}, "
                f"got {raw[:8]!r}…"
            )
            continue
        # Placeholder detection — common copy-paste mistakes
        if any(tok in raw.lower() for tok in (
            "placeholder", "change-me", "change_me", "xxxxx",
            "your-key", "your_api_key",
        )):
            problems.append(
                f"{env_var} ({label}): looks like a placeholder value"
            )
    if not problems:
        _log.info("[startup] provider keys: all configured keys validated OK")
        return
    msg = "Provider key validation failed:\n  - " + "\n  - ".join(problems)
    if is_prod:
        _log.error("[startup] %s", msg)
        raise RuntimeError(msg)
    else:
        _log.warning("[startup] %s\n(dev mode — continuing anyway)", msg)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Initialise Postgres store + user repo here (not at import time) so
    # that DATABASE_URL loaded from .env via load_dotenv is guaranteed to
    # be visible before we check use_postgres().
    global _pg_store, _user_repo
    _db_url = os.environ.get("DATABASE_URL", "")
    if _db_url:
        try:
            if _pg_store is None:
                _pg_store = PostgresJobStore(_db_url)
            _user_repo = PostgresUserRepository(_pg_store.pool)
            _log.info("postgres connected: %s", _db_url.split("@")[-1])
        except Exception as e:  # noqa: BLE001
            _log.error("postgres FAILED — auth will be unavailable: %s", e)
    else:
        _log.warning("DATABASE_URL not set — running in SQLite/anonymous mode")

    # Warn if APP_BASE_URL is unset or still localhost in a production-like env
    _base_url = os.environ.get("APP_BASE_URL", "")
    if not _base_url:
        _log.warning(
            "APP_BASE_URL is not set — password-reset emails will contain "
            "http://localhost:8000 links. Set APP_BASE_URL to your public URL."
        )
    elif "localhost" in _base_url and _db_url:
        _log.warning("APP_BASE_URL=%s looks like dev but DATABASE_URL is set", _base_url)

    # Apply additive schema migrations that don't belong to JobStore
    # (DPDP consent columns + notifications tables). Idempotent — safe
    # to call on every boot.
    if use_postgres():
        _liquibase_ran = False
        _project_root = Path(__file__).resolve().parent.parent
        _lbprops = _project_root / "db" / "liquibase.properties"
        if _lbprops.exists() and os.environ.get("PADHAI_USE_LIQUIBASE") == "1":
            try:
                import subprocess as _sp
                _r = _sp.run(
                    ["liquibase",
                     f"--defaultsFile={_lbprops}",
                     "update"],
                    cwd=str(_project_root),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if _r.returncode == 0:
                    _log.info("[startup] liquibase: changesets applied")
                    _liquibase_ran = True
                else:
                    _log.error("[startup] liquibase failed (rc=%d): %s",
                               _r.returncode, _r.stderr[:300])
            except FileNotFoundError:
                _log.warning("[startup] liquibase not in PATH — falling back to init_schema()")
            except Exception as e:  # noqa: BLE001
                _log.warning("[startup] liquibase error (non-fatal): %s", e)
        if not _liquibase_ran:
            try:
                _pg_store.init_schema()
                _log.info("[startup] postgres base schema applied (Python fallback)")
            except Exception as e:
                _log.critical(
                    "[startup] init_schema FAILED — cannot start with a broken schema: %s", e
                )
                raise RuntimeError(
                    f"Database schema initialisation failed: {e}. "
                    "Fix the schema or DATABASE_URL and redeploy."
                ) from e
    try:
        _orgs.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] orgs.migrate failed (non-fatal): %s", e)
    try:
        _dpdp.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] dpdp.migrate failed (non-fatal): %s", e)
    try:
        _sso.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] sso.migrate failed (non-fatal): %s", e)
    try:
        _schema_v2.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] schema_v2.migrate failed (non-fatal): %s", e)
    try:
        _branding.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] branding.migrate failed (non-fatal): %s", e)
    try:
        _audit.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] audit.migrate failed (non-fatal): %s", e)
    try:
        _push.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] push.migrate failed (non-fatal): %s", e)
    try:
        _saml.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] saml.migrate failed (non-fatal): %s", e)
    try:
        _scim.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] scim.migrate failed (non-fatal): %s", e)
    try:
        _residency.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] residency.migrate failed (non-fatal): %s", e)
    try:
        _streaks.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] streaks.migrate failed (non-fatal): %s", e)
    try:
        _customdom.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] custom_domains.migrate failed (non-fatal): %s", e)
    try:
        _qbank.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] question_bank.migrate failed (non-fatal): %s", e)
    try:
        _scorer.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] curriculum_scorer.migrate failed (non-fatal): %s", e)
    try:
        _countries.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] countries.migrate failed (non-fatal): %s", e)
    try:
        _coaching.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] coaching.migrate failed (non-fatal): %s", e)
    try:
        _mastery.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] mastery.migrate failed (non-fatal): %s", e)
    try:
        _preschool.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] preschool.migrate failed (non-fatal): %s", e)
    try:
        _flags.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] feature_flags.migrate failed (non-fatal): %s", e)
    try:
        _llm_obs.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] llm_obs.migrate failed (non-fatal): %s", e)
    try:
        _tutor.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] tutor.migrate failed (non-fatal): %s", e)
    try:
        _essay.migrate()
        seeded = _essay.seed_default_rubrics()
        if seeded:
            _log.info("[startup] essay_grader: seeded %d default rubrics", seeded)
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] essay_grader.migrate/seed failed (non-fatal): %s", e)
    try:
        _practice.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] practice_test.migrate failed (non-fatal): %s", e)
    try:
        _live.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] live_classes.migrate failed (non-fatal): %s", e)
    try:
        _doubt.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] doubt_clearing.migrate failed (non-fatal): %s", e)
    try:
        _analytics.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] analytics.migrate failed (non-fatal): %s", e)
    try:
        _math_vision.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] math_vision.migrate failed (non-fatal): %s", e)
    try:
        _mock_iv.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] mock_interview.migrate failed (non-fatal): %s", e)
    try:
        _mock_te.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] mock_test_events.migrate failed (non-fatal): %s", e)
    try:
        _forums.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] forums.migrate failed (non-fatal): %s", e)
    try:
        _family.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] family_plans.migrate failed (non-fatal): %s", e)
    try:
        _buddies.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] study_buddies.migrate failed (non-fatal): %s", e)
    try:
        _pub.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] teacher_publishing.migrate failed (non-fatal): %s", e)
    try:
        _cmkt.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] content_market.migrate failed (non-fatal): %s", e)
    try:
        _mentor.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] mentorship.migrate failed (non-fatal): %s", e)
    try:
        _nep.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] nep_alignment.migrate failed (non-fatal): %s", e)
    try:
        _diksha.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] diksha.migrate failed (non-fatal): %s", e)
    try:
        _cs.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] customer_success.migrate failed (non-fatal): %s", e)
    try:
        _states.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] state_partnerships.migrate failed (non-fatal): %s", e)
    try:
        _corp.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] corporate.migrate failed (non-fatal): %s", e)
    try:
        _sales.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] sales_pipeline.migrate failed (non-fatal): %s", e)
    try:
        _tmkt.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] tutor_marketplace.migrate failed (non-fatal): %s", e)
    try:
        _qpmkt.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] question_pack_market.migrate failed (non-fatal): %s", e)
    try:
        _vouchers.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] vouchers.migrate failed (non-fatal): %s", e)
    try:
        _univ.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] university_partners.migrate failed (non-fatal): %s", e)
    try:
        _affiliates.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] affiliates.migrate failed (non-fatal): %s", e)
    try:
        _digilocker.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] digilocker.migrate failed (non-fatal): %s", e)
    try:
        _citations.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] citations.migrate failed (non-fatal): %s", e)
    try:
        _exam_tax.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] exam_taxonomy.migrate failed (non-fatal): %s", e)
    try:
        _accbench.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] accuracy_bench.migrate failed (non-fatal): %s", e)
    try:
        _mock_eng.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] mock_engine.migrate failed (non-fatal): %s", e)
    try:
        _readiness.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] readiness.migrate failed (non-fatal): %s", e)
    try:
        _tutor_grd.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] tutor_grounding.migrate failed (non-fatal): %s", e)
    try:
        _retrieval.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] retrieval.migrate failed (non-fatal): %s", e)
    try:
        _daily_plan.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] daily_plan.migrate failed (non-fatal): %s", e)
    try:
        _modq.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] moderation_queue.migrate failed (non-fatal): %s", e)
    try:
        _dashboards.migrate()    # no-op, kept for symmetry
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] dashboards.migrate failed (non-fatal): %s", e)
    try:
        _expert_review.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] expert_review.migrate failed (non-fatal): %s", e)
    try:
        _srs.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] spaced_repetition.migrate failed (non-fatal): %s", e)
    try:
        _socratic.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] socratic_tutor.migrate failed (non-fatal): %s", e)
    try:
        _research.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] research_tools.migrate failed (non-fatal): %s", e)
    try:
        _mq.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] marketplace_quality.migrate failed (non-fatal): %s", e)
    try:
        _offline.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] offline_packs.migrate failed (non-fatal): %s", e)
    try:
        _messaging.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] messaging.migrate failed (non-fatal): %s", e)
    try:
        _audio_recap.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] audio_recap.migrate failed (non-fatal): %s", e)
    try:
        _adaptive.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] adaptive_packs.migrate failed (non-fatal): %s", e)
    try:
        from . import cwv as _cwv
        _cwv.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] cwv.migrate failed (non-fatal): %s", e)
    try:
        _step_math.migrate()
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] step_math.migrate failed (non-fatal): %s", e)
    try:
        _navigation.migrate()    # no-op
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] navigation.migrate failed (non-fatal): %s", e)
    try:
        _student_home.migrate()    # no-op
    except Exception as e:  # noqa: BLE001
        _log.warning("[startup] student_home.migrate failed (non-fatal): %s", e)
    _log.info("[startup] live video: %s", _live.active_provider())
    _log.info("[startup] tutor: %s", "available" if _tutor.is_available() else "not configured")
    _llm_cache_desc = _llm_cache.describe()
    _log.info(
        "[startup] llm cache: caching=%s batch=%s",
        _llm_cache_desc['caching_enabled'],
        _llm_cache_desc['batch_enabled'],
    )
    _log.info("[startup] region: %s", _region.description())
    _log.info("[startup] db backend: %s", _db_backend.description())
    _log.info("[startup] queue backend: %s", _queue_backend.description())
    if _cdn.is_configured():
        _log.info("[startup] cdn: signed-url delivery enabled")

    # v3.x — validate optional provider keys' format before serving
    # traffic. In APP_ENV=production we fail-fast on malformed keys
    # so we don't run with a misconfigured deployment. In dev we just
    # warn so local work isn't blocked.
    _validate_provider_keys()

    resumed = runner.resume_pending()
    if resumed:
        _log.info("[startup] resumed %d pending jobs from %s", resumed, _DB_PATH)
    yield
    # Give in-flight jobs up to 30 s to finish before hard-killing the pool.
    # wait=True prevents Render's SIGTERM from stranding half-rendered videos.
    _log.info("[shutdown] waiting up to 30 s for in-flight jobs to complete...")
    runner.shutdown(wait=True)
    if _pg_store is not None:
        _pg_store.close()


app = FastAPI(
    title="AI Pathshala",
    description="Scan a textbook page, get a video lesson in your language.",
    version="0.4.0",
    lifespan=_lifespan,
)

# v2.0.2 — extracted routers. Each handles one subsystem; web.py
# itself keeps the auth-gated + tightly-coupled endpoints. New routers
# are added to padhai/routers/__init__.py._ROUTER_NAMES.
from . import routers as _routers  # noqa: E402  -- after app creation
for _r in _routers.all_routers():
    app.include_router(_r)

# v0.13 F2: structured logging + in-memory request metrics + Sentry +
# PostHog (latter two no-op when env vars unset). Install BEFORE other
# middlewares so it sees the un-modified request shape.
_obs.install(app)

# Content-Security-Policy — blocks inline XSS from untrusted content
# rendered in teacher/student UIs (quiz answers, doubt text, etc.).
# 'unsafe-inline' is kept for now because the SPA uses inline scripts;
# migrate to nonces in a future refactor to tighten this further.
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
class _CSPMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        ct = response.headers.get("content-type", "")
        # nosniff on every response — prevents IE/Edge content-type sniffing
        # on JSON endpoints that echo user input.
        response.headers["X-Content-Type-Options"] = "nosniff"
        if "text/html" in ct:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' data: blob: https:; "
                "media-src 'self' blob: https:; "
                "connect-src 'self'; "
                "frame-ancestors 'none';"
            )
            response.headers["X-Frame-Options"] = "DENY"
        return response
app.add_middleware(_CSPMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1024)

# CORS — allow the configured frontend origin(s) plus localhost for dev.
# Set CORS_ORIGINS in env as a comma-separated list of allowed origins.
# Default allows all origins in dev; in production set to the exact SPA origin.
# IMPORTANT: allow_credentials=True is incompatible with allow_origins=["*"]
# per the CORS spec — browsers reject that combination. We only send
# Allow-Credentials when explicit origins are configured.
_cors_raw = os.environ.get("CORS_ORIGINS", "")
_cors_origins: list[str] = (
    [o.strip() for o in _cors_raw.split(",") if o.strip()]
    if _cors_raw
    else ["*"]
)
_cors_credentials = _cors_origins != ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)


# ---------------------------------------------------------------------------
# Browser-friendly error handler
# ---------------------------------------------------------------------------
# When a user types an API URL directly into the browser address bar (no
# Authorization header, Accept: text/html), they see raw JSON like
# {"detail": "sign in to manage an organisation"}.  This is confusing,
# especially when the user IS signed in on the SPA (token in localStorage).
#
# Fix: if the request looks like a browser navigation (accepts text/html,
# is a GET, hits a non-UI path), redirect to the SPA root so the frontend
# can pick up the localStorage token and retry the request properly.
# API clients (curl, fetch, mobile) send Accept: application/json so they
# are unaffected and continue to receive JSON error bodies.
from fastapi.exceptions import HTTPException as _HTTPException  # noqa: E402
from fastapi.responses import RedirectResponse as _RedirectResponse  # noqa: E402

_SPA_PATHS = {"/", "/ui", "/landing", "/home", "/login", "/terms", "/privacy"}
_API_PREFIX = "/api/"
_AUTH_PREFIX = "/auth/"


@app.exception_handler(_HTTPException)
async def _browser_friendly_http_exception(request, exc: _HTTPException):
    """Redirect browsers that hit a protected API URL to the SPA so they
    can authenticate via the normal UI flow.  Non-browser clients (curl,
    fetch with Accept: application/json) still get the JSON error."""
    accept = request.headers.get("accept", "")
    is_browser_nav = (
        "text/html" in accept
        and request.method == "GET"
        and str(request.url.path) not in _SPA_PATHS
        and not str(request.url.path).startswith("/static")
    )
    # Redirect the browser to the SPA for any error that would otherwise
    # render as raw JSON.  Covers:
    #  401 — unauthenticated (no token sent by browser)
    #  403 — insufficient role
    #  503 — auth not configured (no DATABASE_URL in dev)
    _REDIRECT_STATUSES = {401, 403, 503}
    if is_browser_nav and exc.status_code in _REDIRECT_STATUSES:
        # Redirect the browser to the SPA root.  The SPA will load the
        # token from localStorage and the user can navigate normally.
        # We append ?next= so a future deep-link feature can land them
        # back at the right page after sign-in.
        from urllib.parse import quote as _q
        next_url = _q(str(request.url.path), safe="")
        return _RedirectResponse(f"/?next={next_url}", status_code=302)
    # For all other cases (non-browser, non-auth errors) return normal JSON.
    from fastapi.responses import JSONResponse as _JSONResponse  # noqa: E402
    return _JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None) or {},
    )


@app.get("/metrics")
def metrics_endpoint():
    """JSON snapshot of in-memory request metrics — uptime, per-route
    counts + latency percentiles, error rate. Read by Grafana scrape
    or human ops on a deploy.

    Public by design — the data is operational, not user PII. If you
    want to lock it down behind admin auth, gate on Depends(require_admin)."""
    return _obs.snapshot()


# Mount the standalone Admin Console at /admin/*. The admin app is
# fully self-contained (does not import from padhai.*) — this mount is
# the only line of code that ties the two together. To split admin into
# its own Render service later, delete this block + the import above
# and add a `padhai-admin` service to render.yaml pointing at
# admin/Dockerfile (which already exists for that purpose).
from admin.app import app as _admin_app
app.mount("/admin", _admin_app)


# ---- routes ----


_INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AI Pathshala — A multilingual AI teacher for every student</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- v1.0 D3: PWA hooks. Manifest is branding-aware (subdomain-resolved
     theme color + name); service worker caches the SPA shell + media. -->
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#5E60CE">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="AI Pathshala">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Noto+Sans+Devanagari:wght@400;600&family=Noto+Sans+Tamil:wght@400;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:#F4F6FB; --surface:#FFFFFF; --surface-soft:#F8FAFD;
    --ink:#0F1729; --ink-soft:#374151; --muted:#6B7280; --line:#E5E7EB;
    --brand:#5E60CE; --brand-dark:#4845B8; --brand-soft:#EEEEFD;
    --accent:#FF6B35; --accent-soft:#FFE9DD;
    --gold:#FFB627; --gold-soft:#FFF5DD;
    --good:#06D6A0; --good-soft:#DEFAF1;
    --warn:#F77F00; --warn-soft:#FFEAD3;
    --info:#118AB2; --info-soft:#D6F1FA;
    --purple:#7B2CBF; --purple-soft:#EFE0FD;
    --pink:#E63946; --pink-soft:#FDDDE1;
    --shadow-sm: 0 1px 2px rgba(15,23,41,.04), 0 1px 4px rgba(15,23,41,.04);
    --shadow:    0 4px 12px rgba(15,23,41,.06), 0 2px 4px rgba(15,23,41,.04);
    --shadow-lg: 0 12px 32px rgba(15,23,41,.10), 0 4px 12px rgba(15,23,41,.06);
    --radius: 14px;
    --radius-sm: 10px;
  }
  *,*::before,*::after { box-sizing:border-box; }
  html { font-size:15px; }
  body {
    margin:0; min-height:100vh;
    font-family:'Plus Jakarta Sans','Noto Sans Devanagari','Noto Sans Tamil',
                -apple-system,BlinkMacSystemFont,sans-serif;
    background:var(--bg); color:var(--ink);
    line-height:1.55; -webkit-font-smoothing:antialiased;
  }

  /* === Layout === */
  .app {
    display:grid;
    grid-template-columns:260px 1fr;
    grid-template-rows:auto 1fr;
    grid-template-areas: "header header" "sidebar main";
    min-height:100vh;
  }
  header {
    grid-area:header;
    background:rgba(255,255,255,0.85); backdrop-filter:saturate(180%) blur(12px);
    -webkit-backdrop-filter:saturate(180%) blur(12px);
    border-bottom:1px solid var(--line);
    padding:14px 26px;
    display:flex; align-items:center; justify-content:space-between;
    position:sticky; top:0; z-index:9;
  }
  .brand { display:flex; align-items:center; gap:12px; }
  .brand-mark {
    width:36px; height:36px; border-radius:10px;
    background:linear-gradient(135deg,var(--brand) 0%,var(--purple) 100%);
    display:flex; align-items:center; justify-content:center;
    color:#fff; font-weight:800; font-size:18px;
    box-shadow:0 6px 14px rgba(94,96,206,.35);
  }
  .brand h1 {
    margin:0; font-size:18px; font-weight:800; letter-spacing:-0.4px;
  }
  .brand .ver {
    font-size:10px; color:var(--brand); font-weight:700;
    background:var(--brand-soft); padding:2px 7px; border-radius:999px;
    margin-left:6px; letter-spacing:0.4px;
  }
  .brand .tag {
    font-size:12px; color:var(--muted); margin:0; font-weight:500;
  }
  .auth-corner { font-size:14px; }

  /* === Notifications bell + drawer (v0.11.0 E2) === */
  .auth-corner {
    display:flex; align-items:center; gap:10px;
  }
  .notif-bell {
    position:relative; background:none; border:0; padding:6px 10px;
    cursor:pointer; font-size:18px; border-radius:8px;
    transition:background .15s ease;
  }
  .notif-bell:hover { background:var(--surface-soft); }
  .notif-bell .bell-icon { display:inline-block; }
  .notif-badge {
    position:absolute; top:0; right:0;
    background:var(--err); color:#fff; font-size:10px; font-weight:700;
    border-radius:99px; padding:2px 6px; min-width:18px; text-align:center;
    border:2px solid var(--surface);
  }
  .notif-drawer {
    position:fixed; top:0; right:0; bottom:0; width:380px; max-width:90vw;
    background:#fff; border-left:1.5px solid var(--line);
    box-shadow:-8px 0 24px rgba(15,23,42,0.08); z-index:90;
    display:flex; flex-direction:column;
    transform:translateX(0); transition:transform .25s ease;
  }
  .notif-drawer.hidden { transform:translateX(100%); }
  .notif-drawer-header {
    display:flex; align-items:center; justify-content:space-between;
    padding:18px 22px; border-bottom:1px solid var(--line);
  }
  .notif-drawer-header h3 { margin:0; color:var(--navy); font-size:18px; }
  .notif-drawer-actions { display:flex; gap:6px; align-items:center; }
  .btn-text {
    background:none; border:0; color:var(--brand); cursor:pointer;
    font-size:13px; font-weight:600; padding:6px 10px; border-radius:6px;
  }
  .btn-text:hover { background:var(--brand-soft); }
  .notif-list { flex:1; overflow-y:auto; padding:8px; }
  .notif-empty {
    padding:48px 24px; text-align:center; color:var(--muted);
  }
  .notif-row {
    padding:14px 16px; border-radius:10px;
    margin:4px; cursor:pointer;
    border-left:3px solid transparent;
    transition:background .15s ease;
  }
  .notif-row:hover { background:var(--surface-soft); }
  .notif-row.unread { background:#eef0fb; border-left-color:var(--brand); }
  .notif-row .notif-title { font-weight:600; color:var(--ink); margin-bottom:3px; }
  .notif-row .notif-body { font-size:13px; color:var(--muted); margin-bottom:5px; }
  .notif-row .notif-meta { font-size:11px; color:var(--muted); display:flex; gap:8px; }
  .notif-kind-pill {
    display:inline-block; padding:1px 7px; border-radius:99px; font-size:10px;
    font-weight:700; text-transform:uppercase; letter-spacing:0.04em;
  }
  .notif-kind-pill.announcement { background:#dbeafe; color:#1e40af; }
  .notif-kind-pill.assignment_due { background:#fef3c7; color:#92400e; }
  .notif-kind-pill.system { background:#e5e7eb; color:#4b5563; }

  /* === Sign-in modal SSO buttons (v0.11.0 E7) === */
  .sso-block {
    display:flex; flex-direction:column; gap:8px; margin-bottom:14px;
  }
  .sso-button {
    display:flex; align-items:center; justify-content:center; gap:10px;
    padding:11px 14px; background:#fff; color:var(--ink);
    border:1.5px solid var(--line); border-radius:10px; cursor:pointer;
    font:inherit; font-size:14px; font-weight:600;
    transition:all .15s ease; text-decoration:none;
  }
  .sso-button:hover {
    border-color:var(--brand); background:var(--brand-soft); color:var(--brand);
  }
  .sso-button .sso-logo {
    width:18px; height:18px; display:inline-flex; align-items:center;
    justify-content:center; font-size:16px;
  }
  .sso-divider {
    display:flex; align-items:center; gap:10px; margin:14px 0;
    color:var(--muted); font-size:12px; text-transform:uppercase;
    letter-spacing:0.08em; font-weight:600;
  }
  .sso-divider::before, .sso-divider::after {
    content:''; flex:1; height:1px; background:var(--line);
  }

  /* === DPDP signup additions (v0.11.0 S2) === */
  .signup-dpdp { margin-top:10px; padding-top:10px;
                 border-top:1px dashed var(--line); }
  .signup-dpdp.show { display:block; }
  .signup-dpdp .hint { font-size:12px; color:var(--muted); margin-top:4px; }
  .signup-locked-banner {
    background:#fffbeb; border:1px solid #fde68a; color:#78350f;
    padding:12px 14px; border-radius:10px; margin:10px 0;
    font-size:13px;
  }

  /* === E5 Fees + invoicing (v0.16.0) === */
  .sch-fees-summary {
    display:grid; grid-template-columns:repeat(4, 1fr); gap:10px;
  }
  .sch-fees-summary .tile {
    padding:14px; border:1px solid var(--line); border-radius:10px;
    background:#fff;
  }
  .sch-fees-summary .tile.collected { background:#d1fae5; border-color:#a7f3d0; }
  .sch-fees-summary .tile.pending   { background:#fef3c7; border-color:#fde68a; }
  .sch-fees-summary .tile.overdue   { background:#fee2e2; border-color:#fecaca; }
  .sch-fees-summary .lbl {
    font-size:10px; font-weight:700; text-transform:uppercase;
    letter-spacing:0.06em; color:var(--muted); margin-bottom:6px;
  }
  .sch-fees-summary .val {
    font-size:22px; font-weight:700; color:var(--ink);
    font-variant-numeric:tabular-nums;
  }
  .sch-fees-summary .sub { font-size:11px; color:var(--muted); margin-top:2px; }

  .sch-fee-struct-row {
    display:grid; grid-template-columns: 2fr 1fr 1fr 1fr auto;
    gap:12px; align-items:center;
    padding:12px 14px; border:1px solid var(--line); border-radius:8px;
    margin-bottom:6px; font-size:13px;
  }
  .sch-fee-struct-row .name { font-weight:600; color:var(--ink); }
  .sch-fee-struct-row .meta { color:var(--muted); font-size:11px; }

  .sch-invoice-row {
    display:grid; grid-template-columns: 1.4fr 1fr 1fr 1fr auto;
    gap:12px; align-items:center;
    padding:10px 14px; border:1px solid var(--line); border-radius:8px;
    margin-bottom:5px; font-size:13px; background:#fff;
  }
  .sch-invoice-row .amount { font-weight:700; font-variant-numeric:tabular-nums; }
  .sch-invoice-row .status-pill {
    padding:3px 9px; border-radius:99px; font-size:11px;
    font-weight:700; text-transform:uppercase; letter-spacing:0.04em;
    display:inline-block;
  }
  .sch-invoice-row .status-pill.pending   { background:#fef3c7; color:#92400e; }
  .sch-invoice-row .status-pill.paid      { background:#d1fae5; color:#065f46; }
  .sch-invoice-row .status-pill.overdue   { background:#fee2e2; color:#991b1b; }
  .sch-invoice-row .status-pill.cancelled { background:#e5e7eb; color:#4b5563; }
  .sch-invoice-row .status-pill.refunded  { background:#dbeafe; color:#1e40af; }

  /* === E4 Exams + S4 anti-cheat (v0.15.0) === */
  .sch-exam-row {
    display:flex; align-items:center; justify-content:space-between;
    padding:14px; border:1px solid var(--line); border-radius:10px;
    background:#fff; margin-bottom:8px;
  }
  .sch-exam-row .meta { font-size:12px; color:var(--muted); margin-top:4px; }
  .sch-exam-row .status-pill {
    padding:4px 10px; border-radius:99px; font-size:11px;
    font-weight:700; text-transform:uppercase; letter-spacing:0.04em;
  }
  .sch-exam-row .status-pill.scheduled { background:#dbeafe; color:#1e40af; }
  .sch-exam-row .status-pill.draft     { background:#e5e7eb; color:#4b5563; }
  .sch-exam-row .status-pill.in_progress { background:#fef3c7; color:#92400e; }
  .sch-exam-row .status-pill.done      { background:#d1fae5; color:#065f46; }

  .exam-header {
    display:flex; align-items:center; justify-content:space-between;
    border-bottom:1.5px solid var(--line); padding-bottom:12px;
    margin-bottom:18px;
  }
  .exam-header h3 { margin:0; color:var(--ink); font-size:18px; }
  .exam-timer {
    background:#fef3c7; color:#92400e; padding:6px 14px;
    border-radius:99px; font-weight:700; font-variant-numeric:tabular-nums;
    font-size:15px;
  }
  .exam-timer.warning { background:#fee2e2; color:#991b1b;
                        animation:exam-pulse 1s infinite; }
  @keyframes exam-pulse { 50% { opacity:0.6; } }
  .exam-warning {
    background:#fee2e2; color:#991b1b; padding:10px 14px;
    border-radius:8px; margin-bottom:14px; font-size:13px; font-weight:600;
  }
  .exam-q { margin-bottom:18px; padding-bottom:18px;
            border-bottom:1px solid var(--line); }
  .exam-q:last-child { border-bottom:0; }
  .exam-q .q-num {
    font-size:11px; color:var(--muted); font-weight:700;
    text-transform:uppercase; letter-spacing:0.06em; margin-bottom:6px;
  }
  .exam-q .q-text { font-size:15px; color:var(--ink); margin-bottom:10px;
                    font-weight:500; }
  .exam-q .opt {
    display:flex; align-items:center; gap:10px; padding:10px 14px;
    border:1.5px solid var(--line); border-radius:8px; margin-bottom:6px;
    cursor:pointer; transition:all .15s ease;
  }
  .exam-q .opt:hover { background:var(--brand-soft); border-color:var(--brand); }
  .exam-q .opt input { accent-color:var(--brand); }
  .exam-q .opt.selected { background:var(--brand-soft); border-color:var(--brand); }
  .exam-q .opt .letter { font-weight:700; color:var(--brand); width:18px; }
  .exam-q textarea {
    width:100%; padding:10px; border:1.5px solid var(--line);
    border-radius:8px; font:inherit; min-height:80px;
  }

  .exam-attempt-row {
    display:grid;
    grid-template-columns: 1.5fr 1fr 1fr 1fr 1fr;
    gap:12px; align-items:center;
    padding:12px 14px; border:1px solid var(--line); border-radius:8px;
    margin-bottom:6px; font-size:13px;
  }
  .exam-attempt-row.flagged { background:#fef3c7; }
  .exam-flag-pill {
    display:inline-block; padding:2px 8px; border-radius:99px;
    background:#fee2e2; color:#991b1b; font-size:11px; font-weight:700;
  }

  /* === E8 Parent linking children bar (v0.14.0) === */
  .pd-children-bar {
    display:flex; align-items:center; gap:8px; flex-wrap:wrap;
    margin:0 0 14px; padding:10px 14px;
    background:#fff; border:1px solid var(--line); border-radius:12px;
  }
  .pd-child-chip {
    background:#fff; border:1.5px solid var(--line); border-radius:99px;
    padding:6px 14px; font:inherit; font-size:13px; font-weight:600;
    color:var(--muted); cursor:pointer; transition:all .15s ease;
  }
  .pd-child-chip:hover {
    background:var(--brand-soft); border-color:var(--brand); color:var(--brand);
  }
  .pd-child-chip.active {
    background:var(--brand); border-color:var(--brand); color:#fff;
  }
  .pd-child-chip.pending {
    background:#fef3c7; border-color:#fde68a; color:#92400e;
  }
  .pd-child-chip.pd-child-add {
    border-style:dashed; color:var(--brand);
  }

  /* === E3 Attendance + E6 Timetable (v0.13.0) === */
  .sch-att-grid {
    display:flex; flex-direction:column; gap:6px;
  }
  .sch-att-row {
    display:grid;
    grid-template-columns: 1fr auto;
    gap:14px; align-items:center;
    padding:10px 14px; border:1px solid var(--line);
    border-radius:10px; background:#fff;
  }
  .sch-att-row .name { font-weight:600; color:var(--ink); }
  .sch-att-row .pills { display:flex; gap:4px; }
  .sch-att-pill {
    padding:6px 12px; border:1.5px solid var(--line);
    border-radius:99px; background:#fff; cursor:pointer;
    font:inherit; font-size:12px; font-weight:600; color:var(--muted);
    text-transform:uppercase; letter-spacing:0.04em;
    transition:all .15s ease;
  }
  .sch-att-pill:hover { background:var(--brand-soft); border-color:var(--brand); }
  .sch-att-pill.present.active   { background:#d1fae5; color:#065f46; border-color:#10b981; }
  .sch-att-pill.absent.active    { background:#fee2e2; color:#991b1b; border-color:#ef4444; }
  .sch-att-pill.late.active      { background:#fef3c7; color:#92400e; border-color:#f59e0b; }
  .sch-att-pill.excused.active   { background:#e0e7ff; color:#4338ca; border-color:#6366f1; }

  .sch-tt-grid {
    display:grid; grid-template-columns:repeat(7, 1fr); gap:6px;
    overflow-x:auto; min-height:200px;
  }
  .sch-tt-day {
    background:#fff; border:1px solid var(--line); border-radius:10px;
    padding:10px;
  }
  .sch-tt-day h5 {
    margin:0 0 8px; color:var(--brand); font-size:12px;
    font-weight:700; text-transform:uppercase; letter-spacing:0.06em;
  }
  .sch-tt-slot {
    margin-bottom:6px; padding:8px 10px; background:var(--surface-soft);
    border-radius:6px; font-size:12px;
  }
  .sch-tt-slot .time { color:var(--muted); font-variant-numeric:tabular-nums; }
  .sch-tt-slot .subject { font-weight:600; color:var(--ink); }
  .sch-tt-slot .room { color:var(--muted); font-size:11px; }
  .sch-tt-empty { color:var(--muted); font-size:12px; font-style:italic; padding:4px 6px; }
  @media (max-width: 900px) {
    .sch-tt-grid { grid-template-columns:repeat(2, 1fr); }
  }

  /* === School / Coaching portal (v0.9.0) === */
  .sch-hero { margin-bottom:18px; }
  .sch-hero h3 { margin:0 0 6px; color:var(--ink); font-size:20px; }
  .sch-hero p { margin:0; color:var(--muted); font-size:14px; }
  .sch-form .row {
    display:grid; grid-template-columns:1fr 1fr; gap:12px;
  }
  .sch-form label {
    display:block; font-size:13px; font-weight:600;
    color:var(--ink); margin-top:12px; margin-bottom:4px;
  }
  .sch-form label .hint {
    color:var(--muted); font-weight:400; font-size:12px;
  }
  .sch-form input, .sch-form select, .sch-form textarea {
    width:100%; padding:10px 12px; border:1.5px solid var(--line);
    border-radius:8px; font:inherit; font-size:14px;
  }
  .sch-form input:focus, .sch-form select:focus, .sch-form textarea:focus {
    outline:none; border-color:var(--brand);
    box-shadow:0 0 0 3px rgba(94,96,206,0.15);
  }
  .sch-form button[type="submit"] {
    margin-top:18px; width:100%; padding:12px;
  }

  .sch-header {
    display:flex; align-items:center; justify-content:space-between; gap:14px;
  }
  .sch-header h3 { margin:0; color:var(--ink); font-size:22px; }
  .sch-org-meta { margin:4px 0 0; color:var(--muted); font-size:13px; }
  .sch-plan-pill {
    background:linear-gradient(135deg, var(--brand), var(--purple));
    color:#fff; padding:6px 14px; border-radius:99px;
    font-size:12px; font-weight:700;
    text-transform:uppercase; letter-spacing:0.06em;
    white-space:nowrap;
  }

  .sch-kpis {
    display:grid; grid-template-columns:repeat(5, 1fr); gap:10px;
    margin:14px 0 18px;
  }
  .sch-kpi {
    background:#fff; border:1px solid var(--line); border-radius:12px;
    padding:14px;
  }
  .sch-kpi .lbl {
    font-size:10px; font-weight:700; text-transform:uppercase;
    letter-spacing:0.08em; color:var(--muted); margin-bottom:6px;
  }
  .sch-kpi .val {
    font-size:24px; font-weight:700; color:var(--ink);
    font-variant-numeric:tabular-nums;
  }
  .sch-kpi-accent {
    background:linear-gradient(135deg, #eef0fb, #f5e8ff);
    border-color:transparent;
  }
  .sch-kpi-accent .val { color:var(--brand); }

  .sch-tabs {
    display:flex; gap:6px; border-bottom:1.5px solid var(--line);
    margin-bottom:14px;
  }
  .sch-tab {
    background:none; border:none; padding:10px 16px;
    font:inherit; font-size:14px; font-weight:600; color:var(--muted);
    border-bottom:2px solid transparent; margin-bottom:-1.5px;
    cursor:pointer;
  }
  .sch-tab:hover { color:var(--ink); }
  .sch-tab.active {
    color:var(--brand); border-bottom-color:var(--brand);
  }

  .sch-toolbar {
    display:flex; align-items:center; justify-content:space-between;
    margin-bottom:10px;
  }
  .sch-toolbar h4 {
    margin:0; color:var(--ink); font-size:16px;
    flex-shrink:0; white-space:nowrap;
  }
  .sch-toolbar-actions { display:flex; gap:8px; align-items:center; }
  .sch-upload-btn { cursor:pointer; }

  .sch-hint {
    font-size:12px; color:var(--muted); margin:0 0 12px;
  }
  .sch-hint code {
    font-family:'Menlo', monospace; font-size:11px;
    background:var(--surface-soft); padding:1px 6px; border-radius:4px;
  }

  .sch-table-wrap { overflow-x:auto; }
  .sch-table {
    width:100%; border-collapse:collapse; font-size:13.5px;
  }
  .sch-table th, .sch-table td {
    text-align:left; padding:10px 12px;
    border-bottom:1px solid var(--line);
  }
  .sch-table th {
    font-size:11px; text-transform:uppercase; letter-spacing:0.06em;
    color:var(--muted); font-weight:700;
  }
  .sch-table tbody tr:hover { background:#fafbff; }

  .sch-role-pill {
    display:inline-block; padding:2px 9px; border-radius:99px;
    font-size:11px; font-weight:700;
    text-transform:uppercase; letter-spacing:0.04em;
  }
  .sch-role-pill.admin   { background:#fef3c7; color:#92400e; }
  .sch-role-pill.teacher { background:#dbeafe; color:#1e40af; }
  .sch-role-pill.student { background:#d1fae5; color:#065f46; }

  .sch-class-grid {
    display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr));
    gap:12px;
  }
  .sch-class-card {
    background:#fff; border:1.5px solid var(--line); border-radius:12px;
    padding:14px;
  }
  .sch-class-card h5 {
    margin:0 0 4px; color:var(--ink); font-size:15px;
  }
  .sch-class-card .grade {
    font-size:11px; color:var(--muted);
    text-transform:uppercase; letter-spacing:0.06em;
  }

  .sch-assignments { display:flex; flex-direction:column; gap:10px; }
  .sch-assignment-row {
    display:flex; align-items:center; justify-content:space-between;
    padding:14px; border:1px solid var(--line); border-radius:10px;
    background:#fff;
  }
  .sch-assignment-row:hover { background:#fafbff; }
  .sch-assignment-row .meta { font-size:12px; color:var(--muted); margin-top:4px; }
  .sch-assignment-row .due {
    background:var(--brand-soft); color:var(--brand);
    padding:4px 10px; border-radius:99px; font-size:12px; font-weight:600;
    white-space:nowrap;
  }
  .sch-assignment-row .due.overdue { background:#fee2e2; color:#991b1b; }
  .sch-assignment-row { cursor:pointer; }

  /* Per-assignment stats drawer (E1) */
  .sch-stats-kpis {
    display:grid; grid-template-columns:repeat(4, 1fr); gap:10px;
    margin-bottom:14px;
  }
  .sch-stats-kpis > div {
    background:var(--surface-soft); border-radius:10px; padding:12px;
  }
  .sch-stats-kpis .lbl {
    font-size:10px; font-weight:700; text-transform:uppercase;
    letter-spacing:0.06em; color:var(--muted); margin-bottom:4px;
  }
  .sch-stats-kpis .val {
    font-size:22px; font-weight:700; color:var(--ink);
    font-variant-numeric:tabular-nums;
  }
  .sch-stats-kpis .sub { font-size:11px; color:var(--muted); margin-top:2px; }

  .sch-status-pill {
    display:inline-block; padding:2px 9px; border-radius:99px;
    font-size:11px; font-weight:700;
    text-transform:uppercase; letter-spacing:0.04em;
  }
  .sch-status-pill.completed   { background:#d1fae5; color:#065f46; }
  .sch-status-pill.in_progress { background:#dbeafe; color:#1e40af; }
  .sch-status-pill.not_started { background:#e5e7eb; color:#4b5563; }

  .sch-modal {
    position:fixed; inset:0; z-index:100;
    background:rgba(15,23,42,0.45);
    display:flex; align-items:center; justify-content:center;
    padding:24px;
  }
  .sch-modal .card {
    max-width:480px; width:100%;
    max-height:90vh; overflow-y:auto;
    position:relative;
  }
  .sch-modal h3 { margin:0 0 12px; }
  .modal-close {
    position:absolute; top:10px; right:14px;
    background:none; border:none; font-size:24px; line-height:1;
    color:var(--muted); cursor:pointer; padding:4px;
  }
  .modal-close:hover { color:var(--ink); }

  @media (max-width: 800px) {
    .sch-kpis { grid-template-columns:repeat(2, 1fr); }
    .sch-form .row { grid-template-columns:1fr; }
    .sch-header { flex-direction:column; align-items:flex-start; }
  }

  /* === Sidebar (v0.8.2 IA redesign) === */
  aside {
    grid-area:sidebar;
    background:var(--surface);
    border-right:1px solid var(--line);
    padding:14px 0 18px;
    /* Sidebar scrolls independently from main content so the primary
       items stay visible while the user explores grouped tools. */
    overflow-y:auto;
    display:flex; flex-direction:column;
    height:100%;
  }
  aside .nav-section {
    padding:14px 22px 6px; font-size:10px; font-weight:800;
    color:var(--muted); letter-spacing:1.2px; text-transform:uppercase;
    display:flex; align-items:center; gap:6px;
  }
  aside .nav-section::after {
    content:''; flex:1; height:1px; background:var(--line);
    margin-left:6px;
  }
  aside .nav-section:first-child { padding-top:6px; }
  aside .nav-section.tight { padding-top:8px; }

  /* Spacer pushes the bottom-anchored sections (Teach / For Adults) to
     the foot of the sidebar so they sit visually separated regardless
     of how many items are above. */
  aside .nav-spacer { flex:1; min-height:8px; }

  aside .nav-item {
    display:flex; align-items:center; gap:11px; width:100%;
    padding:8px 22px; background:none; border:0; color:var(--ink-soft);
    font-size:13.5px; text-align:left; cursor:pointer; font-family:inherit;
    font-weight:500; transition:all .15s ease; position:relative;
  }
  aside .nav-item:hover {
    background:var(--surface-soft); color:var(--ink);
  }
  aside .nav-item.active {
    color:var(--brand); font-weight:700;
    background:linear-gradient(90deg, var(--brand-soft) 0%, transparent 100%);
  }
  aside .nav-item.active::before {
    content:''; position:absolute; left:0; top:5px; bottom:5px; width:3px;
    background:var(--brand); border-radius:0 4px 4px 0;
  }
  aside .nav-item .ico {
    width:28px; height:28px; border-radius:8px; font-size:14px;
    display:flex; align-items:center; justify-content:center;
    background:var(--surface-soft); flex-shrink:0;
    transition:all .15s ease;
  }
  aside .nav-item:hover .ico,
  aside .nav-item.active .ico {
    background:var(--brand-soft); transform:scale(1.06);
  }
  /* Primary items (the "verbs") get a touch more weight so they read
     as the main entries; secondary tools inside a group are denser. */
  aside .nav-item.primary {
    padding:10px 22px; font-size:14px;
  }
  aside .nav-item.primary .ico {
    width:32px; height:32px; font-size:16px;
  }

  /* Collapsible group: header is a button; clicking it toggles the
     `open` class on the parent .nav-group. */
  aside .nav-group { margin:0; }
  aside .nav-group-header {
    width:100%; display:flex; align-items:center; gap:6px;
    padding:13px 22px 6px; background:none; border:0;
    font-size:10px; font-weight:800; color:var(--muted);
    letter-spacing:1.2px; text-transform:uppercase;
    cursor:pointer; font-family:inherit;
  }
  aside .nav-group-header .chev {
    display:inline-block; width:9px; height:9px;
    border-right:2px solid var(--muted);
    border-bottom:2px solid var(--muted);
    transform:rotate(-45deg);
    transition:transform .18s ease;
    margin-left:-2px;
  }
  aside .nav-group.open .nav-group-header .chev {
    transform:rotate(45deg);
  }
  aside .nav-group-header .count {
    margin-left:auto; font-size:10px; font-weight:700;
    background:var(--surface-soft); color:var(--muted);
    padding:1px 7px; border-radius:99px;
  }
  aside .nav-group-body {
    max-height:0; overflow:hidden;
    transition:max-height .25s ease;
  }
  aside .nav-group.open .nav-group-body {
    /* tall enough to fit the largest group (5 items) */
    max-height:280px;
  }

  /* Footer sections (Teach / For Adults) use a tighter, muted style so
     they read as utility rather than primary verbs. */
  aside .nav-footer { padding-top:6px; }
  aside .nav-footer .nav-item .ico {
    background:transparent; border:1px solid var(--line);
  }

  /* === Main === */
  main { grid-area:main; padding:32px 36px; max-width:980px; }
  .module { display:none; animation:fadeUp .3s ease; }
  .module.active { display:block; }
  @keyframes fadeUp {
    from { opacity:0; transform:translateY(8px); }
    to { opacity:1; transform:translateY(0); }
  }
  .page-title {
    font-size:32px; font-weight:800; letter-spacing:-0.7px;
    margin:0 0 4px; line-height:1.15;
  }
  .page-sub {
    color:var(--muted); margin:0 0 24px; font-size:15px; max-width:600px;
  }

  /* === Hero (Create Lesson) === */
  .hero {
    background:linear-gradient(135deg, var(--brand) 0%, var(--purple) 100%);
    color:#fff; border-radius:20px; padding:32px 36px; margin-bottom:24px;
    position:relative; overflow:hidden;
    box-shadow:0 20px 40px -12px rgba(94,96,206,.4);
  }
  .hero::before {
    content:''; position:absolute; top:-40%; right:-10%;
    width:380px; height:380px; border-radius:50%;
    background:radial-gradient(circle, rgba(255,182,39,.25) 0%, transparent 70%);
    pointer-events:none;
  }
  .hero::after {
    content:''; position:absolute; bottom:-40%; left:-15%;
    width:340px; height:340px; border-radius:50%;
    background:radial-gradient(circle, rgba(255,255,255,.10) 0%, transparent 70%);
    pointer-events:none;
  }
  .hero h2 {
    font-size:30px; font-weight:800; margin:0 0 8px;
    letter-spacing:-0.6px; position:relative; line-height:1.15;
  }
  .hero p {
    margin:0; font-size:15px; opacity:0.92; max-width:520px;
    position:relative;
  }
  .hero-features {
    display:flex; flex-wrap:wrap; gap:8px; margin-top:18px;
    position:relative;
  }
  .hero-chip {
    background:rgba(255,255,255,0.18); backdrop-filter:blur(8px);
    color:#fff; padding:5px 12px; border-radius:999px;
    font-size:12px; font-weight:600; border:1px solid rgba(255,255,255,0.25);
  }

  /* === Cards === */
  .card {
    background:var(--surface); border:1px solid var(--line);
    border-radius:var(--radius); padding:22px;
    margin-bottom:16px; box-shadow:var(--shadow-sm);
    transition:box-shadow .2s ease;
  }
  .card.compact { padding:14px 18px; }
  .card-title {
    font-size:16px; font-weight:700; margin:0 0 14px;
    display:flex; align-items:center; gap:8px; color:var(--ink);
  }
  .card-title .ic {
    width:28px; height:28px; border-radius:8px;
    background:var(--brand-soft); color:var(--brand);
    display:flex; align-items:center; justify-content:center; font-size:14px;
  }

  /* === Forms === */
  label {
    display:block; font-weight:600; font-size:13px;
    margin:14px 0 6px; color:var(--ink-soft); letter-spacing:0.1px;
  }
  label:first-child { margin-top:0; }
  input[type=file], input[type=email], input[type=password],
  input[type=text], textarea, select {
    display:block; width:100%; padding:11px 14px;
    border:1.5px solid var(--line); border-radius:var(--radius-sm);
    font-size:14px; background:#fff; font-family:inherit;
    color:var(--ink); transition:all .15s ease;
  }
  input:focus, select:focus, textarea:focus {
    outline:none; border-color:var(--brand);
    box-shadow:0 0 0 4px var(--brand-soft);
  }
  input[type=file] { padding:9px 14px; cursor:pointer; }
  input[type=file]::file-selector-button {
    background:var(--brand-soft); color:var(--brand); border:0;
    padding:7px 14px; border-radius:8px; font-weight:700;
    cursor:pointer; margin-right:12px; font-family:inherit; font-size:13px;
  }
  textarea { resize:vertical; min-height:80px; }
  .row { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  .row3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:14px; }
  .check {
    display:inline-flex; align-items:center; gap:8px; margin-top:12px;
    font-size:14px; color:var(--ink-soft); cursor:pointer; font-weight:500;
  }
  .check input { width:16px; height:16px; accent-color:var(--brand); }

  /* === Buttons === */
  button.primary, button.btn {
    padding:12px 22px; font-size:15px; font-weight:700;
    background:linear-gradient(135deg, var(--brand) 0%, var(--brand-dark) 100%);
    color:white; border:0; border-radius:var(--radius-sm);
    cursor:pointer; font-family:inherit;
    box-shadow:0 4px 12px rgba(94,96,206,.3);
    transition:all .15s ease;
  }
  button.primary:hover, button.btn:hover {
    transform:translateY(-1px);
    box-shadow:0 8px 20px rgba(94,96,206,.4);
  }
  button.primary:active, button.btn:active { transform:translateY(0); }
  button.btn-ghost {
    padding:9px 16px; font-size:14px; background:var(--surface);
    color:var(--ink); border:1.5px solid var(--line);
    border-radius:8px; cursor:pointer; font-family:inherit;
    font-weight:600; transition:all .15s ease;
  }
  button.btn-ghost:hover {
    border-color:var(--brand); color:var(--brand);
    background:var(--brand-soft);
  }
  button.primary { width:100%; margin-top:20px; }
  button:disabled { opacity:0.55; cursor:wait; transform:none !important; }

  /* === Status === */
  .status {
    margin-top:12px; font-size:14px; color:var(--muted);
    min-height:20px; font-weight:500;
  }
  .status.error { color:var(--pink); }
  .status.ok { color:var(--good); }

  /* === Output === */
  video {
    width:100%; border-radius:var(--radius); margin-top:14px;
    background:#000; box-shadow:var(--shadow);
  }

  /* === Chat === */
  .messages {
    max-height:340px; overflow-y:auto; padding:6px 0; margin-top:8px;
  }
  .msg {
    padding:11px 14px; border-radius:14px; margin-bottom:8px;
    font-size:14px; line-height:1.5; max-width:80%;
  }
  .msg.you {
    background:linear-gradient(135deg, var(--brand) 0%, var(--brand-dark) 100%);
    color:#fff; margin-left:auto; border-bottom-right-radius:4px;
  }
  .msg.ai {
    background:var(--surface-soft); color:var(--ink);
    border-bottom-left-radius:4px; border:1px solid var(--line);
  }
  .chat-input { display:flex; gap:8px; margin-top:8px; }
  .chat-input input { flex:1; }
  .chat-input button { width:auto; margin:0; padding:11px 20px; }

  /* === Library === */
  .lib-item {
    display:grid; grid-template-columns:auto 1fr auto; gap:14px;
    align-items:center; padding:14px 4px;
    border-bottom:1px solid var(--line);
  }
  .lib-item:last-child { border-bottom:0; }
  .lib-item .ico {
    width:46px; height:46px; border-radius:12px;
    background:linear-gradient(135deg, var(--brand-soft), var(--accent-soft));
    display:flex; align-items:center; justify-content:center;
    font-size:22px;
  }
  .lib-item .meta { font-size:12px; color:var(--muted); margin-top:2px; }
  .lib-item .title { font-weight:700; font-size:15px; color:var(--ink); }

  /* === Chips & pills === */
  .chip {
    display:inline-block; padding:3px 11px; font-size:11px; font-weight:700;
    background:var(--surface-soft); color:var(--muted); border-radius:999px;
    margin-right:5px; letter-spacing:0.2px;
  }
  .chip.success { background:var(--good-soft); color:var(--good); }
  .chip.error { background:var(--pink-soft); color:var(--pink); }
  .chip.info { background:var(--info-soft); color:var(--info); }
  .chip.warn { background:var(--warn-soft); color:var(--warn); }

  /* === Modal === */
  .modal {
    position:fixed; inset:0; background:rgba(15,23,41,0.55);
    backdrop-filter:blur(4px);
    display:none; align-items:center; justify-content:center;
    z-index:20; padding:20px;
    animation:fadeIn .2s ease;
  }
  .modal.open { display:flex; }
  @keyframes fadeIn { from {opacity:0;} to {opacity:1;} }
  .modal .card {
    width:100%; max-width:420px; margin:0;
    box-shadow:var(--shadow-lg); padding:28px;
    animation:popIn .25s cubic-bezier(.4,1.4,.6,1);
  }
  @keyframes popIn {
    from { opacity:0; transform:scale(.92) translateY(8px); }
    to   { opacity:1; transform:scale(1) translateY(0); }
  }
  .modal-close {
    float:right; background:none; color:var(--muted);
    border:0; padding:0 6px; font-size:22px; cursor:pointer; line-height:1;
  }
  .tabs {
    display:flex; gap:4px; margin-bottom:18px;
    background:var(--surface-soft); padding:4px; border-radius:10px;
  }
  .tab {
    flex:1; padding:8px; text-align:center; cursor:pointer; font-size:14px;
    color:var(--muted); border-radius:7px; font-weight:600;
    transition:all .15s ease;
  }
  .tab.active {
    color:var(--brand); background:#fff;
    box-shadow:var(--shadow-sm);
  }

  /* === Flashcards === */
  .fc-deck-stats {
    font-size:13px; color:var(--muted); margin-bottom:14px;
    display:flex; gap:18px; flex-wrap:wrap;
  }
  .fc-deck-stats strong { color:var(--ink); font-weight:700; }
  .fc-deck {
    perspective:1500px; padding:8px 0 18px;
    display:flex; justify-content:center;
  }
  .fc-card {
    position:relative; width:100%; max-width:480px; height:340px;
    transform-style:preserve-3d;
    transition:transform .65s cubic-bezier(.4,.2,.2,1);
    cursor:pointer;
  }
  .fc-card.flipped { transform:rotateY(180deg); }
  .fc-face {
    position:absolute; inset:0; backface-visibility:hidden;
    border-radius:18px; padding:30px 26px;
    display:flex; flex-direction:column; justify-content:center;
    box-shadow:0 20px 40px -12px rgba(15,23,41,.18);
    border:1px solid rgba(255,255,255,.5);
  }
  .fc-front {
    background:linear-gradient(135deg, #fff 0%, var(--brand-soft) 100%);
  }
  .fc-back {
    background:linear-gradient(135deg, #fff 0%, var(--gold-soft) 100%);
    transform:rotateY(180deg);
  }
  .fc-text {
    font-size:22px; font-weight:700; color:var(--ink);
    line-height:1.4; letter-spacing:-0.3px;
    text-align:center; max-height:200px; overflow-y:auto;
  }
  .fc-back .fc-text { font-size:17px; font-weight:500; }
  .fc-tags {
    display:flex; gap:6px; justify-content:center;
    margin-bottom:18px; flex-wrap:wrap;
  }
  .fc-tag {
    background:var(--brand-soft); color:var(--brand);
    padding:3px 11px; border-radius:999px; font-size:11px;
    font-weight:700; letter-spacing:0.3px;
  }
  .fc-hint {
    margin-top:14px; font-size:13px; color:var(--muted);
    font-style:italic; text-align:center;
  }
  .fc-flip-cue {
    position:absolute; bottom:14px; left:0; right:0;
    text-align:center; font-size:11px; color:var(--muted);
    letter-spacing:0.5px; text-transform:uppercase;
    font-weight:600; opacity:0.7;
  }
  .fc-nav {
    display:flex; align-items:center; justify-content:center;
    gap:18px; margin:14px 0;
  }
  .fc-pos {
    font-weight:700; color:var(--muted); font-size:14px;
    min-width:60px; text-align:center;
  }
  .fc-srs {
    display:grid; grid-template-columns:repeat(4, 1fr); gap:10px;
    margin-top:12px;
  }
  .srs-btn {
    padding:14px 8px; border:1.5px solid var(--line); background:#fff;
    border-radius:12px; cursor:pointer; font-family:inherit;
    display:flex; flex-direction:column; gap:4px; align-items:center;
    transition:all .15s ease;
  }
  .srs-btn:hover {
    transform:translateY(-2px); box-shadow:var(--shadow);
  }
  .srs-btn .rate { font-weight:800; font-size:14px; }
  .srs-btn .when { font-size:11px; color:var(--muted); font-weight:600; }
  .srs-again { border-color:var(--pink-soft); }
  .srs-again .rate { color:var(--pink); }
  .srs-again:hover { background:var(--pink-soft); border-color:var(--pink); }
  .srs-hard  { border-color:var(--warn-soft); }
  .srs-hard  .rate { color:var(--warn); }
  .srs-hard:hover { background:var(--warn-soft); border-color:var(--warn); }
  .srs-good  { border-color:var(--good-soft); }
  .srs-good  .rate { color:var(--good); }
  .srs-good:hover { background:var(--good-soft); border-color:var(--good); }
  .srs-easy  { border-color:var(--info-soft); }
  .srs-easy  .rate { color:var(--info); }
  .srs-easy:hover { background:var(--info-soft); border-color:var(--info); }
  .fc-actions {
    display:flex; gap:8px; margin-top:18px; justify-content:center;
    flex-wrap:wrap;
  }
  @media (max-width:600px) {
    .fc-card { height:420px; }
    .fc-text { font-size:18px; }
    .fc-back .fc-text { font-size:15px; }
    .fc-srs { grid-template-columns:repeat(2, 1fr); }
  }

  /* === Curriculum mapper === */
  .cm-match-card {
    border:1px solid var(--line); border-radius:12px; padding:16px;
    margin-bottom:10px; background:#fff;
    transition:all .15s ease;
  }
  .cm-match-card:hover { box-shadow:var(--shadow); transform:translateY(-1px); }
  .cm-match-card .cm-rank {
    display:inline-block; width:26px; height:26px; border-radius:999px;
    background:linear-gradient(135deg, var(--brand), var(--purple));
    color:#fff; text-align:center; line-height:26px;
    font-size:12px; font-weight:800; margin-right:10px;
  }
  .cm-match-card .cm-conf {
    float:right; font-size:13px; color:var(--good); font-weight:700;
  }
  .cm-match-card .cm-title { font-weight:700; font-size:15px; margin-bottom:6px; }
  .cm-match-card .cm-meta { font-size:12px; color:var(--muted); margin-bottom:8px; }
  .cm-match-card .cm-reason { font-size:13px; line-height:1.5; }
  .cm-index { display:grid; gap:8px; max-height:520px; overflow-y:auto; padding-right:6px; }
  .cm-row {
    display:grid; grid-template-columns:auto 1fr; gap:14px;
    padding:12px 14px; border:1px solid var(--line); border-radius:10px;
    background:#fff; font-size:14px;
    transition:all .15s ease;
  }
  .cm-row:hover { border-color:var(--brand); background:var(--surface-soft); }
  .cm-row .cm-chip {
    background:var(--brand-soft); color:var(--brand);
    padding:4px 11px; border-radius:999px; font-size:11px;
    font-weight:700; white-space:nowrap; align-self:start;
  }
  .cm-row .cm-info .cm-title { font-weight:700; margin-bottom:3px; color:var(--ink); }
  .cm-row .cm-info .cm-summary { color:var(--muted); font-size:13px; line-height:1.5; }
  .cm-row .cm-tags { margin-top:6px; display:flex; flex-wrap:wrap; gap:4px; }
  .cm-row .cm-tag {
    font-size:10px; background:var(--gold-soft); color:var(--warn);
    padding:2px 9px; border-radius:999px; font-weight:600;
  }

  /* === Parent dashboard === */
  .pd-tiles {
    display:grid; grid-template-columns:repeat(4,1fr); gap:14px;
    margin-bottom:18px;
  }
  .pd-tile {
    background:var(--surface); border:1px solid var(--line);
    border-radius:var(--radius); padding:18px;
    position:relative; overflow:hidden;
    transition:all .15s ease;
  }
  .pd-tile:hover { transform:translateY(-2px); box-shadow:var(--shadow); }
  .pd-tile .lbl {
    font-size:11px; color:var(--muted); text-transform:uppercase;
    letter-spacing:0.8px; font-weight:700;
  }
  .pd-tile .val {
    font-size:34px; font-weight:800; letter-spacing:-1.4px;
    color:var(--ink); margin-top:6px; line-height:1.1;
  }
  .pd-tile .sub { font-size:12px; color:var(--muted); margin-top:6px; font-weight:500; }
  .pd-tile.tile-week  { background:linear-gradient(135deg, var(--brand-soft) 0%, #fff 100%); }
  .pd-tile.tile-streak { background:linear-gradient(135deg, var(--accent-soft) 0%, #fff 100%); }
  .pd-tile.tile-streak .val { color:var(--accent); }
  .pd-tile.tile-total { background:linear-gradient(135deg, var(--purple-soft) 0%, #fff 100%); }
  .pd-tile.tile-lang  { background:linear-gradient(135deg, var(--good-soft) 0%, #fff 100%); }
  .pd-tile.tile-lang .val { color:var(--good); }
  .pd-chart {
    display:grid; grid-template-columns:repeat(7,1fr); gap:8px;
    height:150px; align-items:end; padding:8px 0 0;
  }
  .pd-bar {
    background:linear-gradient(180deg, var(--brand) 0%, var(--purple) 100%);
    border-radius:8px 8px 2px 2px; position:relative;
    min-height:8px; display:flex; flex-direction:column;
    justify-content:flex-end; align-items:center;
    transition:transform .2s ease, box-shadow .2s ease;
  }
  .pd-bar:hover {
    transform:translateY(-3px);
    box-shadow:0 6px 14px rgba(94,96,206,.3);
  }
  .pd-bar.zero { background:var(--surface-soft); border:1px solid var(--line); }
  .pd-bar .day-label {
    position:absolute; bottom:-24px; font-size:11px;
    color:var(--muted); font-weight:700;
  }
  .pd-bar .val-label {
    position:absolute; top:-22px; font-size:12px;
    color:var(--ink); font-weight:700;
  }
  .pd-bar.zero .val-label { display:none; }
  .pd-stripe {
    display:flex; align-items:center; justify-content:space-between;
    padding:8px 0; font-size:14px; border-bottom:1px solid var(--line);
  }
  .pd-stripe:last-child { border-bottom:0; }
  .pd-stripe .label { font-weight:600; }
  .pd-stripe .bar-wrap {
    flex:1; margin:0 14px; height:8px; background:var(--surface-soft);
    border-radius:999px; overflow:hidden;
  }
  .pd-stripe .bar-fill {
    height:100%;
    background:linear-gradient(90deg, var(--brand), var(--purple));
    border-radius:999px;
  }
  .pd-stripe .count {
    font-size:13px; color:var(--muted); font-weight:700;
    white-space:nowrap; min-width:28px; text-align:right;
  }
  .pd-recent-item {
    display:grid; grid-template-columns:auto 1fr auto; gap:12px;
    padding:12px 0; align-items:center;
    border-bottom:1px solid var(--line);
  }
  .pd-recent-item:last-child { border-bottom:0; }
  .pd-recent-item .when { font-size:12px; color:var(--muted); }
  @media (max-width:600px) {
    .pd-tiles { grid-template-columns:repeat(2,1fr); }
  }

  /* === Learning path === */
  .lp-summary {
    background:linear-gradient(135deg, var(--brand) 0%, var(--purple) 100%);
    color:#fff; border-radius:var(--radius); padding:24px 26px;
    margin-bottom:18px; box-shadow:0 12px 30px rgba(94,96,206,.3);
  }
  .lp-summary h3 {
    margin:0 0 8px; font-size:22px; letter-spacing:-0.5px; font-weight:800;
  }
  .lp-summary p { margin:0; opacity:0.92; font-size:14px; }
  .lp-summary p strong { font-weight:700; }
  .lp-week {
    background:var(--surface); border:1px solid var(--line);
    border-radius:var(--radius); margin-bottom:14px; overflow:hidden;
    box-shadow:var(--shadow-sm);
  }
  .lp-week-head {
    padding:16px 22px; background:var(--surface-soft);
    border-bottom:1px solid var(--line);
    display:flex; align-items:center; justify-content:space-between;
  }
  .lp-week-head .wknum {
    background:linear-gradient(135deg, var(--brand), var(--purple));
    color:#fff; width:32px; height:32px; border-radius:999px;
    display:flex; align-items:center; justify-content:center;
    font-size:13px; font-weight:800; margin-right:14px;
  }
  .lp-week-head .lp-theme { font-weight:700; font-size:15px; flex:1; }
  .lp-week-head .lp-time {
    font-size:12px; color:var(--muted); font-weight:600;
    background:#fff; padding:5px 11px; border-radius:999px;
    border:1px solid var(--line);
  }
  .lp-tasks { padding:8px 16px 14px; }
  .lp-task {
    display:grid; grid-template-columns:auto 1fr auto auto;
    gap:14px; align-items:center;
    padding:12px 8px; border-bottom:1px solid var(--surface-soft);
    font-size:14px;
  }
  .lp-task:last-child { border-bottom:0; }
  .lp-task .day {
    width:42px; height:42px; border-radius:10px;
    background:var(--surface-soft); color:var(--muted);
    display:flex; align-items:center; justify-content:center;
    font-size:11px; font-weight:800; letter-spacing:0.5px;
    text-transform:uppercase;
  }
  .lp-task .topic { font-weight:600; }
  .lp-task .topic .sub-task { color:var(--muted); font-size:12px; font-weight:500; }
  .lp-task .type-badge {
    padding:4px 11px; border-radius:999px; font-size:11px;
    font-weight:700; letter-spacing:0.3px;
  }
  .lp-task .type-watch    { background:var(--good-soft); color:var(--good); }
  .lp-task .type-quiz     { background:var(--accent-soft); color:var(--accent); }
  .lp-task .type-study    { background:var(--info-soft); color:var(--info); }
  .lp-task .type-practice { background:var(--warn-soft); color:var(--warn); }
  .lp-task .type-revision { background:var(--purple-soft); color:var(--purple); }
  .lp-task .mins {
    font-size:12px; color:var(--muted); white-space:nowrap;
    font-weight:600;
  }
  @media (max-width:600px) {
    .lp-task { grid-template-columns:auto 1fr auto; gap:10px; }
    .lp-task .mins { grid-column:2 / span 2; font-size:11px; padding-top:2px; }
  }

  /* === Stub modules === */
  .stub {
    background:linear-gradient(135deg, var(--surface-soft) 0%, var(--surface) 100%);
    border:2px dashed var(--line); border-radius:18px; padding:40px 28px;
    text-align:center; color:var(--muted);
  }
  .stub h3 {
    color:var(--ink); margin:0 0 10px; font-size:22px;
    font-weight:800; letter-spacing:-0.4px;
  }
  .stub .ico {
    font-size:56px; margin-bottom:14px; display:inline-block;
    background:var(--brand-soft); width:90px; height:90px; border-radius:24px;
    display:flex; align-items:center; justify-content:center;
    margin:0 auto 16px;
  }
  .stub ul {
    display:inline-block; text-align:left; margin:14px auto 0;
    padding-left:20px; color:var(--ink-soft); font-size:14px;
    line-height:1.8;
  }
  .stub a { color:var(--brand); font-weight:600; }

  /* === Quiz player === */
  .qz-progress-card { padding:14px 18px; }
  .qz-meta {
    display:flex; justify-content:space-between; align-items:center;
    gap:12px; margin-bottom:8px;
  }
  .qz-lesson-title { font-weight:600; color:var(--ink); font-size:14px; }
  .qz-counter { font-size:13px; color:var(--muted); font-variant-numeric:tabular-nums; }
  .qz-bar {
    height:6px; background:#eef0fb; border-radius:99px; overflow:hidden;
  }
  .qz-bar-fill {
    height:100%; background:linear-gradient(90deg, var(--brand), var(--purple));
    width:0%; transition:width .3s ease;
  }
  .qz-question {
    font-size:18px; font-weight:600; color:var(--ink); margin:6px 0 16px;
    line-height:1.4;
  }
  .qz-options { display:flex; flex-direction:column; gap:10px; }
  .qz-opt {
    display:flex; align-items:center; gap:12px;
    padding:14px 16px; border:1.5px solid #e6e7f3; border-radius:12px;
    background:#fff; cursor:pointer; text-align:left;
    font-size:15px; color:var(--ink); transition:all .15s ease;
  }
  .qz-opt:hover:not(.locked) { border-color:var(--brand); background:#f7f7ff; }
  .qz-opt-letter {
    display:inline-flex; align-items:center; justify-content:center;
    width:32px; height:32px; border-radius:99px;
    background:#eef0fb; color:var(--brand); font-weight:700; font-size:14px;
    flex-shrink:0;
  }
  .qz-opt.selected .qz-opt-letter { background:var(--brand); color:#fff; }
  .qz-opt.correct { border-color:#22a06b; background:#e8f7ef; }
  .qz-opt.correct .qz-opt-letter { background:#22a06b; color:#fff; }
  .qz-opt.wrong { border-color:#d23f3f; background:#fbe9e9; }
  .qz-opt.wrong .qz-opt-letter { background:#d23f3f; color:#fff; }
  .qz-opt.locked { cursor:default; }
  .qz-feedback {
    margin-top:14px; padding:12px 14px; border-radius:10px;
    font-size:14px; display:none;
  }
  .qz-feedback.show { display:block; }
  .qz-feedback.correct { background:#e8f7ef; color:#1a7a4f; }
  .qz-feedback.wrong { background:#fbe9e9; color:#a93030; }
  .qz-nav { display:flex; justify-content:flex-end; margin-top:14px; }
  .qz-score {
    font-size:48px; font-weight:800;
    background:linear-gradient(135deg, var(--brand), var(--purple));
    -webkit-background-clip:text; -webkit-text-fill-color:transparent;
    background-clip:text; text-align:center; margin:8px 0;
  }
  .qz-score-msg { text-align:center; color:var(--muted); font-size:15px; margin:0 0 18px; }
  .qz-actions { display:flex; gap:10px; flex-wrap:wrap; margin-top:8px; }
  .qz-review-q {
    padding:14px; border-radius:10px; background:#f7f7fb; margin-top:12px;
    border-left:3px solid #e6e7f3;
  }
  .qz-review-q.right { border-left-color:#22a06b; }
  .qz-review-q.wrong { border-left-color:#d23f3f; }
  .qz-review-q strong { color:var(--ink); }
  .qz-review-q p { margin:6px 0; font-size:14px; color:var(--muted); }

  /* === Match game === */
  .mg-progress-card { padding:14px 18px; }
  .mg-stats {
    display:flex; justify-content:space-around; align-items:center;
    gap:12px; font-size:14px; color:var(--muted);
  }
  .mg-stats strong { color:var(--ink); font-variant-numeric:tabular-nums; }
  .mg-grid {
    display:grid; grid-template-columns:repeat(4, 1fr); gap:12px;
    margin-top:16px;
  }
  .mg-cell {
    aspect-ratio:3/4; perspective:800px; cursor:pointer;
  }
  .mg-cell-inner {
    position:relative; width:100%; height:100%; transition:transform .45s;
    transform-style:preserve-3d;
  }
  .mg-cell.flipped .mg-cell-inner { transform:rotateY(180deg); }
  .mg-cell.matched .mg-cell-inner { transform:rotateY(180deg); }
  .mg-face {
    position:absolute; inset:0; border-radius:14px;
    display:flex; align-items:center; justify-content:center; padding:10px;
    backface-visibility:hidden; -webkit-backface-visibility:hidden;
    font-size:13px; line-height:1.3; text-align:center;
    box-shadow:var(--shadow-sm);
  }
  .mg-face-back {
    background:linear-gradient(135deg, var(--brand), var(--purple));
    color:#fff; font-size:28px;
  }
  .mg-face-front {
    background:#fff; color:var(--ink); transform:rotateY(180deg);
    border:1.5px solid #e6e7f3; overflow:hidden;
  }
  .mg-face-front.is-q { background:#f7f7ff; font-weight:600; }
  .mg-face-front.is-a { background:#fff8eb; }
  .mg-cell.matched .mg-face-front { border-color:#22a06b; box-shadow:0 0 0 2px #22a06b33; opacity:.85; }
  @media (max-width:600px) {
    .mg-grid { grid-template-columns:repeat(3, 1fr); gap:8px; }
    .mg-face { font-size:11px; }
  }

  /* === Audio recap === */
  .rc-header { display:flex; align-items:center; gap:14px; margin-bottom:6px; }
  .rc-icon {
    font-size:32px; width:56px; height:56px; border-radius:14px;
    background:linear-gradient(135deg, var(--brand), var(--purple));
    display:flex; align-items:center; justify-content:center;
    box-shadow:var(--shadow-sm);
  }
  .rc-title { font-weight:700; color:var(--ink); font-size:16px; }
  .rc-sub { font-size:13px; color:var(--muted); margin-top:2px; }
  .rc-transcript {
    border-top:1px solid #eef0fb; padding-top:10px; margin-top:6px;
  }
  .rc-transcript summary {
    cursor:pointer; font-size:13px; color:var(--brand); font-weight:600;
    padding:4px 0;
  }
  .rc-transcript p {
    margin:10px 0 4px; font-size:14px; line-height:1.6; color:var(--ink);
  }

  /* === Notes === */
  .nt-toolbar {
    display:flex; justify-content:space-between; align-items:center;
    margin-bottom:10px;
  }
  .nt-title { font-weight:600; color:var(--ink); font-size:14px; }
  .nt-save-state {
    font-size:12px; color:var(--muted); transition:color .25s;
  }
  .nt-save-state.saving { color:#b9892a; }
  .nt-save-state.saved { color:#22a06b; }
  #nt-textarea {
    width:100%; min-height:360px; padding:14px;
    border:1.5px solid #e6e7f3; border-radius:12px;
    font-family:inherit; font-size:15px; line-height:1.6;
    color:var(--ink); resize:vertical; outline:none;
    transition:border-color .15s;
  }
  #nt-textarea:focus { border-color:var(--brand); }

  /* === Live lecture === */
  .live-controls { padding:22px; }
  .live-mic-row {
    display:flex; align-items:center; gap:18px; flex-wrap:wrap;
  }
  .live-mic-btn {
    display:inline-flex; align-items:center; gap:12px;
    padding:18px 28px; border-radius:99px; border:0;
    background:linear-gradient(135deg, var(--brand), var(--purple));
    color:#fff; font-size:16px; font-weight:700; cursor:pointer;
    box-shadow:var(--shadow-md); transition:all .15s;
  }
  .live-mic-btn:hover { transform:translateY(-2px); box-shadow:var(--shadow-lg); }
  .live-mic-btn.listening {
    background:linear-gradient(135deg, #e74c3c, #c0392b);
    animation:live-pulse 1.4s infinite;
  }
  .live-mic-btn.thinking {
    background:linear-gradient(135deg, #b9892a, #d4a83c);
    cursor:wait;
  }
  .live-mic-btn.speaking {
    background:linear-gradient(135deg, #22a06b, #1a7a4f);
  }
  .live-mic-ico { font-size:22px; }
  @keyframes live-pulse {
    0%,100% { box-shadow:0 0 0 0 rgba(231,76,60,.6); }
    50%     { box-shadow:0 0 0 14px rgba(231,76,60,0); }
  }
  .live-lang { display:flex; flex-direction:column; gap:4px; flex:1; min-width:160px; }
  .live-lang select {
    padding:10px 12px; border-radius:10px; border:1.5px solid #e6e7f3;
    background:#fff; font-size:14px; color:var(--ink); outline:none;
  }
  .live-transcript {
    max-height:420px; overflow-y:auto;
    display:flex; flex-direction:column; gap:10px; margin-bottom:12px;
  }
  .live-turn {
    padding:12px 14px; border-radius:12px; font-size:14px; line-height:1.5;
    max-width:88%; word-wrap:break-word;
  }
  .live-turn.user {
    background:#eef0fb; color:var(--ink); align-self:flex-end;
    border-bottom-right-radius:4px;
  }
  .live-turn.tutor {
    background:linear-gradient(135deg, #f7f7ff, #fff);
    border:1.5px solid #e6e7f3; color:var(--ink); align-self:flex-start;
    border-bottom-left-radius:4px;
  }
  .live-turn .who {
    display:block; font-size:11px; font-weight:700; color:var(--muted);
    text-transform:uppercase; letter-spacing:.05em; margin-bottom:4px;
  }
  .live-turn.tutor .who { color:var(--brand); }

  /* === Explainer === */
  .ex-suggest-row {
    display:flex; gap:8px; flex-wrap:wrap; margin:14px 0 6px;
    align-items:center;
  }
  .ex-suggest-label { font-size:13px; color:var(--muted); margin-right:4px; }
  .ex-chip {
    padding:7px 12px; border-radius:99px; border:1.5px solid #e6e7f3;
    background:#fff; color:var(--ink); font-size:13px; cursor:pointer;
    transition:all .15s;
  }
  .ex-chip:hover {
    border-color:var(--brand); color:var(--brand);
    background:var(--brand-soft);
  }
  .ex-result { padding:24px; }
  .ex-header { display:flex; align-items:center; gap:14px; margin-bottom:14px; }
  .ex-icon {
    font-size:28px; width:54px; height:54px; border-radius:14px;
    background:linear-gradient(135deg, var(--brand), var(--purple));
    display:flex; align-items:center; justify-content:center;
    box-shadow:var(--shadow-sm); color:#fff;
  }
  .ex-topic {
    font-size:22px; font-weight:800; color:var(--ink);
    letter-spacing:-.01em; line-height:1.25;
  }
  .ex-meta {
    font-size:12px; color:var(--muted); margin-top:4px;
    display:flex; gap:8px; align-items:center;
  }
  .ex-cache-badge {
    display:none; padding:2px 8px; border-radius:99px;
    background:#e8f7ef; color:#1a7a4f; font-size:11px; font-weight:600;
  }
  .ex-cache-badge.show { display:inline-block; }
  .ex-oneliner {
    font-size:17px; line-height:1.5; color:var(--ink);
    background:linear-gradient(135deg, var(--brand-soft), #fff);
    padding:14px 16px; border-radius:12px; font-weight:500;
    border-left:4px solid var(--brand); margin-bottom:18px;
  }
  .ex-section { margin-bottom:18px; }
  .ex-section-h {
    font-size:13px; font-weight:700; color:var(--brand);
    text-transform:uppercase; letter-spacing:.05em; margin-bottom:8px;
  }
  .ex-explanation {
    font-size:15px; line-height:1.65; color:var(--ink);
    white-space:pre-wrap;
  }
  .ex-keypoints, .ex-mistakes {
    margin:0; padding-left:20px;
    display:flex; flex-direction:column; gap:6px;
  }
  .ex-keypoints li, .ex-mistakes li {
    font-size:15px; line-height:1.5; color:var(--ink);
  }
  .ex-keypoints li::marker { color:var(--brand); }
  .ex-mistakes li::marker { color:#d23f3f; }
  .ex-example {
    font-size:14px; line-height:1.7; color:var(--ink);
    background:#fafbff; padding:14px 16px; border-radius:10px;
    border:1px solid #e6e7f3; white-space:pre-wrap;
    font-family:ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .ex-analogy-section .ex-section-h { color:var(--purple); }
  .ex-analogy {
    font-size:15px; line-height:1.5; color:var(--ink); font-style:italic;
    background:var(--purple-soft); padding:14px 16px; border-radius:10px;
    border-left:4px solid var(--purple);
  }
  .ex-video-card { padding:22px; }
  .ex-video-status {
    font-size:14px; color:var(--ink); font-weight:600;
    padding:10px 14px; border-radius:10px;
    background:linear-gradient(135deg, var(--brand-soft), #fff);
    border-left:4px solid var(--brand);
    margin-bottom:14px;
  }
  .ex-video-status.done {
    background:#e8f7ef; border-left-color:#22a06b; color:#1a7a4f;
  }
  .ex-video-status.error {
    background:#fbe9e9; border-left-color:#d23f3f; color:#a93030;
  }
  .ex-video-steps {
    display:flex; flex-direction:column; gap:10px; padding-left:4px;
  }
  .ex-step {
    display:flex; align-items:center; gap:12px;
    font-size:14px; color:var(--muted); transition:color .25s;
  }
  .ex-step-dot {
    width:14px; height:14px; border-radius:99px;
    background:#eef0fb; border:2px solid #d0d3ec;
    flex-shrink:0; transition:all .25s;
  }
  .ex-step.active { color:var(--ink); font-weight:600; }
  .ex-step.active .ex-step-dot {
    background:var(--brand); border-color:var(--brand);
    box-shadow:0 0 0 4px var(--brand-soft);
    animation:ex-step-pulse 1.2s infinite;
  }
  .ex-step.done { color:#1a7a4f; }
  .ex-step.done .ex-step-dot {
    background:#22a06b; border-color:#22a06b;
  }
  .ex-step.done .ex-step-dot::after {
    content:"✓"; color:#fff; font-size:9px; font-weight:900;
    display:block; line-height:10px; text-align:center;
  }
  @keyframes ex-step-pulse {
    0%,100% { box-shadow:0 0 0 4px var(--brand-soft); }
    50%     { box-shadow:0 0 0 8px transparent; }
  }

  /* === Video Studio (PRD §15 Screens 1-5) === */
  .vs-steps {
    display:flex; align-items:center; gap:12px; padding:14px 18px;
    background:#fff; border:1.5px solid var(--line); border-radius:16px;
    margin-bottom:14px; overflow-x:auto;
  }
  .vs-step {
    display:flex; align-items:center; gap:8px;
    font-size:14px; font-weight:600; color:var(--muted);
    white-space:nowrap;
  }
  .vs-step:not(:last-child)::after {
    content:""; display:inline-block; width:32px; height:2px;
    background:var(--line); margin-left:10px;
  }
  .vs-step.active { color:var(--brand); }
  .vs-step.done { color:#1a7a4f; }
  .vs-num {
    display:inline-flex; align-items:center; justify-content:center;
    width:26px; height:26px; border-radius:99px;
    background:#eef0fb; color:var(--muted); font-size:13px; font-weight:700;
  }
  .vs-step.active .vs-num { background:var(--brand); color:#fff; }
  .vs-step.done  .vs-num { background:#22a06b; color:#fff; }

  .vs-source-grid {
    display:grid; grid-template-columns:repeat(2,1fr); gap:12px;
    margin:10px 0 14px;
  }
  .vs-source-btn {
    display:flex; flex-direction:column; gap:6px; padding:18px;
    border:2px solid var(--line); border-radius:14px;
    background:#fff; cursor:pointer; text-align:left;
    transition:all .15s ease;
  }
  .vs-source-btn:hover { border-color:var(--brand); background:var(--brand-soft); }
  .vs-source-btn.active {
    border-color:var(--brand); background:var(--brand-soft);
    box-shadow:0 0 0 3px rgba(94,96,206,0.15);
  }
  .vs-source-btn .ico { font-size:28px; }
  .vs-source-btn .lbl { font-size:15px; font-weight:700; color:var(--ink); }
  .vs-source-btn .sub { font-size:12px; color:var(--muted); }
  .vs-source-input { margin-top:4px; }
  .vs-source-input input[type="file"] {
    width:100%; padding:14px; border:1.5px dashed var(--line);
    border-radius:10px; background:#fafbff;
  }

  .vs-hint {
    font-size:12px; color:var(--muted); margin:6px 0 0;
    font-style:italic;
  }
  .vs-nav { display:flex; justify-content:space-between; margin-top:18px; gap:10px; }

  .vs-progress-head { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:8px; }
  .vs-progress-title { font-size:18px; font-weight:700; color:var(--ink); }
  .vs-progress-meta { font-size:13px; color:var(--muted); font-variant-numeric:tabular-nums; }
  .vs-progress-bar {
    height:8px; background:#eef0fb; border-radius:99px; overflow:hidden;
    margin-bottom:14px;
  }
  .vs-progress-fill {
    height:100%; background:linear-gradient(90deg, var(--brand), var(--purple));
    width:0%; transition:width .4s ease;
  }
  .vs-progress-list {
    list-style:none; padding:0; margin:0;
    display:flex; flex-direction:column; gap:8px;
  }
  .vs-progress-list li {
    padding:10px 14px; border-radius:10px; background:#fafbff;
    color:var(--muted); font-size:14px;
    display:flex; align-items:center; gap:10px;
    transition:all .25s ease;
  }
  .vs-progress-list li::before {
    content:""; display:inline-block; width:10px; height:10px; border-radius:99px;
    background:#d0d3ec; transition:all .25s;
  }
  .vs-progress-list li.active {
    background:var(--brand-soft); color:var(--ink); font-weight:600;
  }
  .vs-progress-list li.active::before {
    background:var(--brand); box-shadow:0 0 0 4px rgba(94,96,206,0.25);
    animation:vs-pulse 1.4s infinite;
  }
  .vs-progress-list li.done { color:#1a7a4f; }
  .vs-progress-list li.done::before {
    background:#22a06b;
    box-shadow:none; animation:none;
  }
  @keyframes vs-pulse {
    0%,100% { box-shadow:0 0 0 4px rgba(94,96,206,0.25); }
    50%     { box-shadow:0 0 0 8px rgba(94,96,206,0); }
  }

  .vs-result-head { margin-bottom:6px; }
  .vs-result-title { font-size:20px; font-weight:700; color:var(--ink); }
  .vs-result-meta { font-size:13px; color:var(--muted); margin-top:2px; }
  .vs-actions {
    display:flex; gap:8px; flex-wrap:wrap; margin:14px 0 10px;
  }
  .vs-actions .btn-ghost { padding:8px 14px; font-size:13px; }
  .vs-result-foot { border-top:1px solid var(--line); padding-top:14px; margin-top:6px; }

  @media (max-width:600px) {
    .vs-source-grid { grid-template-columns:1fr; }
    .vs-step:not(:last-child)::after { width:14px; }
  }

  /* === Burger / responsive === */
  .burger {
    display:none; background:none; border:0; font-size:22px;
    cursor:pointer; padding:0 8px 0 0;
  }
  @media (max-width:850px) {
    .app { grid-template-columns:1fr; grid-template-areas: "header" "main"; }
    aside {
      display:none; position:fixed; left:0; top:64px; bottom:0;
      width:260px; z-index:5;
      box-shadow:var(--shadow-lg);
    }
    aside.open { display:block; }
    .burger { display:inline-block; }
    main { padding:20px; }
    .row, .row3 { grid-template-columns:1fr; }
    .page-title { font-size:26px; }
    .hero { padding:24px; }
    .hero h2 { font-size:24px; }
  }

  /* === Student Home (default screen) === */
  .sh-grid { display:grid; grid-template-columns:minmax(0,1.15fr) 280px; gap:20px; align-items:start; margin-bottom:20px; }
  .sh-hero { background:var(--surface); border:1px solid var(--line); border-radius:var(--radius); padding:24px; box-shadow:var(--shadow); }
  .sh-eyebrow { display:inline-flex; align-items:center; gap:6px; background:var(--good-soft); color:var(--good); border-radius:999px; padding:5px 11px; font-size:11px; font-weight:800; letter-spacing:.03em; margin-bottom:12px; }
  .sh-hero h2 { font-size:21px; font-weight:800; letter-spacing:-.3px; margin:0 0 6px; line-height:1.2; }
  .sh-hero p { color:var(--muted); font-size:13.5px; margin:0 0 16px; line-height:1.5; }
  .sh-actions { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-top:16px; }
  .sh-action { border:1px solid var(--line); background:#fff; border-radius:var(--radius-sm); padding:14px 12px; min-height:88px; display:flex; flex-direction:column; justify-content:space-between; cursor:pointer; transition:all .15s ease; text-align:left; font-family:inherit; }
  .sh-action:hover { border-color:var(--brand); box-shadow:var(--shadow); transform:translateY(-1px); }
  .sh-action.primary { background:var(--brand); border-color:var(--brand); color:#fff; }
  .sh-action b { font-size:13px; font-weight:700; display:block; margin-bottom:4px; }
  .sh-action span { font-size:11.5px; color:var(--muted); line-height:1.35; }
  .sh-action.primary span { color:rgba(255,255,255,.75); }
  .sh-readiness { background:var(--surface); border:1px solid var(--line); border-radius:var(--radius); padding:18px; }
  .sh-readiness .lbl { font-size:10px; font-weight:800; text-transform:uppercase; letter-spacing:.07em; color:var(--muted); margin-bottom:4px; }
  .sh-readiness .exam-name { font-size:22px; font-weight:800; margin:4px 0 2px; }
  .sh-readiness .exam-sub { font-size:12px; color:var(--muted); margin:0 0 12px; }
  .sh-bar { height:8px; background:var(--line); border-radius:999px; overflow:hidden; margin:10px 0 8px; }
  .sh-bar-fill { height:100%; background:var(--good); border-radius:999px; transition:width .6s ease; }
  .sh-badge-row { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-bottom:20px; }
  .sh-badge { background:var(--surface); border:1px solid var(--line); border-radius:var(--radius-sm); padding:14px; }
  .sh-badge b { display:block; font-size:20px; font-weight:800; margin-bottom:3px; }
  .sh-badge span { font-size:12px; color:var(--muted); }
  .sh-section-title { font-size:15px; font-weight:800; margin:0 0 12px; }
  .sh-panel { background:var(--surface); border:1px solid var(--line); border-radius:var(--radius); padding:20px; margin-bottom:20px; box-shadow:var(--shadow-sm); }
  .sh-flow-step { display:grid; grid-template-columns:34px minmax(0,1fr) auto; gap:12px; align-items:center; padding:13px 0; border-bottom:1px solid var(--line); }
  .sh-flow-step:last-child { border-bottom:0; }
  .sh-flow-dot { width:30px; height:30px; border-radius:8px; background:var(--brand-soft); color:var(--brand); display:flex; align-items:center; justify-content:center; font-weight:800; font-size:12px; flex-shrink:0; }
  .sh-flow-step b { font-size:13px; font-weight:700; display:block; margin-bottom:3px; }
  .sh-flow-step span { font-size:12px; color:var(--muted); }
  .sh-two-col { display:grid; grid-template-columns:1.1fr .9fr; gap:16px; align-items:start; margin-bottom:20px; }
  .sh-tools-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
  .sh-tool-group { background:var(--surface-soft); border:1px solid var(--line); border-radius:var(--radius-sm); padding:13px; }
  .sh-tool-group b { font-size:12px; font-weight:800; display:block; margin-bottom:8px; color:var(--ink); }
  .sh-chips { display:flex; flex-wrap:wrap; gap:6px; }
  .sh-chip { font-size:11px; font-weight:700; background:#fff; border:1px solid var(--line); border-radius:999px; padding:5px 9px; color:var(--ink-soft); cursor:pointer; transition:all .12s ease; }
  .sh-chip:hover { background:var(--brand-soft); color:var(--brand); border-color:var(--brand); }
  .sh-exam-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:10px; }
  .sh-exam-item { background:var(--surface-soft); border:1px solid var(--line); border-radius:var(--radius-sm); padding:12px; }
  .sh-exam-item b { font-size:12px; font-weight:800; display:block; margin-bottom:6px; }
  .sh-tag { display:inline-flex; align-items:center; white-space:nowrap; border-radius:999px; padding:4px 9px; font-size:11px; font-weight:800; }
  .sh-tag.blue { background:var(--brand-soft); color:var(--brand); }
  .sh-tag.green { background:var(--good-soft); color:var(--good); }
  .sh-tag.amber { background:var(--warn-soft); color:var(--warn); }
  .sh-tag.violet { background:var(--purple-soft); color:var(--purple); }
  .sh-tag.red { background:var(--pink-soft); color:var(--pink); }

  /* === Role Switcher === */
  .role-switcher { display:flex; align-items:center; gap:3px; padding:4px; background:var(--bg); border-radius:10px; border:1px solid var(--line); margin-bottom:16px; }
  .role-btn { flex:1; padding:7px 6px; border:0; background:none; border-radius:7px; font-size:11px; font-weight:700; color:var(--muted); cursor:pointer; font-family:inherit; transition:all .15s ease; white-space:nowrap; text-align:center; }
  .role-btn.active { background:#fff; color:var(--brand); box-shadow:0 1px 4px rgba(15,23,41,.1); }

  /* === Source Citations in chat === */
  .msg { padding:12px 14px; border-radius:10px; max-width:88%; font-size:13.5px; line-height:1.5; margin-bottom:4px; }
  .msg.you { background:var(--brand); color:#fff; margin-left:auto; border-bottom-right-radius:3px; }
  .msg.ai { background:var(--surface-soft); border:1px solid var(--line); margin-right:auto; border-bottom-left-radius:3px; }
  .msg-sources { display:flex; flex-wrap:wrap; gap:5px; margin-top:8px; padding-top:8px; border-top:1px solid rgba(0,0,0,.07); }
  .msg-source-pill { font-size:11px; font-weight:700; background:var(--good-soft); color:var(--good); border-radius:999px; padding:4px 8px; }
  .msg-confidence { font-size:11px; color:var(--muted); margin-top:5px; font-style:italic; }

  /* === Mobile bottom nav (5 tabs) === */
  .mobile-bottom-nav { display:none; }
  @media (max-width:850px) {
    main { padding-bottom:80px !important; }
    .sh-grid { grid-template-columns:1fr; }
    .sh-actions { grid-template-columns:repeat(2,1fr); }
    .sh-two-col { grid-template-columns:1fr; }
    .sh-tools-grid { grid-template-columns:repeat(2,1fr); }
    .sh-exam-grid { grid-template-columns:1fr; }
    .mobile-bottom-nav {
      display:flex; position:fixed; left:0; right:0; bottom:0;
      background:rgba(255,255,255,.97); backdrop-filter:blur(12px);
      -webkit-backdrop-filter:blur(12px);
      border-top:1px solid var(--line); z-index:10;
      box-shadow:0 -4px 20px rgba(15,23,41,.08);
      padding:0 4px; padding-bottom:env(safe-area-inset-bottom,0);
    }
    .mobile-bottom-nav button {
      flex:1; display:flex; flex-direction:column; align-items:center;
      gap:3px; padding:9px 4px 11px;
      color:var(--muted); font-size:9px; font-weight:700;
      letter-spacing:.02em; background:none; border:0;
      font-family:inherit; cursor:pointer; transition:color .15s ease;
    }
    .mobile-bottom-nav button.active { color:var(--brand); }
    .mobile-bottom-nav button .nav-ico { font-size:19px; line-height:1; margin-bottom:1px; }
  }
  @media (max-width:600px) {
    .sh-tools-grid { grid-template-columns:1fr; }
    .sh-badge-row { grid-template-columns:repeat(3,1fr); }
  }
</style>
</head>
<body>
<div class="app">
  <header>
    <div style="display:flex; align-items:center; gap:14px;">
      <button class="burger" id="burger" aria-label="menu">☰</button>
      <div class="brand">
        <div class="brand-mark">पा</div>
        <div>
          <h1>AI Pathshala <span class="ver">v0.11</span></h1>
          <p class="tag">A multilingual AI teacher for every student</p>
        </div>
      </div>
    </div>
    <div class="auth-corner" id="auth-corner">
      <!-- Notifications bell (E2). Hidden until the user is signed in
           and a member of at least one org. renderAuthCorner() inserts
           the identity span next to this. -->
      <button id="notif-bell" class="notif-bell" title="Notifications"
              style="display:none;">
        <span class="bell-icon">🔔</span>
        <span class="notif-badge" id="notif-badge" style="display:none;">0</span>
      </button>
    </div>
  </header>

  <!-- Notifications drawer (E2) -->
  <aside class="notif-drawer" id="notif-drawer" style="display:none;">
    <div class="notif-drawer-header">
      <h3>Notifications</h3>
      <div class="notif-drawer-actions">
        <button class="btn-text" id="notif-mark-all">Mark all read</button>
        <button class="modal-close" id="notif-close">×</button>
      </div>
    </div>
    <div class="notif-list" id="notif-list">
      <div class="notif-empty">No notifications yet.</div>
    </div>
  </aside>

  <aside id="sidebar">
    <!-- Role switcher — changes home screen without mixing all dashboards -->
    <div style="padding:14px 14px 0;">
      <div class="role-switcher" id="role-switcher">
        <button class="role-btn active" data-role="student">Student</button>
        <button class="role-btn" data-role="teacher">Teacher</button>
        <button class="role-btn" data-role="parent">Parent</button>
        <button class="role-btn" data-role="admin">Admin</button>
      </div>
    </div>

    <div class="nav-section">Daily</div>
    <button class="nav-item primary active" data-module="home">
      <span class="ico">🏠</span><span>Home</span>
    </button>
    <button class="nav-item primary" data-module="path">
      <span class="ico">🗺️</span><span>Study Plan</span>
    </button>
    <button class="nav-item primary" data-module="quizmaker">
      <span class="ico">🧪</span><span>Tests &amp; PYQ</span>
    </button>
    <button class="nav-item primary" data-module="chat">
      <span class="ico">💬</span><span>Ask Doubt</span>
    </button>

    <div class="nav-group" data-group="create">
      <button class="nav-group-header" type="button" aria-expanded="false">
        <span class="chev"></span><span>Create</span><span class="count">4</span>
      </button>
      <div class="nav-group-body">
        <button class="nav-item" data-module="studio">
          <span class="ico">✨</span><span>Video Studio</span>
        </button>
        <button class="nav-item" data-module="explainer">
          <span class="ico">💡</span><span>Explainer</span>
        </button>
        <button class="nav-item" data-module="library">
          <span class="ico">📚</span><span>My Library</span>
        </button>
        <button class="nav-item" data-module="notes">
          <span class="ico">📝</span><span>Notes</span>
        </button>
      </div>
    </div>

    <div class="nav-group" data-group="study">
      <button class="nav-group-header" type="button" aria-expanded="false">
        <span class="chev"></span><span>Study</span><span class="count">4</span>
      </button>
      <div class="nav-group-body">
        <button class="nav-item" data-module="flashcards">
          <span class="ico">🗂️</span><span>Flashcards</span>
        </button>
        <button class="nav-item" data-module="recap">
          <span class="ico">🎧</span><span>Audio Recap</span>
        </button>
        <button class="nav-item" data-module="match">
          <span class="ico">🎮</span><span>Match Game</span>
        </button>
        <button class="nav-item" data-module="curriculum">
          <span class="ico">📖</span><span>Curriculum Map</span>
        </button>
      </div>
    </div>

    <div class="nav-group" data-group="tutor">
      <button class="nav-group-header" type="button" aria-expanded="false">
        <span class="chev"></span><span>Tutor</span><span class="count">6</span>
      </button>
      <div class="nav-group-body">
        <button class="nav-item" data-module="live">
          <span class="ico">🎤</span><span>Live Lecture</span>
        </button>
        <button class="nav-item" data-module="voice">
          <span class="ico">🎙️</span><span>Voice Tutor</span>
        </button>
        <button class="nav-item" data-module="essay">
          <span class="ico">✍️</span><span>Essay Grader</span>
        </button>
        <button class="nav-item" data-module="mathvision">
          <span class="ico">🔢</span><span>Math Check</span>
        </button>
        <button class="nav-item" data-module="interview">
          <span class="ico">🎯</span><span>Mock Interview</span>
        </button>
        <button class="nav-item" data-module="adaptive">
          <span class="ico">🧠</span><span>Adaptive Practice</span>
        </button>
        <button class="nav-item" data-module="practice">
          <span class="ico">📝</span><span>Practice Tests</span>
        </button>
      </div>
    </div>

    <div class="nav-spacer"></div>

    <div class="nav-footer">
      <div class="nav-section tight">School &amp; Org</div>
      <button class="nav-item" data-module="teacher">
        <span class="ico">🎓</span><span>Teacher Studio</span>
      </button>
      <button class="nav-item" data-module="parent">
        <span class="ico">👨‍👩‍👧</span><span>Parent View</span>
      </button>
      <button class="nav-item" data-module="school">
        <span class="ico">🏫</span><span>School / Coaching</span>
      </button>
    </div>
  </aside>

  <main>
    <!-- ===== STUDENT HOME (default first screen) ===== -->
    <section id="mod-home" class="module active">
      <h2 class="page-title">Welcome back</h2>
      <p class="page-sub" id="home-sub">Your personalised learning hub.</p>

      <!-- Role-specific home panels (shown/hidden by role switcher JS) -->
      <div id="home-student">
        <!-- Exam hub + readiness -->
        <div class="sh-grid">
          <div class="sh-hero">
            <div class="sh-eyebrow">🎯 One goal at a time</div>
            <h2 id="sh-today-goal">Choose your active exam or course below to get your daily plan.</h2>
            <p id="sh-today-sub">The app should feel calm: one goal, one next action, and four clear ways to continue.</p>
            <div class="sh-actions">
              <button class="sh-action primary" onclick="showModule('path')">
                <b>Continue Learning</b>
                <span>Resume next planned lesson</span>
              </button>
              <button class="sh-action" onclick="showModule('studio')">
                <b>Create / Upload</b>
                <span>Video Studio, Explainer, Library</span>
              </button>
              <button class="sh-action" onclick="showModule('quizmaker')">
                <b>Practice / Test</b>
                <span>Quiz, PYQ, mock exam</span>
              </button>
              <button class="sh-action" onclick="showModule('chat')">
                <b>Ask Doubt</b>
                <span>Source-grounded chat or voice</span>
              </button>
            </div>
          </div>
          <div class="sh-readiness" id="sh-exam-card">
            <div class="lbl">Active exam / course</div>
            <div class="exam-name" id="sh-exam-name">—</div>
            <p class="exam-sub" id="sh-exam-sub">Select a goal to activate</p>
            <div class="sh-bar"><div class="sh-bar-fill" id="sh-bar-fill" style="width:0%"></div></div>
            <span class="sh-tag amber" id="sh-readiness-tag">Not set</span>
            <div style="margin-top:14px;">
              <select id="sh-goal-picker" style="width:100%;padding:9px 12px;border:1.5px solid var(--line);border-radius:8px;font-family:inherit;font-size:13px;background:#fff;">
                <option value="">— Choose your goal —</option>
                <optgroup label="School">
                  <option value="cbse10">CBSE Class 10</option>
                  <option value="cbse12">CBSE Class 12</option>
                  <option value="icse10">ICSE Class 10</option>
                  <option value="state_board">State Board</option>
                </optgroup>
                <optgroup label="Competitive Exams">
                  <option value="upsc">UPSC CSE</option>
                  <option value="ssc_cgl">SSC CGL</option>
                  <option value="banking">Banking (IBPS/SBI)</option>
                  <option value="railways">Railways (RRB)</option>
                  <option value="defence">Defence (NDA/CDS)</option>
                  <option value="teaching">Teaching (CTET)</option>
                </optgroup>
                <optgroup label="College Entrances">
                  <option value="jee">JEE Main / Advanced</option>
                  <option value="neet">NEET UG</option>
                  <option value="cuet">CUET</option>
                  <option value="gate">GATE</option>
                  <option value="cat">CAT</option>
                  <option value="clat">CLAT</option>
                </optgroup>
                <optgroup label="Career">
                  <option value="placement">Campus Placements</option>
                  <option value="ugc_net">UGC NET / CSIR NET</option>
                  <option value="phd">PhD / Research</option>
                </optgroup>
              </select>
            </div>
          </div>
        </div>

        <!-- Progress badges -->
        <div class="sh-badge-row">
          <div class="sh-badge">
            <b id="sh-streak">0 days</b>
            <span>study streak</span>
          </div>
          <div class="sh-badge">
            <b id="sh-lessons-count">0</b>
            <span>lessons created</span>
          </div>
          <div class="sh-badge">
            <b id="sh-time-today">—</b>
            <span>study today</span>
          </div>
        </div>

        <!-- Daily flow + Practice -->
        <div class="sh-two-col">
          <div class="sh-panel">
            <div class="sh-section-title">Daily Study Flow</div>
            <p style="color:var(--muted);font-size:13px;margin:0 0 14px;">Simple sequence: learn → practice → review. Complete all three for maximum retention.</p>
            <div class="sh-flow-step">
              <div class="sh-flow-dot">1</div>
              <div>
                <b>Watch or read an explainer</b>
                <span>Generate from any topic, PDF, or NCERT chapter</span>
              </div>
              <span class="sh-tag blue">Learn</span>
            </div>
            <div class="sh-flow-step">
              <div class="sh-flow-dot">2</div>
              <div>
                <b>Solve practice questions</b>
                <span>Quiz, flashcards, or PYQ from your material</span>
              </div>
              <span class="sh-tag violet">Test</span>
            </div>
            <div class="sh-flow-step">
              <div class="sh-flow-dot">3</div>
              <div>
                <b>Ask one doubt with source</b>
                <span>AI tutor answers from your uploaded material</span>
              </div>
              <span class="sh-tag green">Review</span>
            </div>
          </div>
          <div class="sh-panel">
            <div class="sh-section-title">Practice &amp; Readiness</div>
            <div class="sh-flow-step">
              <div class="sh-flow-dot" style="background:var(--warn-soft);color:var(--warn);">P</div>
              <div>
                <b>Quick mock test</b>
                <span>20 questions · timed · see your weak areas</span>
              </div>
              <button class="btn-ghost" onclick="showModule('quizmaker')" style="font-size:12px;padding:6px 10px;">Start</button>
            </div>
            <div class="sh-flow-step">
              <div class="sh-flow-dot" style="background:var(--purple-soft);color:var(--purple);">F</div>
              <div>
                <b>Flashcard revision</b>
                <span>Spaced repetition — cards due for review</span>
              </div>
              <button class="btn-ghost" onclick="showModule('flashcards')" style="font-size:12px;padding:6px 10px;">Review</button>
            </div>
            <div class="sh-flow-step">
              <div class="sh-flow-dot" style="background:var(--good-soft);color:var(--good);">Q</div>
              <div>
                <b>PYQ practice</b>
                <span>Previous year questions for your exam</span>
              </div>
              <button class="btn-ghost" onclick="showModule('quizmaker')" style="font-size:12px;padding:6px 10px;">Practice</button>
            </div>
          </div>
        </div>

        <!-- More Tools — all existing modules accessible here -->
        <div class="sh-panel">
          <div class="sh-section-title">More Tools</div>
          <p style="color:var(--muted);font-size:13px;margin:0 0 14px;">All existing PadhaiApp modules — grouped for clarity. Tap any to open.</p>
          <div class="sh-tools-grid">
            <div class="sh-tool-group">
              <b>✨ Create</b>
              <div class="sh-chips">
                <button class="sh-chip" onclick="showModule('studio')">Video Studio</button>
                <button class="sh-chip" onclick="showModule('explainer')">Explainer</button>
                <button class="sh-chip" onclick="showModule('library')">My Library</button>
                <button class="sh-chip" onclick="showModule('notes')">Notes</button>
              </div>
            </div>
            <div class="sh-tool-group">
              <b>📚 Study</b>
              <div class="sh-chips">
                <button class="sh-chip" onclick="showModule('flashcards')">Flashcards</button>
                <button class="sh-chip" onclick="showModule('recap')">Audio Recap</button>
                <button class="sh-chip" onclick="showModule('match')">Match Game</button>
                <button class="sh-chip" onclick="showModule('curriculum')">Curriculum Map</button>
              </div>
            </div>
            <div class="sh-tool-group">
              <b>🧪 Practice</b>
              <div class="sh-chips">
                <button class="sh-chip" onclick="showModule('quizmaker')">Quiz Maker</button>
                <button class="sh-chip" onclick="showModule('quizmaker')">Mock Tests</button>
                <button class="sh-chip" onclick="showModule('quizmaker')">PYQ Practice</button>
              </div>
            </div>
            <div class="sh-tool-group">
              <b>💬 Tutor</b>
              <div class="sh-chips">
                <button class="sh-chip" onclick="showModule('chat')">Doubt Chat</button>
                <button class="sh-chip" onclick="showModule('voice')">Voice Tutor</button>
                <button class="sh-chip" onclick="showModule('live')">Live Lecture</button>
              </div>
            </div>
            <div class="sh-tool-group">
              <b>🗺️ Planning</b>
              <div class="sh-chips">
                <button class="sh-chip" onclick="showModule('path')">Learning Path</button>
                <button class="sh-chip" onclick="showModule('curriculum')">Curriculum Map</button>
              </div>
            </div>
            <div class="sh-tool-group">
              <b>🏫 School &amp; Org</b>
              <div class="sh-chips">
                <button class="sh-chip" onclick="showModule('teacher')">Teacher Studio</button>
                <button class="sh-chip" onclick="showModule('parent')">Parent View</button>
                <button class="sh-chip" onclick="showModule('school')">School Portal</button>
              </div>
            </div>
          </div>
        </div>

        <!-- All-India exam & board coverage registry -->
        <div class="sh-panel">
          <div class="sh-section-title">All-India Exam &amp; Board Registry</div>
          <p style="color:var(--muted);font-size:13px;margin:0 0 14px;">Content packs for every major Indian board and exam path. Select your goal above to activate the right pack.</p>
          <div class="sh-exam-grid">
            <div class="sh-exam-item">
              <b>School Boards</b>
              <div class="sh-chips">
                <span class="sh-chip">CBSE</span><span class="sh-chip">ICSE / ISC</span><span class="sh-chip">NIOS</span>
                <span class="sh-chip">All State Boards</span><span class="sh-chip">SCERT + NCERT</span>
              </div>
            </div>
            <div class="sh-exam-item">
              <b>Government Exams</b>
              <div class="sh-chips">
                <span class="sh-chip">UPSC</span><span class="sh-chip">SSC</span><span class="sh-chip">Banking</span>
                <span class="sh-chip">Railways</span><span class="sh-chip">Defence</span><span class="sh-chip">State PSC</span>
              </div>
            </div>
            <div class="sh-exam-item">
              <b>College Entrances</b>
              <div class="sh-chips">
                <span class="sh-chip">JEE</span><span class="sh-chip">NEET</span><span class="sh-chip">CUET</span>
                <span class="sh-chip">GATE</span><span class="sh-chip">CLAT</span><span class="sh-chip">CAT</span>
              </div>
            </div>
            <div class="sh-exam-item">
              <b>Career &amp; Research</b>
              <div class="sh-chips">
                <span class="sh-chip">Placements</span><span class="sh-chip">UGC NET</span>
                <span class="sh-chip">CSIR NET</span><span class="sh-chip">PhD</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Source-grounded tutor demo -->
        <div class="sh-panel">
          <div class="sh-section-title">Source-Grounded AI Tutor</div>
          <p style="color:var(--muted);font-size:13px;margin:0 0 14px;">Every answer shows where it came from. Cite source, page, and confidence — not the open internet.</p>
          <div id="sh-demo-chat" style="display:flex;flex-direction:column;gap:10px;">
            <div class="msg you">Why are Fundamental Rights enforceable but DPSP not directly enforceable?</div>
            <div class="msg ai">
              Fundamental Rights are justiciable — enforceable through constitutional remedies under Article 32. DPSP under Part IV guides state policy but cannot be enforced in court, though they are fundamental to governance.
              <div class="msg-sources">
                <span class="msg-source-pill">NCERT Polity p.42</span>
                <span class="msg-source-pill">Laxmikanth ch.7</span>
                <span class="msg-source-pill">UPSC PYQ 2017</span>
              </div>
              <div class="msg-confidence">Confidence: 0.94 · Verified answer</div>
            </div>
          </div>
          <div style="margin-top:14px;">
            <button class="primary" onclick="showModule('chat')" style="width:auto;padding:10px 20px;font-size:13px;">Open AI Tutor →</button>
          </div>
        </div>
      </div><!-- end #home-student -->

      <!-- Teacher home panel -->
      <div id="home-teacher" style="display:none;">
        <div class="sh-panel">
          <div class="sh-section-title">🎓 Teacher Dashboard</div>
          <p style="color:var(--muted);font-size:13px;margin:0 0 16px;">Create lessons, manage classes, track student progress, and run assignments.</p>
          <div class="sh-tools-grid" style="grid-template-columns:repeat(2,1fr);">
            <div class="sh-tool-group">
              <b>Create Content</b>
              <div class="sh-chips">
                <button class="sh-chip" onclick="showModule('studio')">Video Studio</button>
                <button class="sh-chip" onclick="showModule('teacher')">Teacher Studio</button>
                <button class="sh-chip" onclick="showModule('explainer')">Explainer</button>
              </div>
            </div>
            <div class="sh-tool-group">
              <b>Manage School</b>
              <div class="sh-chips">
                <button class="sh-chip" onclick="showModule('school')">School Portal</button>
                <button class="sh-chip" onclick="showModule('school')">Classes</button>
                <button class="sh-chip" onclick="showModule('school')">Assignments</button>
                <button class="sh-chip" onclick="showModule('school')">Attendance</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Parent home panel -->
      <div id="home-parent" style="display:none;">
        <div class="sh-panel">
          <div class="sh-section-title">👨‍👩‍👧 Parent Dashboard</div>
          <p style="color:var(--muted);font-size:13px;margin:0 0 16px;">Monitor your child's learning progress, linked lessons, and school updates.</p>
          <div style="display:flex;gap:10px;flex-wrap:wrap;">
            <button class="primary" onclick="showModule('parent')" style="width:auto;padding:10px 20px;">View Child Progress →</button>
            <button class="btn-ghost" onclick="showModule('school')" style="padding:10px 20px;">School Updates</button>
          </div>
        </div>
      </div>

      <!-- Admin home panel -->
      <div id="home-admin" style="display:none;">
        <div class="sh-panel">
          <div class="sh-section-title">⚙️ Admin Console</div>
          <p style="color:var(--muted);font-size:13px;margin:0 0 16px;">Manage organisation, roles, billing, compliance, and audit logs.</p>
          <div style="display:flex;gap:10px;flex-wrap:wrap;">
            <button class="primary" onclick="showModule('school')" style="width:auto;padding:10px 20px;">School / Org Portal →</button>
          </div>
        </div>
      </div>
    </section>

    <!-- ===== VIDEO STUDIO (PRD §15 — unified Create → Customize → Generate → Result) ===== -->
    <section id="mod-studio" class="module">
      <div class="hero">
        <h2>Video Studio — one place for every video you want to make.</h2>
        <p>Upload a page, scan a photo, or just type the topic. Pick who it's for, what kind of video, and what language. We'll generate it.</p>
        <div class="hero-features">
          <span class="hero-chip">🎬 9 video modes</span>
          <span class="hero-chip">🌐 10 Indian languages</span>
          <span class="hero-chip">🎯 8 audience types</span>
          <span class="hero-chip">📐 16:9 · 9:16 · 1:1</span>
        </div>
      </div>

      <!-- Step rail (PRD §15 Screens 1-5) -->
      <div class="vs-steps" id="vs-steps">
        <div class="vs-step active" data-step="1"><span class="vs-num">1</span>Source</div>
        <div class="vs-step" data-step="2"><span class="vs-num">2</span>Customize</div>
        <div class="vs-step" data-step="3"><span class="vs-num">3</span>Generate</div>
        <div class="vs-step" data-step="4"><span class="vs-num">4</span>Learn</div>
      </div>

      <!-- STEP 1 — Source -->
      <div class="card vs-panel" id="vs-step-1">
        <label>What do you want to make a video from?</label>
        <div class="vs-source-grid">
          <button type="button" class="vs-source-btn active" data-source="topic">
            <span class="ico">📝</span><span class="lbl">Type a topic</span>
            <span class="sub">No upload — just say what to explain</span>
          </button>
          <button type="button" class="vs-source-btn" data-source="file">
            <span class="ico">📄</span><span class="lbl">Upload PDF / image</span>
            <span class="sub">Textbook page, notes, scan, slide</span>
          </button>
        </div>

        <div id="vs-source-topic" class="vs-source-input">
          <label>Topic</label>
          <input type="text" id="vs-topic" placeholder="e.g. Photosynthesis, Pythagoras theorem, Diabetes awareness">
        </div>
        <div id="vs-source-file" class="vs-source-input" style="display:none;">
          <label>File (PDF, PNG, JPG)</label>
          <input type="file" id="vs-file" accept="image/*,.pdf,.png,.jpg,.jpeg">
        </div>

        <button type="button" class="primary" id="vs-go-customize">Next: customize →</button>
      </div>

      <!-- STEP 2 — Customize (PRD §15 Screen 3) -->
      <div class="card vs-panel" id="vs-step-2" style="display:none;">
        <div class="row">
          <div>
            <label>Video mode</label>
            <select id="vs-mode"></select>
            <p class="vs-hint" id="vs-mode-hint"></p>
          </div>
          <div>
            <label>Who is this for?</label>
            <select id="vs-user-type">
              <option value="student">Student</option>
              <option value="teacher">Teacher</option>
              <option value="parent">Parent</option>
              <option value="professor">Professor</option>
              <option value="coaching">Coaching institute</option>
              <option value="school_admin">School admin</option>
              <option value="professional">Professional</option>
              <option value="general">General audience</option>
            </select>
          </div>
        </div>

        <div class="row">
          <div>
            <label>Age</label>
            <select id="vs-age">
              <option value="5">3-6 (KG)</option>
              <option value="9">7-10 (Primary)</option>
              <option value="13" selected>11-13 (Middle)</option>
              <option value="15">14-16 (Secondary)</option>
              <option value="18">17-18 (Board / Competitive)</option>
              <option value="22">19-25 (College)</option>
              <option value="30">25+ (Professional)</option>
            </select>
          </div>
          <div>
            <label>Class / level</label>
            <select id="vs-grade">
              <option>Nursery / KG</option>
              <option>Class 1</option><option>Class 2</option><option>Class 3</option>
              <option>Class 4</option><option>Class 5</option><option>Class 6</option>
              <option>Class 7</option><option selected>Class 8</option><option>Class 9</option>
              <option>Class 10</option><option>Class 11</option><option>Class 12</option>
              <option>College</option><option>NEET / JEE</option>
              <option>Professional</option><option>General public</option>
            </select>
          </div>
        </div>

        <div class="row">
          <div>
            <label>Language</label>
            <select id="vs-language">
              <option value="en" selected>English</option>
              <option value="hi">हिन्दी (Hindi)</option>
              <option value="mr">मराठी (Marathi)</option>
              <option value="ta">தமிழ் (Tamil)</option>
              <option value="te">తెలుగు (Telugu)</option>
              <option value="bn">বাংলা (Bengali)</option>
              <option value="gu">ગુજરાતી (Gujarati)</option>
              <option value="kn">ಕನ್ನಡ (Kannada)</option>
              <option value="ml">മലയാളം (Malayalam)</option>
              <option value="pa">ਪੰਜਾਬੀ (Punjabi)</option>
            </select>
          </div>
          <div>
            <label>Tone (optional override)</label>
            <select id="vs-tone">
              <option value="">Auto (based on audience)</option>
              <option value="friendly">Friendly</option>
              <option value="fun">Fun</option>
              <option value="serious">Serious</option>
              <option value="professional">Professional</option>
              <option value="storytelling">Storytelling</option>
              <option value="cartoon">Cartoon</option>
              <option value="exam_focused">Exam focused</option>
            </select>
          </div>
        </div>

        <div class="row">
          <div>
            <label>Duration</label>
            <select id="vs-duration">
              <option value="">Auto (mode default)</option>
              <option value="30">30 seconds</option>
              <option value="60">1 minute</option>
              <option value="120">2 minutes</option>
              <option value="300">5 minutes</option>
              <option value="420">7 minutes</option>
              <option value="600">10 minutes</option>
            </select>
          </div>
          <div>
            <label>Output format</label>
            <select id="vs-format">
              <option value="16:9" selected>16:9 — YouTube / classroom</option>
              <option value="9:16">9:16 — Reel / Shorts</option>
              <option value="1:1">1:1 — Social square</option>
            </select>
          </div>
        </div>

        <div class="vs-nav">
          <button type="button" class="btn-ghost" id="vs-back-1">← Back</button>
          <button type="button" class="primary" id="vs-generate">⚡ Generate video</button>
        </div>
        <div class="status" id="vs-customize-status"></div>
      </div>

      <!-- STEP 3 — Generation Progress (PRD §15 Screen 4) -->
      <div class="card vs-panel" id="vs-step-3" style="display:none;">
        <div class="vs-progress-head">
          <div class="vs-progress-title" id="vs-progress-title">Generating your video…</div>
          <div class="vs-progress-meta" id="vs-progress-meta"></div>
        </div>
        <div class="vs-progress-bar"><div class="vs-progress-fill" id="vs-progress-fill"></div></div>
        <ul class="vs-progress-list" id="vs-progress-list">
          <li data-step="analyzing_document">Analyzing document</li>
          <li data-step="understanding_topic">Understanding topic</li>
          <li data-step="creating_script">Creating script</li>
          <li data-step="creating_storyboard">Creating storyboard</li>
          <li data-step="generating_voice">Generating voice</li>
          <li data-step="rendering_video">Rendering animation</li>
          <li data-step="preparing_quiz">Preparing quiz</li>
          <li data-step="uploading">Uploading</li>
        </ul>
        <div class="status" id="vs-progress-status"></div>
      </div>

      <!-- STEP 4 — Result (PRD §15 Screen 5) -->
      <div class="card vs-panel" id="vs-step-4" style="display:none;">
        <div class="vs-result-head">
          <div class="vs-result-title" id="vs-result-title">Your video is ready</div>
          <div class="vs-result-meta" id="vs-result-meta"></div>
        </div>
        <video id="vs-player" controls preload="metadata"
               style="width:100%; border-radius:12px; background:#000; margin:14px 0;"></video>
        <div class="vs-actions">
          <button class="btn-ghost" id="vs-act-easier">⬇ Make easier</button>
          <button class="btn-ghost" id="vs-act-advanced">⬆ Make advanced</button>
          <button class="btn-ghost" id="vs-act-lang">🌐 Change language</button>
          <button class="btn-ghost" id="vs-act-short">📱 Create short</button>
          <button class="btn-ghost" id="vs-act-exam">🧪 Exam focused</button>
          <button class="btn-ghost" id="vs-act-download">⬇ Download MP4</button>
          <button class="btn-ghost" id="vs-act-audio">🎧 Audio only</button>
          <button class="btn-ghost" id="vs-act-subs">💬 Subtitles (.srt)</button>
          <button class="btn-ghost" id="vs-act-share">📲 Share on WhatsApp</button>
          <button class="btn-ghost" id="vs-act-chat">🤔 Ask a doubt</button>
        </div>
        <div class="vs-result-foot">
          <button type="button" class="btn-ghost" id="vs-new">↻ Start a new video</button>
        </div>
      </div>
    </section>

    <!-- ===== CREATE LESSON ===== -->
    <section id="mod-create" class="module">
      <div class="hero">
        <h2>Turn any page into a video lesson — in your language.</h2>
        <p>Upload a textbook scan, notes, or PDF. Pick your language and grade level. Get back a narrated animated lesson with quiz and follow-up chat.</p>
        <div class="hero-features">
          <span class="hero-chip">🌐 10 Indian languages</span>
          <span class="hero-chip">⚡ Ready in 60 seconds</span>
          <span class="hero-chip">🎓 NCERT-aligned</span>
          <span class="hero-chip">🆓 Free tier</span>
        </div>
      </div>

      <div class="card">
        <form id="f">
          <label>Textbook page (image or PDF)</label>
          <input type="file" name="image" accept="image/*,.pdf,.png,.jpg,.jpeg" required>

          <div class="row">
            <div>
              <label>Language</label>
              <select name="language">
                <option value="en">English</option>
                <option value="hi">हिन्दी (Hindi)</option>
                <option value="ta">தமிழ் (Tamil)</option>
                <option value="te">తెలుగు (Telugu)</option>
                <option value="bn">বাংলা (Bengali)</option>
                <option value="mr">मराठी (Marathi)</option>
                <option value="gu">ગુજરાતી (Gujarati)</option>
                <option value="kn">ಕನ್ನಡ (Kannada)</option>
                <option value="ml">മലയാളം (Malayalam)</option>
                <option value="pa">ਪੰਜਾਬੀ (Punjabi)</option>
              </select>
            </div>
            <div>
              <label>Grade level</label>
              <select name="level">
                <option value="kg">Kindergarten</option>
                <option value="primary">Primary (3-5)</option>
                <option value="middle" selected>Middle (6-8)</option>
                <option value="secondary">Secondary (9-12)</option>
                <option value="neet_jee">NEET / JEE / UPSC</option>
                <option value="eli5">Explain like I'm 5</option>
              </select>
            </div>
          </div>

          <label class="check">
            <input type="checkbox" name="teacher" checked>
            Show animated teacher on the whiteboard
          </label>
          <label class="check">
            <input type="checkbox" name="include_quiz" checked>
            Add quiz at the end (questions baked into the video)
          </label>

          <button type="submit" class="primary" id="go">Generate lesson</button>
          <div class="status" id="status"></div>
        </form>
      </div>

      <video id="v" controls hidden></video>

      <div class="card" id="chat-inline" style="display:none;">
        <label>Ask a follow-up question about this lesson</label>
        <div class="messages" id="messages-inline"></div>
        <div class="chat-input">
          <input type="text" id="q-inline" placeholder="e.g. Why do plants need sunlight?">
          <button id="send-inline" class="primary" style="margin:0;">Ask</button>
        </div>
      </div>
    </section>

    <!-- ===== EXPLAINER ===== -->
    <section id="mod-explainer" class="module">
      <h2 class="page-title">Explainer</h2>
      <p class="page-sub">Type any concept — get an instant structured explanation. No file upload needed. Perfect for quick doubts before an exam.</p>

      <div class="card">
        <form id="exf">
          <label>What do you want explained?</label>
          <input type="text" id="ex-topic" placeholder="e.g. photosynthesis, quadratic formula, Newton's third law, partition of India" required>
          <div class="row" style="margin-top:12px;">
            <div>
              <label>Language</label>
              <select id="ex-lang">
                <option value="en" selected>English</option>
                <option value="hi">हिन्दी (Hindi)</option>
                <option value="mr">मराठी (Marathi)</option>
                <option value="ta">தமிழ் (Tamil)</option>
                <option value="te">తెలుగు (Telugu)</option>
                <option value="bn">বাংলা (Bengali)</option>
                <option value="gu">ગુજરાતી (Gujarati)</option>
                <option value="kn">ಕನ್ನಡ (Kannada)</option>
                <option value="ml">മലയാളം (Malayalam)</option>
                <option value="pa">ਪੰਜਾਬੀ (Punjabi)</option>
              </select>
            </div>
            <div>
              <label>Level</label>
              <select id="ex-level">
                <option value="kg">Kindergarten (3-6)</option>
                <option value="eli5">Explain like I'm 5</option>
                <option value="primary">Primary (grades 3-5)</option>
                <option value="middle" selected>Middle school (grades 6-8)</option>
                <option value="secondary">Secondary / board exam</option>
                <option value="neet_jee">NEET / JEE rigour</option>
              </select>
            </div>
          </div>
          <div class="ex-suggest-row">
            <span class="ex-suggest-label">Quick try:</span>
            <button type="button" class="ex-chip" data-topic="Photosynthesis">Photosynthesis</button>
            <button type="button" class="ex-chip" data-topic="Pythagoras theorem">Pythagoras theorem</button>
            <button type="button" class="ex-chip" data-topic="Newton's third law of motion">Newton's 3rd law</button>
            <button type="button" class="ex-chip" data-topic="Quadratic formula">Quadratic formula</button>
            <button type="button" class="ex-chip" data-topic="French Revolution causes">French Revolution</button>
          </div>
          <button type="submit" class="primary" id="ex-go">⚡ Explain it</button>
          <div class="status" id="ex-status"></div>
        </form>
      </div>

      <div id="ex-output" style="display:none;">
        <div class="card ex-result">
          <div class="ex-header">
            <div class="ex-icon">💡</div>
            <div style="flex:1;">
              <div class="ex-topic" id="ex-out-topic"></div>
              <div class="ex-meta"><span id="ex-out-meta"></span> <span id="ex-out-cached" class="ex-cache-badge"></span></div>
            </div>
          </div>
          <div class="ex-oneliner" id="ex-out-oneliner"></div>
          <div class="ex-section">
            <div class="ex-section-h">📖 In plain words</div>
            <div class="ex-explanation" id="ex-out-explanation"></div>
          </div>
          <div class="ex-section">
            <div class="ex-section-h">🔑 Key points</div>
            <ul class="ex-keypoints" id="ex-out-keypoints"></ul>
          </div>
          <div class="ex-section">
            <div class="ex-section-h">🧮 Worked example</div>
            <div class="ex-example" id="ex-out-example"></div>
          </div>
          <div class="ex-section ex-analogy-section">
            <div class="ex-section-h">🎯 Think of it like…</div>
            <div class="ex-analogy" id="ex-out-analogy"></div>
          </div>
          <div class="ex-section" id="ex-mistakes-section">
            <div class="ex-section-h">⚠️ Common mistakes</div>
            <ul class="ex-mistakes" id="ex-out-mistakes"></ul>
          </div>
          <div class="qz-actions">
            <button class="btn-ghost primary" id="ex-make-video">🎬 Generate cartoon video</button>
            <button class="btn-ghost" id="ex-regen">↻ Regenerate</button>
            <button class="btn-ghost" id="ex-copy">📋 Copy as text</button>
            <button class="btn-ghost" id="ex-save-notes">📝 Save to notes</button>
          </div>
        </div>

        <div class="card ex-video-card" id="ex-video-card" style="display:none;">
          <div class="ex-section-h" style="margin-bottom:10px;">🎬 Cartoon video lesson</div>
          <div class="ex-video-status" id="ex-video-status">Queuing render…</div>
          <div class="ex-video-steps" id="ex-video-steps">
            <div class="ex-step" data-step="generate">
              <span class="ex-step-dot"></span><span>Convert explainer to 5-scene lesson</span>
            </div>
            <div class="ex-step" data-step="narrate">
              <span class="ex-step-dot"></span><span>Narrate in selected language</span>
            </div>
            <div class="ex-step" data-step="render">
              <span class="ex-step-dot"></span><span>Animate cartoon teacher + scene boards</span>
            </div>
            <div class="ex-step" data-step="encode">
              <span class="ex-step-dot"></span><span>Encode MP4</span>
            </div>
          </div>
          <video id="ex-video-player" controls preload="metadata" style="display:none; width:100%; border-radius:12px; margin-top:12px; background:#000;"></video>
          <div class="qz-actions" id="ex-video-actions" style="display:none;">
            <button class="btn-ghost" id="ex-video-download">⬇ Download MP4</button>
            <button class="btn-ghost" id="ex-video-flashcards">🗂️ Make flashcards</button>
            <button class="btn-ghost" id="ex-video-recap">🎧 Audio recap</button>
            <button class="btn-ghost" id="ex-video-chat">💬 Chat about this</button>
          </div>
        </div>
      </div>
    </section>

    <!-- ===== CHAT ===== -->
    <section id="mod-chat" class="module">
      <h2 class="page-title">💬 Ask Doubt</h2>
      <p class="page-sub">Source-grounded AI tutor — answers from your uploaded material with citations, not the open internet. Generates a lesson first if you haven't.</p>
      <div class="card">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap;">
          <div style="flex:1;">
            <label style="margin-bottom:4px;display:block;font-size:13px;font-weight:600;">Lesson ID</label>
            <input type="text" id="chat-lesson-id" placeholder="paste the lesson_id from a generated lesson" style="margin:0;">
          </div>
          <button class="btn-ghost" onclick="showModule('library')" style="white-space:nowrap;margin-top:20px;">Browse library →</button>
        </div>
        <div class="messages" id="messages-page" style="min-height:180px;max-height:420px;overflow-y:auto;border:1px solid var(--line);border-radius:10px;padding:14px;background:var(--surface-soft);display:flex;flex-direction:column;gap:6px;margin-bottom:10px;"></div>
        <div style="font-size:11.5px;color:var(--muted);margin-bottom:8px;">Answers include source citations and confidence when available.</div>
        <div class="chat-input">
          <input type="text" id="q-page" placeholder="Ask your doubt — e.g. Explain Article 21 with a UPSC example">
          <button id="send-page" class="primary" style="margin:0;">Ask</button>
        </div>
      </div>
    </section>

    <!-- ===== LIBRARY ===== -->
    <section id="mod-library" class="module">
      <h2 class="page-title">My library</h2>
      <p class="page-sub">All lessons you've generated. Click any one to re-watch or ask follow-up questions.</p>
      <div class="card" id="lib-list">
        <div class="status">Loading…</div>
      </div>
    </section>

    <!-- ===== QUIZ MAKER ===== -->
    <section id="mod-quizmaker" class="module">
      <h2 class="page-title">Quiz / Tests</h2>
      <p class="page-sub">Take the quiz from any lesson you've generated. Get instant feedback, see your score, retake to master.</p>

      <div class="card" id="qz-picker">
        <label>Lesson ID</label>
        <input type="text" id="qz-lesson-id" placeholder="paste lesson_id, or click 'Use latest' below">
        <div class="row" style="margin-top:10px;">
          <div>
            <button type="button" class="btn-ghost" id="qz-use-latest" style="margin-top:0;">
              Use my latest lesson
            </button>
          </div>
          <div>
            <button type="button" class="primary" id="qz-start" style="margin-top:0;">Start quiz</button>
          </div>
        </div>
        <div class="status" id="qz-status"></div>
      </div>

      <div id="qz-runner" style="display:none;">
        <div class="card qz-progress-card">
          <div class="qz-meta">
            <span id="qz-title" class="qz-lesson-title"></span>
            <span id="qz-counter" class="qz-counter">1 / 3</span>
          </div>
          <div class="qz-bar"><div class="qz-bar-fill" id="qz-bar-fill"></div></div>
        </div>
        <div class="card">
          <div class="qz-question" id="qz-question"></div>
          <div class="qz-options" id="qz-options"></div>
          <div class="qz-feedback" id="qz-feedback"></div>
          <div class="qz-nav">
            <button class="btn-ghost" id="qz-next" disabled>Next →</button>
          </div>
        </div>
      </div>

      <div id="qz-result" class="card" style="display:none;">
        <div class="qz-score" id="qz-score-big"></div>
        <p class="qz-score-msg" id="qz-score-msg"></p>
        <div class="qz-actions">
          <button class="btn-ghost" id="qz-retake">↻ Retake</button>
          <button class="btn-ghost" onclick="showModule('flashcards')">🗂️ Practice with flashcards</button>
          <button class="btn-ghost" onclick="showModule('recap')">🎧 Listen to recap</button>
        </div>
        <div id="qz-review"></div>
      </div>
    </section>

    <!-- ===== MATCH GAME ===== -->
    <section id="mod-match" class="module">
      <h2 class="page-title">Match Game</h2>
      <p class="page-sub">Flip pairs of cards (question + answer) until you've matched them all. Fastest student wins.</p>

      <div class="card" id="mg-picker">
        <label>Lesson ID</label>
        <input type="text" id="mg-lesson-id" placeholder="paste lesson_id, or click 'Use latest' below">
        <div class="row" style="margin-top:10px;">
          <div>
            <button type="button" class="btn-ghost" id="mg-use-latest" style="margin-top:0;">
              Use my latest lesson
            </button>
          </div>
          <div>
            <button type="button" class="primary" id="mg-start" style="margin-top:0;">Start game</button>
          </div>
        </div>
        <div class="status" id="mg-status"></div>
      </div>

      <div id="mg-runner" style="display:none;">
        <div class="card mg-progress-card">
          <div class="mg-stats">
            <span>⏱ <strong id="mg-time">0:00</strong></span>
            <span>🔁 Moves: <strong id="mg-moves">0</strong></span>
            <span>✓ Pairs: <strong id="mg-pairs">0</strong> / <span id="mg-pairs-total">0</span></span>
          </div>
        </div>
        <div class="mg-grid" id="mg-grid"></div>
      </div>

      <div id="mg-result" class="card" style="display:none;">
        <div class="qz-score" id="mg-final-time"></div>
        <p class="qz-score-msg" id="mg-final-msg"></p>
        <div class="qz-actions">
          <button class="btn-ghost" id="mg-restart">↻ Play again</button>
          <button class="btn-ghost" onclick="showModule('flashcards')">🗂️ Study cards</button>
          <button class="btn-ghost" onclick="showModule('quizmaker')">🧪 Take the quiz</button>
        </div>
      </div>
    </section>

    <!-- ===== AUDIO RECAP ===== -->
    <section id="mod-recap" class="module">
      <h2 class="page-title">Audio Recap</h2>
      <p class="page-sub">Podcast-style 60-second summary of any lesson — perfect for the bus ride home.</p>

      <div class="card" id="rc-picker">
        <label>Lesson ID</label>
        <input type="text" id="rc-lesson-id" placeholder="paste lesson_id, or click 'Use latest' below">
        <div class="row" style="margin-top:10px;">
          <div>
            <button type="button" class="btn-ghost" id="rc-use-latest" style="margin-top:0;">
              Use my latest lesson
            </button>
          </div>
          <div>
            <button type="button" class="primary" id="rc-generate" style="margin-top:0;">Generate recap</button>
          </div>
        </div>
        <div class="status" id="rc-status"></div>
      </div>

      <div id="rc-player" class="card" style="display:none;">
        <div class="rc-header">
          <div class="rc-icon">🎧</div>
          <div>
            <div class="rc-title" id="rc-title"></div>
            <div class="rc-sub">~60-second recap · spoken in your language</div>
          </div>
        </div>
        <audio id="rc-audio" controls preload="metadata" style="width:100%; margin:14px 0;"></audio>
        <details class="rc-transcript">
          <summary>Show transcript</summary>
          <p id="rc-text"></p>
        </details>
        <div class="qz-actions">
          <button class="btn-ghost" id="rc-regen">↻ Regenerate</button>
          <button class="btn-ghost" id="rc-download">⬇ Download MP3</button>
        </div>
      </div>
    </section>

    <!-- ===== NOTES ===== -->
    <section id="mod-notes" class="module">
      <h2 class="page-title">Notes</h2>
      <p class="page-sub">Jot down your own notes for any lesson. Autosaves while you type. Works offline (localStorage) and syncs when signed in.</p>

      <div class="card" id="nt-picker">
        <label>Lesson ID</label>
        <input type="text" id="nt-lesson-id" placeholder="paste lesson_id, or click 'Use latest' below">
        <div class="row" style="margin-top:10px;">
          <div>
            <button type="button" class="btn-ghost" id="nt-use-latest" style="margin-top:0;">
              Use my latest lesson
            </button>
          </div>
          <div>
            <button type="button" class="primary" id="nt-open" style="margin-top:0;">Open notes</button>
          </div>
        </div>
        <div class="status" id="nt-status"></div>
      </div>

      <div id="nt-editor" class="card" style="display:none;">
        <div class="nt-toolbar">
          <span class="nt-title" id="nt-title">Notes</span>
          <span class="nt-save-state" id="nt-save-state">All changes saved</span>
        </div>
        <textarea id="nt-textarea" placeholder="Start writing your notes here…

Tip: paste vocab, formulas, doubts to ask later. Press Tab to indent. Auto-saves every 2 seconds."></textarea>
        <div class="qz-actions">
          <button class="btn-ghost" id="nt-download">⬇ Download as .txt</button>
          <button class="btn-ghost" id="nt-clear">🗑 Clear</button>
        </div>
      </div>
    </section>

    <!-- ===== LIVE LECTURE ===== -->
    <section id="mod-live" class="module">
      <h2 class="page-title">Live Lecture</h2>
      <p class="page-sub">Tap the mic, ask anything aloud. The AI tutor listens, thinks, and speaks back — like a private 1-on-1 class.</p>

      <div class="card live-controls">
        <div class="live-mic-row">
          <button id="live-mic" class="live-mic-btn" type="button">
            <span class="live-mic-ico">🎤</span>
            <span id="live-mic-label">Tap to speak</span>
          </button>
          <div class="live-lang">
            <label for="live-lang-sel" style="margin:0;">Language</label>
            <select id="live-lang-sel">
              <option value="en-IN">English (India)</option>
              <option value="hi-IN">Hindi</option>
              <option value="mr-IN">Marathi</option>
              <option value="ta-IN">Tamil</option>
              <option value="te-IN">Telugu</option>
              <option value="bn-IN">Bengali</option>
              <option value="gu-IN">Gujarati</option>
              <option value="kn-IN">Kannada</option>
              <option value="ml-IN">Malayalam</option>
              <option value="pa-IN">Punjabi</option>
            </select>
          </div>
        </div>
        <div class="status" id="live-status">Ready. Click the mic to start.</div>
      </div>

      <div class="card" id="live-transcript-card" style="display:none;">
        <div class="card-title">Live conversation</div>
        <div id="live-transcript" class="live-transcript"></div>
        <div class="qz-actions">
          <button class="btn-ghost" id="live-clear">↻ New conversation</button>
          <button class="btn-ghost" id="live-stop-speak">⏹ Stop voice</button>
        </div>
      </div>

      <div class="card compact" style="background:#f3f4ff; border-color:#d4d6f5; margin-top:14px;">
        <p style="margin:0; font-size:13px; color:var(--ink);">
          ℹ️ <strong>How it works:</strong> Your microphone audio is transcribed in your browser (Web Speech API — works in Chrome, Edge, Safari). Only the text is sent to AI Pathshala; raw audio stays on your device. The reply is read aloud by your browser's text-to-speech, also offline.
        </p>
      </div>
    </section>

    <!-- ===== FLASHCARDS ===== -->
    <section id="mod-flashcards" class="module">
      <h2 class="page-title">Flashcards</h2>
      <p class="page-sub">Spaced-repetition deck from any lesson. Tap to flip; rate your recall to schedule the next review.</p>

      <div class="card" id="fc-picker">
        <label>Lesson ID</label>
        <input type="text" id="fc-lesson-id" placeholder="paste lesson_id, or click 'Use latest' below">
        <div class="row" style="margin-top:10px;">
          <div>
            <label>Number of cards</label>
            <select id="fc-count">
              <option value="5">5 cards</option>
              <option value="8" selected>8 cards</option>
              <option value="12">12 cards</option>
              <option value="16">16 cards</option>
            </select>
          </div>
          <div>
            <label>&nbsp;</label>
            <button type="button" class="btn-ghost" id="fc-use-latest" style="margin-top:0;">
              Use my latest lesson
            </button>
          </div>
        </div>
        <button type="button" class="primary" id="fc-generate">Generate deck</button>
        <div class="status" id="fc-status"></div>
      </div>

      <!-- Deck viewer (hidden until cards loaded) -->
      <div id="fc-deck-wrap" style="display:none;">
        <div class="fc-deck-stats" id="fc-stats"></div>
        <div class="fc-deck">
          <div class="fc-card" id="fc-card">
            <div class="fc-face fc-front">
              <div class="fc-tags" id="fc-tags-front"></div>
              <div class="fc-text" id="fc-front"></div>
              <div class="fc-hint" id="fc-hint"></div>
              <div class="fc-flip-cue">Tap or press SPACE to flip</div>
            </div>
            <div class="fc-face fc-back">
              <div class="fc-tags" id="fc-tags-back"></div>
              <div class="fc-text" id="fc-back"></div>
              <div class="fc-flip-cue">Rate how well you knew it →</div>
            </div>
          </div>
        </div>
        <div class="fc-nav">
          <button class="btn-ghost" id="fc-prev">← Prev</button>
          <span class="fc-pos" id="fc-pos">0 / 0</span>
          <button class="btn-ghost" id="fc-next">Next →</button>
        </div>
        <div class="fc-srs" id="fc-srs">
          <button class="srs-btn srs-again" data-rating="again">
            <span class="rate">Again</span>
            <span class="when" id="when-again">&lt;1d</span>
          </button>
          <button class="srs-btn srs-hard" data-rating="hard">
            <span class="rate">Hard</span>
            <span class="when" id="when-hard">2d</span>
          </button>
          <button class="srs-btn srs-good" data-rating="good">
            <span class="rate">Good</span>
            <span class="when" id="when-good">4d</span>
          </button>
          <button class="srs-btn srs-easy" data-rating="easy">
            <span class="rate">Easy</span>
            <span class="when" id="when-easy">7d</span>
          </button>
        </div>
        <div class="fc-actions">
          <button class="btn-ghost" id="fc-shuffle">🔀 Shuffle</button>
          <button class="btn-ghost" id="fc-restart">↻ Restart</button>
          <button class="btn-ghost" id="fc-export">⬇ Export Anki</button>
        </div>
      </div>
    </section>

    <!-- ===== VOICE TUTOR ===== -->
    <section id="mod-voice" class="module">
      <h2 class="page-title">Voice Tutor</h2>
      <p class="page-sub">Speak your doubt in any Indian language. Optionally link a lesson so answers are grounded in your material.</p>

      <!-- Lesson context (optional) -->
      <div class="card" id="vt-lesson-card">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
          <div style="flex:1;">
            <label style="margin-bottom:4px;display:block;font-size:13px;font-weight:600;">Lesson ID <span style="font-weight:400;color:var(--muted);">(optional — grounds answers in your material)</span></label>
            <input type="text" id="vt-lesson-id" placeholder="Paste a lesson_id, or leave blank for general tutoring" style="margin:0;">
          </div>
          <button class="btn-ghost" onclick="showModule('library')" style="white-space:nowrap;margin-top:20px;">Browse library →</button>
        </div>
      </div>

      <!-- Mic controls -->
      <div class="card live-controls">
        <div class="live-mic-row">
          <button id="vt-mic" class="live-mic-btn" type="button">
            <span class="live-mic-ico">🎙️</span>
            <span id="vt-mic-label">Tap to speak</span>
          </button>
          <div class="live-lang">
            <label for="vt-lang-sel" style="margin:0;">Language</label>
            <select id="vt-lang-sel">
              <option value="en-IN">English (India)</option>
              <option value="hi-IN">Hindi</option>
              <option value="mr-IN">Marathi</option>
              <option value="ta-IN">Tamil</option>
              <option value="te-IN">Telugu</option>
              <option value="bn-IN">Bengali</option>
              <option value="gu-IN">Gujarati</option>
              <option value="kn-IN">Kannada</option>
              <option value="ml-IN">Malayalam</option>
              <option value="pa-IN">Punjabi</option>
            </select>
          </div>
        </div>
        <div class="status" id="vt-status">Ready. Click the mic to start.</div>
      </div>

      <!-- Conversation transcript -->
      <div class="card" id="vt-transcript-card" style="display:none;">
        <div class="card-title">Voice conversation</div>
        <div id="vt-transcript" class="live-transcript"></div>
        <div class="qz-actions">
          <button class="btn-ghost" id="vt-clear">↻ New conversation</button>
          <button class="btn-ghost" id="vt-stop-speak">⏹ Stop voice</button>
        </div>
      </div>

      <div class="card compact" style="background:#f3f4ff; border-color:#d4d6f5; margin-top:14px;">
        <p style="margin:0; font-size:13px; color:var(--ink);">
          ℹ️ <strong>How it works:</strong> Your microphone audio is transcribed in your browser (Web Speech API — works in Chrome, Edge, Safari). Only the text is sent to AI Pathshala; raw audio stays on your device. The reply is read aloud by your browser's text-to-speech, also offline.
        </p>
      </div>
    </section>

    <!-- ===== ESSAY GRADER ===== -->
    <section id="mod-essay" class="module">
      <h2 class="page-title">Essay / Answer Grader</h2>
      <p class="page-sub">Write a UPSC, JEE, or board descriptive answer. AI scores it against the rubric, gives per-criterion feedback and model answer.</p>

      <div class="card" id="eg-form-card">
        <div class="row">
          <div style="flex:1;">
            <label>Exam</label>
            <select id="eg-exam">
              <option value="upsc_mains">UPSC Mains (GS)</option>
              <option value="upsc_essay">UPSC Essay Paper</option>
              <option value="jee_adv_descriptive">JEE Advanced (Descriptive)</option>
              <option value="neet_descriptive">NEET (Descriptive)</option>
              <option value="cat_va">CAT Verbal Ability</option>
              <option value="cbse_class12_eng">CBSE Class 12 English</option>
              <option value="cbse_class10_eng">CBSE Class 10 English</option>
              <option value="generic">General / Other</option>
            </select>
          </div>
          <div style="flex:1;">
            <label>Paper / Rubric</label>
            <select id="eg-rubric-sel"><option value="">Loading…</option></select>
          </div>
        </div>
        <label>Your answer</label>
        <textarea id="eg-text" rows="8" placeholder="Write your answer here (minimum 50 words for accurate grading)…" style="width:100%;box-sizing:border-box;font-family:inherit;font-size:14px;padding:10px;border:1px solid var(--line);border-radius:8px;resize:vertical;"></textarea>
        <button type="button" class="primary" id="eg-submit" style="margin-top:10px;">Grade my answer</button>
        <div class="status" id="eg-status"></div>
      </div>

      <div id="eg-result" style="display:none;">
        <div class="card" id="eg-score-card">
          <div class="card-title">AI Score</div>
          <div id="eg-score-display" style="font-size:32px;font-weight:700;color:var(--brand);margin:8px 0;"></div>
          <div id="eg-criteria-list"></div>
        </div>
        <div class="card" style="margin-top:12px;">
          <div class="card-title">Feedback</div>
          <div id="eg-feedback" style="white-space:pre-wrap;font-size:14px;line-height:1.6;"></div>
        </div>
        <div class="card" style="margin-top:12px;" id="eg-model-answer-card">
          <div class="card-title">Model Answer</div>
          <div id="eg-model-answer" style="white-space:pre-wrap;font-size:14px;line-height:1.6;"></div>
        </div>
        <div class="qz-actions" style="margin-top:12px;">
          <button class="btn-ghost" id="eg-try-again">↻ Try another answer</button>
        </div>
      </div>
    </section>

    <!-- ===== MATH VISION ===== -->
    <section id="mod-mathvision" class="module">
      <h2 class="page-title">Math Check</h2>
      <p class="page-sub">Paste a URL of your handwritten math solution. AI reads it, extracts the steps, and marks the first wrong step.</p>

      <div class="card" id="mv-form-card">
        <label>Image URL of handwritten solution</label>
        <input type="url" id="mv-image-url" placeholder="https://i.imgur.com/… or any public JPG/PNG URL">
        <div style="font-size:12px;color:var(--muted);margin-top:4px;">
          Must be a <strong>public</strong> URL (AI reads it directly). Upload to
          <a href="https://imgur.com" target="_blank" rel="noopener" style="color:var(--brand);">Imgur</a>,
          Google Photos (shared link), or any CDN.
          Supports JPG, PNG, WEBP. Max resolution ~4000×4000 px.
        </div>
        <div style="margin-top:12px;">
          <label>Language</label>
          <select id="mv-lang" style="width:auto;">
            <option value="en">English</option>
            <option value="hi">Hindi</option>
          </select>
        </div>
        <button type="button" class="primary" id="mv-submit" style="margin-top:12px;">Check my work</button>
        <div class="status" id="mv-status"></div>
      </div>

      <div id="mv-result" style="display:none;">
        <div class="card">
          <div class="card-title">Extracted steps</div>
          <div id="mv-steps" style="font-family:monospace;font-size:13px;line-height:2;"></div>
        </div>
        <div class="card" style="margin-top:12px;" id="mv-validation-card">
          <div class="card-title">Step validation</div>
          <div id="mv-validation"></div>
        </div>
        <div class="qz-actions" style="margin-top:12px;">
          <button class="btn-ghost" id="mv-validate-btn">▶ Validate steps</button>
          <button class="btn-ghost" id="mv-try-again">↻ Check another</button>
        </div>
      </div>
    </section>

    <!-- ===== MOCK INTERVIEW ===== -->
    <section id="mod-interview" class="module">
      <h2 class="page-title">Mock Interview</h2>
      <p class="page-sub">AI simulates a UPSC / JEE / placement interview. Answer questions aloud or by typing; get scored feedback at the end.</p>

      <div class="card" id="mi-start-card">
        <label>Interview track</label>
        <select id="mi-track">
          <option value="upsc_personality">UPSC Personality Test (200 marks)</option>
          <option value="jee_counseling">JEE Advanced Counselling Readiness</option>
          <option value="iit_placement">IIT/NIT Campus Placement (Tech + HR)</option>
          <option value="neet_pg">NEET PG Departmental Interview</option>
          <option value="mba_admission">IIM / ISB Admission Interview</option>
          <option value="generic">General / Other</option>
        </select>
        <button type="button" class="primary" id="mi-start" style="margin-top:12px;">Start interview</button>
        <div class="status" id="mi-status"></div>
      </div>

      <div id="mi-session" style="display:none;">
        <div class="card" id="mi-question-card">
          <div class="card-title" id="mi-turn-label">Question 1</div>
          <div id="mi-question" style="font-size:16px;font-weight:600;line-height:1.5;margin-bottom:14px;"></div>
          <div id="mi-mic-row" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
            <button id="mi-mic" class="live-mic-btn" style="min-width:140px;" type="button">
              <span class="live-mic-ico">🎙️</span>
              <span id="mi-mic-label">Tap to answer</span>
            </button>
            <span style="color:var(--muted);font-size:13px;">or</span>
            <input type="text" id="mi-text-input" placeholder="Type your answer instead…" style="flex:1;margin:0;">
            <button class="primary" id="mi-submit-answer" style="white-space:nowrap;margin:0;">Submit →</button>
          </div>
          <div class="status" id="mi-answer-status" style="margin-top:8px;"></div>
        </div>

        <div id="mi-transcript-log" style="margin-top:12px;"></div>

        <div class="qz-actions" style="margin-top:12px;">
          <button class="btn-ghost" id="mi-end">⏹ End &amp; get report</button>
        </div>
      </div>

      <div id="mi-report" class="card" style="display:none;margin-top:12px;">
        <div class="card-title">Interview Report</div>
        <div id="mi-overall-score" style="font-size:32px;font-weight:700;color:var(--brand);margin:8px 0;"></div>
        <div id="mi-report-body" style="white-space:pre-wrap;font-size:14px;line-height:1.6;"></div>
        <div class="qz-actions" style="margin-top:14px;">
          <button class="btn-ghost" id="mi-restart">↻ Start new interview</button>
        </div>
      </div>
    </section>

    <!-- ===== ADAPTIVE PRACTICE ===== -->
    <section id="mod-adaptive" class="module">
      <h2 class="page-title">Adaptive Practice</h2>
      <p class="page-sub">Tell us your syllabus pack. AI builds a personalised question set — harder where you're strong, easier where you need support.</p>

      <div class="card" id="ap-form-card">
        <div class="row">
          <div style="flex:1;">
            <label>Syllabus pack</label>
            <select id="ap-pack"><option value="">Loading exam packs…</option></select>
          </div>
          <div style="flex:1;">
            <label>Questions per session</label>
            <select id="ap-count">
              <option value="5">5 questions</option>
              <option value="10" selected>10 questions</option>
              <option value="15">15 questions</option>
              <option value="20">20 questions</option>
            </select>
          </div>
        </div>
        <button type="button" class="primary" id="ap-create" style="margin-top:10px;">Create adaptive pack</button>
        <div class="status" id="ap-status"></div>
      </div>

      <div id="ap-list-card" class="card" style="display:none;margin-top:12px;">
        <div class="card-title">Your packs</div>
        <div id="ap-list"></div>
        <button class="btn-ghost" id="ap-refresh" style="margin-top:10px;">↻ Refresh</button>
      </div>
    </section>

    <!-- ===== PRACTICE TESTS ===== -->
    <section id="mod-practice" class="module">
      <h2 class="page-title">Practice Tests</h2>
      <p class="page-sub">Generate a timed practice test for your exam. Questions are pulled from our bank and adapted to your weak topics.</p>

      <div class="card" id="pt-form-card">
        <div class="row">
          <div style="flex:1;">
            <label>Exam</label>
            <select id="pt-exam">
              <option value="jee_main">JEE Main</option>
              <option value="jee_advanced">JEE Advanced</option>
              <option value="neet">NEET</option>
              <option value="upsc">UPSC CSE (General)</option>
              <option value="upsc_pre">UPSC Prelims</option>
              <option value="upsc_mains">UPSC Mains</option>
              <option value="cat">CAT</option>
              <option value="gate">GATE</option>
              <option value="generic">Generic / Other</option>
            </select>
          </div>
          <div style="flex:1;">
            <label>Subject</label>
            <input type="text" id="pt-subject" placeholder="e.g. Physics, General Studies, Quantitative Aptitude" maxlength="64">
          </div>
        </div>
        <div class="row" style="margin-top:10px;">
          <div style="flex:1;">
            <label>Time limit</label>
            <select id="pt-minutes">
              <option value="10">10 minutes (~7 Qs)</option>
              <option value="20">20 minutes (~13 Qs)</option>
              <option value="30" selected>30 minutes (~20 Qs)</option>
              <option value="45">45 minutes (~30 Qs)</option>
              <option value="60">60 minutes (~40 Qs)</option>
              <option value="120">2 hours (~80 Qs)</option>
            </select>
          </div>
          <div style="flex:1; display:flex; align-items:flex-end; padding-bottom:2px;">
            <button type="button" class="primary" id="pt-create" style="width:100%;">Generate test</button>
          </div>
        </div>
        <div class="status" id="pt-status"></div>
      </div>

      <!-- Active test UI -->
      <div id="pt-test-card" class="card" style="display:none; margin-top:12px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
          <div class="card-title" id="pt-test-title">Test</div>
          <div id="pt-timer" style="font-size:1.1em; font-weight:600; color:var(--accent);"></div>
        </div>
        <div id="pt-questions"></div>
        <button type="button" class="primary" id="pt-submit" style="margin-top:16px;">Submit test</button>
        <div class="status" id="pt-submit-status"></div>
      </div>

      <!-- Score report -->
      <div id="pt-report-card" class="card" style="display:none; margin-top:12px;">
        <div class="card-title">Your result</div>
        <div id="pt-report"></div>
        <button type="button" class="btn-ghost" id="pt-new" style="margin-top:12px;">+ New test</button>
      </div>

      <!-- Past tests -->
      <div id="pt-history-card" class="card" style="margin-top:12px;">
        <div class="card-title">Recent tests</div>
        <div id="pt-history"><em style="color:var(--muted-text);">No tests yet.</em></div>
        <button class="btn-ghost" id="pt-refresh" style="margin-top:10px;">↻ Refresh</button>
      </div>
    </section>

    <!-- ===== LEARNING PATH ===== -->
    <section id="mod-path" class="module">
      <h2 class="page-title">Personalised learning path</h2>
      <p class="page-sub">Tell us your class, subjects, and how much time you have. We'll build a week-by-week plan from the NCERT catalogue + your library.</p>

      <div class="card" id="lp-form">
        <form id="lpf">
          <div class="row">
            <div>
              <label>Class</label>
              <select name="student_class" id="lp-class">
                <option value="6">Class 6</option>
                <option value="7">Class 7</option>
                <option value="8" selected>Class 8</option>
                <option value="9">Class 9</option>
                <option value="10">Class 10</option>
                <option value="11">Class 11</option>
                <option value="12">Class 12</option>
              </select>
            </div>
            <div>
              <label>Subjects (select 1+)</label>
              <div style="display:flex; gap:14px; padding-top:8px;">
                <label class="check" style="margin:0;"><input type="checkbox" name="subj" value="Maths" checked> Maths</label>
                <label class="check" style="margin:0;"><input type="checkbox" name="subj" value="Science" checked> Science</label>
                <label class="check" style="margin:0;"><input type="checkbox" name="subj" value="Social"> Social</label>
              </div>
            </div>
          </div>

          <div class="row">
            <div>
              <label>Plan length</label>
              <select id="lp-weeks">
                <option value="2">2 weeks</option>
                <option value="3">3 weeks</option>
                <option value="4" selected>4 weeks (recommended)</option>
                <option value="6">6 weeks</option>
                <option value="8">8 weeks</option>
              </select>
            </div>
            <div>
              <label>Daily time</label>
              <select id="lp-daily">
                <option value="15">15 minutes</option>
                <option value="20">20 minutes</option>
                <option value="30" selected>30 minutes</option>
                <option value="45">45 minutes</option>
                <option value="60">60 minutes (1 hour)</option>
              </select>
            </div>
          </div>

          <label>Weak topics or areas you want to focus on (optional, comma-separated)</label>
          <input type="text" id="lp-focus" placeholder="e.g. trigonometry, photosynthesis, force and motion">

          <button type="submit" class="primary" id="lp-go">Build my plan</button>
          <div class="status" id="lp-status"></div>
        </form>
      </div>

      <div id="lp-output"></div>
    </section>

    <!-- ===== TEACHER STUDIO ===== -->
    <section id="mod-teacher" class="module">
      <h2 class="page-title">Teacher / Tutor studio</h2>
      <p class="page-sub">Upload one chapter; generate the full lesson pack — multilingual videos, lesson plan, homework, test paper, answer key.</p>

      <div class="card">
        <form id="tf">
          <label>Chapter (PDF, PPT, or scanned notes)</label>
          <input type="file" id="tf-image" accept="image/*,.pdf,.png,.jpg,.jpeg" required>

          <label>Generate in these languages (multi-select)</label>
          <div style="display:flex; flex-wrap:wrap; gap:8px;">
            <label class="check" style="margin:0;"><input type="checkbox" name="tl" value="en" checked> English</label>
            <label class="check" style="margin:0;"><input type="checkbox" name="tl" value="hi" checked> Hindi</label>
            <label class="check" style="margin:0;"><input type="checkbox" name="tl" value="mr"> Marathi</label>
            <label class="check" style="margin:0;"><input type="checkbox" name="tl" value="ta"> Tamil</label>
            <label class="check" style="margin:0;"><input type="checkbox" name="tl" value="te"> Telugu</label>
            <label class="check" style="margin:0;"><input type="checkbox" name="tl" value="bn"> Bengali</label>
          </div>

          <div class="row">
            <div>
              <label>Target grade</label>
              <select id="tf-level">
                <option value="primary">Primary (3-5)</option>
                <option value="middle" selected>Middle (6-8)</option>
                <option value="secondary">Secondary (9-12)</option>
                <option value="neet_jee">NEET / JEE / UPSC</option>
              </select>
            </div>
            <div>
              <label>Avatar tier</label>
              <select id="tf-avatar">
                <option value="cartoon">Cartoon (free)</option>
                <option value="wav2lip">Wav2Lip photoreal (M3)</option>
                <option value="synthesia">Synthesia (M4d)</option>
                <option value="heygen">HeyGen (M4b)</option>
              </select>
            </div>
          </div>

          <label class="check"><input type="checkbox" id="tf-plan" checked> Generate lesson plan (Markdown)</label>
          <label class="check"><input type="checkbox" id="tf-hw" checked> Generate homework + answer key (PDF)</label>
          <label class="check"><input type="checkbox" id="tf-test"> Generate test paper (board-exam style)</label>

          <button type="submit" class="primary" id="tgo">Generate full lesson pack</button>
          <div class="status" id="tstatus"></div>
        </form>
      </div>

      <div class="card compact">
        <p style="margin:0; color:var(--muted); font-size:13px;">
          🚧 The teacher studio reuses the lesson-generation pipeline behind the scenes. Multi-language output runs N parallel renders (one per selected language); lesson plan + homework + test paper are separate Claude calls keyed off the same source. Performance analytics is Phase 2.
        </p>
      </div>
    </section>

    <!-- ===== CURRICULUM MAP ===== -->
    <section id="mod-curriculum" class="module">
      <h2 class="page-title">Curriculum mapper</h2>
      <p class="page-sub">Browse the NCERT/CBSE chapter index, or auto-match a lesson you've generated to its position in the syllabus.</p>

      <!-- Match panel -->
      <div class="card">
        <label>Match a lesson</label>
        <input type="text" id="cm-lesson-id" placeholder="paste lesson_id, or click 'Use latest'">
        <div style="display:flex; gap:8px; margin-top:10px;">
          <button class="btn-ghost" id="cm-use-latest">Use my latest lesson</button>
          <button class="primary" id="cm-match" style="margin:0; flex:1;">Match against syllabus</button>
        </div>
        <div class="status" id="cm-status"></div>
        <div id="cm-results"></div>
      </div>

      <!-- Index browser -->
      <div class="card">
        <label>Browse the catalogue</label>
        <div class="row3">
          <select id="cm-board"><option value="">All boards</option></select>
          <select id="cm-class"><option value="">All classes</option></select>
          <select id="cm-subject"><option value="">All subjects</option></select>
        </div>
        <div id="cm-index" class="cm-index" style="margin-top:14px;"></div>
      </div>

      <div class="card compact">
        <p style="margin:0; color:var(--muted); font-size:13px;">
          🛡️ <strong>NCERT-safe:</strong> this catalogue holds only chapter titles + topic tags
          (non-copyrightable factual metadata). We never reproduce textbook content.
          See <code>AI_PATHSHALA_BLUEPRINT.md</code> §9 for the legal strategy.
          Today's seed: CBSE Class 6-10 Maths + Science (~50 chapters).
          State boards + Class 11-12 + competitive-exam syllabi expand in Phase 2.
        </p>
      </div>
    </section>

    <!-- ===== PARENT VIEW ===== -->
    <section id="mod-parent" class="module">
      <h2 class="page-title">Parent dashboard</h2>
      <p class="page-sub">Weekly progress at a glance — lessons, languages, streaks, and where to nudge next.</p>

      <!-- v0.14 E8: Linked-children switcher. Lets a parent toggle
           between "my activity" and a verified child's view. -->
      <div id="pd-children-bar" class="pd-children-bar" style="display:none;">
        <strong style="color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:0.06em;">Viewing:</strong>
        <button class="pd-child-chip active" data-child="me">My activity</button>
        <span id="pd-child-list" style="display:contents;"></span>
        <button class="pd-child-chip pd-child-add" id="pd-link-child-btn">+ Link a child</button>
      </div>

      <div class="card compact" style="background:#f0fdf4; border-color:#bbf7d0;">
        <p style="margin:0; font-size:13px; color:var(--ink);">
          🛡️ <strong>DPDP §9 compliant:</strong> link a child by email — they
          must click a verification link before you can view their data.
          Either side can revoke at any time. Consent timestamp + IP are
          logged in the audit trail.
        </p>
      </div>

      <!-- E8 modal: link a child by email -->
      <div class="sch-modal" id="pd-link-modal" style="display:none;">
        <div class="card">
          <button class="modal-close" data-close-pd>×</button>
          <h3>Link a child</h3>
          <p style="color:var(--muted); font-size:13px; margin:0 0 14px;">
            The child you're linking must already have an AI Pathshala
            account. They'll receive a verification link they must click
            before you can view their progress.
          </p>
          <form id="pd-link-form">
            <label>Child's email</label>
            <input type="email" name="other_email" required>
            <label>Relation <span class="hint" style="color:var(--muted); font-weight:400;">(optional)</span></label>
            <select name="relation">
              <option value="">— prefer not to say —</option>
              <option value="father">Father</option>
              <option value="mother">Mother</option>
              <option value="guardian">Guardian</option>
              <option value="other">Other</option>
            </select>
            <input type="hidden" name="role" value="parent">
            <button class="primary" type="submit">Send verification link</button>
            <div class="status" id="pd-link-status"></div>
          </form>
        </div>
      </div>

      <div id="pd-tiles" class="pd-tiles"></div>

      <div class="card">
        <label>Activity — last 7 days</label>
        <div id="pd-chart" class="pd-chart"></div>
      </div>

      <div class="row">
        <div class="card">
          <label>Languages used</label>
          <div id="pd-langs"></div>
        </div>
        <div class="card">
          <label>Grade levels practiced</label>
          <div id="pd-levels"></div>
        </div>
      </div>

      <div class="card">
        <label>Recent lessons</label>
        <div id="pd-recent"></div>
      </div>
    </section>

    <!-- ===== SCHOOL / COACHING ===== -->
    <section id="mod-school" class="module">
      <h2 class="page-title">🏫 School / Coaching</h2>
      <p class="page-sub">
        Institutional portal — roster, class groups, assignments, and
        per-class video activity. <strong>Schools:</strong> ₹30/student/year ·
        <strong>Coaching:</strong> ₹100/student/year on M4 photoreal tier.
      </p>

      <!-- Signed-out / no-orgs landing — shown by JS when /api/orgs/me is empty -->
      <div id="sch-landing" class="card" style="display:none;">
        <div class="sch-hero">
          <h3>Create your school or coaching organisation</h3>
          <p>One owner, one school. Add students and teachers, group them
            into classes, and assign videos. Takes 2 minutes.</p>
        </div>
        <form id="sch-create-form" class="sch-form">
          <div class="row">
            <div>
              <label>Organisation name</label>
              <input type="text" name="name" required minlength="2" maxlength="120"
                     placeholder="St. Paul's School, Bangalore">
            </div>
            <div>
              <label>Type</label>
              <select name="kind">
                <option value="school" selected>School</option>
                <option value="coaching">Coaching institute</option>
                <option value="ngo">NGO / non-profit</option>
                <option value="gov">Government / state board</option>
              </select>
            </div>
          </div>
          <div class="row">
            <div>
              <label>Board / curriculum <span class="hint">(optional)</span></label>
              <input type="text" name="board" placeholder="CBSE, ICSE, state, IB, JEE…">
            </div>
            <div>
              <label>City <span class="hint">(optional)</span></label>
              <input type="text" name="city" placeholder="Bangalore">
            </div>
          </div>
          <label>Contact email <span class="hint">(for invoices &amp; pilot ops)</span></label>
          <input type="email" name="contact_email" placeholder="principal@school.edu.in">
          <button class="primary" type="submit">Create organisation</button>
          <div class="status" id="sch-create-status"></div>
        </form>

        <div class="sch-not-signed-in" style="display:none;">
          <p style="color:var(--muted); margin-top:18px;">
            Sign in or create an account to set up your school portal.
            <button class="btn-text" id="sch-open-signin">Sign in →</button>
          </p>
        </div>
      </div>

      <!-- Main dashboard — populated by JS after /api/orgs/me returns an org -->
      <div id="sch-dashboard" style="display:none;">
        <!-- Header -->
        <div class="card sch-header">
          <div>
            <h3 id="sch-org-name">—</h3>
            <p class="sch-org-meta" id="sch-org-meta">—</p>
          </div>
          <div class="sch-plan-pill" id="sch-plan-pill">pilot</div>
        </div>

        <!-- KPI tiles -->
        <div class="sch-kpis">
          <div class="sch-kpi">
            <div class="lbl">Students</div>
            <div class="val" id="sch-kpi-students">0</div>
          </div>
          <div class="sch-kpi">
            <div class="lbl">Teachers</div>
            <div class="val" id="sch-kpi-teachers">0</div>
          </div>
          <div class="sch-kpi">
            <div class="lbl">Classes</div>
            <div class="val" id="sch-kpi-classes">0</div>
          </div>
          <div class="sch-kpi">
            <div class="lbl">Assignments</div>
            <div class="val" id="sch-kpi-assignments">0</div>
          </div>
          <div class="sch-kpi sch-kpi-accent">
            <div class="lbl">Videos this week</div>
            <div class="val" id="sch-kpi-videos">0</div>
          </div>
        </div>

        <!-- Tab bar -->
        <div class="sch-tabs">
          <button class="sch-tab active" data-sch-tab="members">Members</button>
          <button class="sch-tab" data-sch-tab="classes">Classes</button>
          <button class="sch-tab" data-sch-tab="assignments">Assignments</button>
          <button class="sch-tab" data-sch-tab="attendance">Attendance</button>
          <button class="sch-tab" data-sch-tab="timetable">Timetable</button>
          <button class="sch-tab" data-sch-tab="exams">Exams</button>
          <button class="sch-tab" data-sch-tab="fees">Fees</button>
        </div>

        <!-- MEMBERS TAB -->
        <div class="sch-tab-panel" id="sch-panel-members">
          <div class="card">
            <div class="sch-toolbar">
              <h4>Roster</h4>
              <div class="sch-toolbar-actions">
                <label class="btn-ghost sch-upload-btn">
                  📤 Upload CSV
                  <input type="file" id="sch-roster-csv" accept=".csv" hidden>
                </label>
                <button class="primary" id="sch-add-member">+ Add one</button>
              </div>
            </div>
            <p class="sch-hint">
              CSV columns: <code>email</code> (required) ·
              <code>name</code> · <code>role</code> (admin / teacher / student) ·
              <code>class</code>. New classes auto-created.
            </p>
            <div class="sch-table-wrap">
              <table class="sch-table" id="sch-members-table">
                <thead>
                  <tr><th>Name</th><th>Email</th><th>Role</th><th>Class</th><th>Joined</th></tr>
                </thead>
                <tbody></tbody>
              </table>
            </div>
            <div class="status" id="sch-members-status"></div>
          </div>
        </div>

        <!-- CLASSES TAB -->
        <div class="sch-tab-panel" id="sch-panel-classes" style="display:none;">
          <div class="card">
            <div class="sch-toolbar">
              <h4>Class groups</h4>
              <button class="primary" id="sch-add-class">+ New class</button>
            </div>
            <div class="sch-class-grid" id="sch-class-grid"></div>
          </div>
        </div>

        <!-- ASSIGNMENTS TAB -->
        <div class="sch-tab-panel" id="sch-panel-assignments" style="display:none;">
          <div class="card">
            <div class="sch-toolbar">
              <h4>Assignments</h4>
              <button class="primary" id="sch-add-assignment">+ Create assignment</button>
            </div>
            <div class="sch-assignments" id="sch-assignments-list"></div>
          </div>
        </div>

        <!-- E3 ATTENDANCE TAB -->
        <div class="sch-tab-panel" id="sch-panel-attendance" style="display:none;">
          <div class="card">
            <div class="sch-toolbar">
              <h4>Daily attendance</h4>
              <div class="sch-toolbar-actions" style="gap:10px;">
                <select id="sch-att-class" style="padding:8px 12px; border:1.5px solid var(--line); border-radius:8px;"></select>
                <input type="date" id="sch-att-date" style="padding:8px 12px; border:1.5px solid var(--line); border-radius:8px;">
                <button class="primary" id="sch-att-save">Save attendance</button>
              </div>
            </div>
            <div id="sch-att-grid" class="sch-att-grid"></div>
            <div class="status" id="sch-att-status"></div>
          </div>
        </div>

        <!-- E6 TIMETABLE TAB -->
        <div class="sch-tab-panel" id="sch-panel-timetable" style="display:none;">
          <div class="card">
            <div class="sch-toolbar">
              <h4>Class timetable</h4>
              <div class="sch-toolbar-actions">
                <select id="sch-tt-class" style="padding:8px 12px; border:1.5px solid var(--line); border-radius:8px;"></select>
              </div>
            </div>
            <div id="sch-tt-grid" class="sch-tt-grid"></div>
            <p class="sch-hint" style="margin-top:12px;">
              <strong>Editing:</strong> bulk-replace via JSON POST in v0.13.
              Inline editing UI lands in v0.14 — for now use the API directly
              or upload a CSV via <code>POST /api/orgs/{id}/classes/{cid}/timetable</code>.
            </p>
          </div>
        </div>

        <!-- E4 EXAMS TAB -->
        <div class="sch-tab-panel" id="sch-panel-exams" style="display:none;">
          <div class="card">
            <div class="sch-toolbar">
              <h4>Exams</h4>
              <div class="sch-toolbar-actions">
                <select id="sch-ex-class-filter" style="padding:8px 12px; border:1.5px solid var(--line); border-radius:8px;">
                  <option value="">All classes</option>
                </select>
                <button class="primary" id="sch-add-exam">+ Create exam</button>
              </div>
            </div>
            <div id="sch-exams-list"></div>
          </div>
        </div>

        <!-- E5 FEES TAB -->
        <div class="sch-tab-panel" id="sch-panel-fees" style="display:none;">
          <!-- Summary cards -->
          <div class="card">
            <div class="sch-toolbar">
              <h4>Fees overview</h4>
              <button class="primary" id="sch-add-fee">+ Define fee</button>
            </div>
            <div id="sch-fees-summary" class="sch-fees-summary"></div>
          </div>

          <div class="card">
            <h4 style="margin:0 0 10px;">Fee structures</h4>
            <div id="sch-fees-structures"></div>
          </div>

          <div class="card">
            <h4 style="margin:0 0 10px;">Invoices</h4>
            <div class="sch-toolbar-actions" style="margin-bottom:10px;">
              <select id="sch-fees-status-filter" style="padding:8px 12px; border:1.5px solid var(--line); border-radius:8px;">
                <option value="">All statuses</option>
                <option value="pending">Pending</option>
                <option value="paid">Paid</option>
                <option value="overdue">Overdue</option>
                <option value="cancelled">Cancelled</option>
              </select>
            </div>
            <div id="sch-fees-invoices"></div>
          </div>
        </div>
      </div>

      <!-- Modals: Add Member, Add Class, Create Assignment, Create Exam, Take Exam, Create Fee -->
      <div class="sch-modal" id="sch-modal-member" style="display:none;">
        <div class="card">
          <button class="modal-close" data-close-sch>×</button>
          <h3>Add a member</h3>
          <form id="sch-member-form">
            <label>Email</label>
            <input type="email" name="email" required>
            <label>Role</label>
            <select name="role">
              <option value="student">Student</option>
              <option value="teacher">Teacher</option>
              <option value="admin">Admin</option>
            </select>
            <label>Class <span class="hint">(students only)</span></label>
            <select name="class_id" id="sch-member-class"><option value="">— none —</option></select>
            <label>Display name <span class="hint">(optional)</span></label>
            <input type="text" name="display_name">
            <button class="primary" type="submit">Add</button>
            <div class="status" id="sch-member-status"></div>
          </form>
        </div>
      </div>

      <div class="sch-modal" id="sch-modal-class" style="display:none;">
        <div class="card">
          <button class="modal-close" data-close-sch>×</button>
          <h3>New class group</h3>
          <form id="sch-class-form">
            <label>Class name</label>
            <input type="text" name="name" required placeholder="Class 8A">
            <div class="row">
              <div>
                <label>Grade level</label>
                <input type="text" name="grade_level" placeholder="8">
              </div>
              <div>
                <label>Section</label>
                <input type="text" name="section" placeholder="A">
              </div>
            </div>
            <button class="primary" type="submit">Create</button>
            <div class="status" id="sch-class-status"></div>
          </form>
        </div>
      </div>

      <div class="sch-modal" id="sch-modal-assignment" style="display:none;">
        <div class="card">
          <button class="modal-close" data-close-sch>×</button>
          <h3>Create assignment</h3>
          <form id="sch-assignment-form">
            <label>Class</label>
            <select name="class_id" id="sch-assignment-class" required></select>
            <label>Title</label>
            <input type="text" name="title" required placeholder="Photosynthesis chapter">
            <label>Topic <span class="hint">(what the video should explain)</span></label>
            <input type="text" name="topic" required placeholder="Photosynthesis">
            <div class="row">
              <div>
                <label>Language</label>
                <select name="language">
                  <option value="en">English</option><option value="hi">Hindi</option>
                  <option value="mr">Marathi</option><option value="ta">Tamil</option>
                  <option value="te">Telugu</option><option value="bn">Bengali</option>
                  <option value="gu">Gujarati</option><option value="kn">Kannada</option>
                  <option value="ml">Malayalam</option><option value="pa">Punjabi</option>
                </select>
              </div>
              <div>
                <label>Level</label>
                <select name="level">
                  <option value="primary">Primary</option>
                  <option value="middle" selected>Middle</option>
                  <option value="secondary">Secondary</option>
                  <option value="neet_jee">NEET / JEE</option>
                </select>
              </div>
            </div>
            <label>Due date <span class="hint">(optional)</span></label>
            <input type="date" name="due_date">
            <label>Notes <span class="hint">(optional)</span></label>
            <textarea name="notes" rows="2" placeholder="e.g. focus on chloroplasts; quiz at end"></textarea>
            <button class="primary" type="submit">Create assignment</button>
            <div class="status" id="sch-assignment-status"></div>
          </form>
        </div>
      </div>

      <!-- E1: Per-assignment analytics drawer -->
      <div class="sch-modal" id="sch-modal-stats" style="display:none;">
        <div class="card" style="max-width:720px;">
          <button class="modal-close" data-close-sch>×</button>
          <h3>Assignment results</h3>
          <div id="sch-stats-content"></div>
        </div>
      </div>

      <!-- E4: Create exam modal -->
      <div class="sch-modal" id="sch-modal-exam-create" style="display:none;">
        <div class="card" style="max-width:640px;">
          <button class="modal-close" data-close-sch>×</button>
          <h3>Create exam</h3>
          <form id="sch-exam-form">
            <div class="row">
              <div>
                <label>Class</label>
                <select name="class_id" id="sch-exam-class" required></select>
              </div>
              <div>
                <label>Duration (min)</label>
                <input type="number" name="duration_min" min="5" max="240" value="30" required>
              </div>
            </div>
            <label>Title</label>
            <input type="text" name="title" required minlength="2" maxlength="160" placeholder="Photosynthesis chapter test">
            <label>Topic <span class="hint" style="color:var(--muted); font-weight:400;">(what the questions are about)</span></label>
            <input type="text" name="topic" required placeholder="Photosynthesis">
            <label>Subject <span class="hint" style="color:var(--muted); font-weight:400;">(optional)</span></label>
            <input type="text" name="subject" placeholder="Science">
            <label>Questions <span class="hint" style="color:var(--muted); font-weight:400;">(JSON array)</span></label>
            <textarea name="questions_json" rows="10" required style="font-family: 'Menlo', monospace; font-size:12px; width:100%; padding:10px; border:1.5px solid var(--line); border-radius:8px;"></textarea>
            <p class="sch-hint">
              Schema: <code>[{"kind":"mcq", "q":"...", "options":{"A":"...","B":"...","C":"...","D":"..."}, "answer":"B", "marks":2}, {"kind":"free", "q":"...", "marks":4}]</code>
            </p>
            <button class="primary" type="submit">Create exam</button>
            <div class="status" id="sch-exam-status"></div>
          </form>
        </div>
      </div>

      <!-- E4: Student exam-taking modal -->
      <div class="sch-modal" id="sch-modal-exam-take" style="display:none;">
        <div class="card" style="max-width:640px; max-height:90vh; overflow-y:auto;">
          <div class="exam-header">
            <h3 id="exam-take-title"></h3>
            <div id="exam-timer" class="exam-timer">--:--</div>
          </div>
          <div class="exam-warning" id="exam-anticheat-warning" style="display:none;">
            ⚠️ Tab switch detected. Stay on this tab — switches are logged.
          </div>
          <div id="exam-questions"></div>
          <button class="primary" id="exam-submit-btn" style="width:100%; padding:14px; margin-top:14px;">Submit exam</button>
          <div class="status" id="exam-take-status"></div>
        </div>
      </div>

      <!-- E4: Attempts review modal (teacher) -->
      <div class="sch-modal" id="sch-modal-exam-attempts" style="display:none;">
        <div class="card" style="max-width:840px;">
          <button class="modal-close" data-close-sch>×</button>
          <h3 id="exam-attempts-title">Exam attempts</h3>
          <div id="exam-attempts-content"></div>
        </div>
      </div>

      <!-- E5 Fee create modal -->
      <div class="sch-modal" id="sch-modal-fee-create" style="display:none;">
        <div class="card" style="max-width:520px;">
          <button class="modal-close" data-close-sch>×</button>
          <h3>Define a fee</h3>
          <form id="sch-fee-form">
            <label>Name</label>
            <input type="text" name="name" required minlength="2" maxlength="120"
                   placeholder="Class 8 Annual Fee">
            <div class="row">
              <div>
                <label>Amount (₹)</label>
                <input type="number" name="amount_rupees" min="1" required
                       placeholder="24000">
              </div>
              <div>
                <label>Due date</label>
                <input type="date" name="due_date">
              </div>
            </div>
            <label>Applies to</label>
            <select name="applies_to" id="sch-fee-applies" required></select>
            <label>Notes <span class="hint" style="color:var(--muted); font-weight:400;">(optional)</span></label>
            <textarea name="notes" rows="2"></textarea>
            <button class="primary" type="submit">Create fee</button>
            <div class="status" id="sch-fee-status"></div>
          </form>
        </div>
      </div>
    </section>

  </main>
</div>

<!-- Mobile bottom nav (5 tabs — only shown at ≤850px) -->
<nav class="mobile-bottom-nav" id="mobile-bottom-nav" aria-label="Main navigation">
  <button data-module="home" class="active">
    <span class="nav-ico">🏠</span>Home
  </button>
  <button data-module="studio">
    <span class="nav-ico">✨</span>Create
  </button>
  <button data-module="quizmaker">
    <span class="nav-ico">🧪</span>Test
  </button>
  <button data-module="chat">
    <span class="nav-ico">💬</span>Tutor
  </button>
  <button data-module="home" id="mobile-more-btn">
    <span class="nav-ico">⋯</span>More
  </button>
</nav>

<!-- Auth modal -->
<div class="modal" id="auth-modal">
  <div class="card">
    <button class="modal-close" id="close-modal" type="button">×</button>
    <div class="tabs">
      <div class="tab active" data-mode="signup">Sign up</div>
      <div class="tab" data-mode="login">Log in</div>
    </div>

    <!-- SSO buttons (E7) — visible only when providers are configured -->
    <div class="sso-block" id="sso-block" style="display:none;">
      <a href="/auth/sso/google/start?next=/" class="sso-button" id="sso-google" style="display:none;">
        <span class="sso-logo">G</span> Continue with Google
      </a>
      <a href="/auth/sso/microsoft/start?next=/" class="sso-button" id="sso-microsoft" style="display:none;">
        <span class="sso-logo">⊞</span> Continue with Microsoft
      </a>
      <div class="sso-divider">or with email</div>
    </div>

    <form id="auth-form">
      <div class="signup-only">
        <label>Full name</label>
        <input type="text" name="name" id="auth-name" placeholder="Your name" maxlength="60">
      </div>

      <label>Email</label>
      <input type="email" name="email" required>
      <label>Password</label>
      <input type="password" name="password" minlength="8" required>

      <!-- DPDP §9 (India, 2023) — DOB collected at signup. When under 18,
           parent/guardian email is required and the account is locked until
           they click the verification link. -->
      <div class="signup-only">
        <label>Date of birth <span class="hint" style="color:var(--muted); font-weight:400; font-size:12px;">(used to apply child-safety protections)</span></label>
        <input type="date" name="dob" id="auth-dob" max="">

        <div class="signup-dpdp" id="signup-dpdp" style="display:none;">
          <div class="signup-locked-banner">
            <strong>Under-18 account.</strong> Per the Indian DPDP Act 2023
            §9, we need your parent or guardian to verify this account.
            We'll email them a one-time link.
          </div>
          <label>Parent / guardian email</label>
          <input type="email" name="parent_email" id="auth-parent-email">
          <div class="hint">We won't email them anything else.</div>
        </div>

        <label style="display:flex;gap:8px;align-items:center;margin-top:10px;cursor:pointer;">
          <input type="checkbox" name="terms_accepted" id="auth-terms" value="true" style="width:auto;margin:0;">
          <span style="font-size:13px;">I agree to the <a href="/terms" target="_blank" style="color:var(--brand);">Terms of Service</a> and <a href="/privacy" target="_blank" style="color:var(--brand);">Privacy Policy</a></span>
        </label>
      </div>

      <button type="submit" class="primary">Continue</button>
      <div class="status" id="auth-status"></div>
    </form>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
let token = localStorage.getItem('pathshala_token');
let email = localStorage.getItem('pathshala_email');
let mode = 'signup';
let currentLessonId = null;

// ---- module navigation (v3.1 — home-first, role switcher, mobile bottom nav) ----
let _activeRole = localStorage.getItem('padhai_role') || 'student';

function showModule(name) {
  document.querySelectorAll('.module').forEach(m => m.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const mod = $('mod-' + name);
  if (mod) mod.classList.add('active');
  const nav = document.querySelector(`#sidebar .nav-item[data-module="${name}"]`);
  if (nav) {
    nav.classList.add('active');
    const group = nav.closest('.nav-group');
    if (group && !group.classList.contains('open')) {
      group.classList.add('open');
      group.querySelector('.nav-group-header')?.setAttribute('aria-expanded', 'true');
    }
  }
  // Sync mobile bottom nav tab
  document.querySelectorAll('#mobile-bottom-nav button[data-module]').forEach(b => {
    b.classList.toggle('active', b.dataset.module === name);
  });
  // Special: "More" tab should highlight when on any non-primary module
  const primaryMobile = ['home','studio','quizmaker','chat'];
  const moreBtn = $('mobile-more-btn');
  if (moreBtn && !primaryMobile.includes(name)) {
    document.querySelectorAll('#mobile-bottom-nav button').forEach(b => b.classList.remove('active'));
    moreBtn.classList.add('active');
  }
  $('sidebar').classList.remove('open');
  if (name === 'library') loadLibrary();
  if (name === 'school' && typeof schBoot === 'function') schBoot();
  if (name === 'home') initHome();
  document.dispatchEvent(new CustomEvent('moduleShow', { detail: name }));
}

document.querySelectorAll('.nav-item').forEach(n => {
  n.addEventListener('click', () => showModule(n.dataset.module));
});
// Mobile bottom nav buttons
document.querySelectorAll('#mobile-bottom-nav button[data-module]').forEach(b => {
  b.addEventListener('click', () => {
    const mod = b.dataset.module;
    if (b.id === 'mobile-more-btn') {
      // "More" scrolls to the More Tools section on the home screen
      showModule('home');
      setTimeout(() => {
        const t = document.querySelector('.sh-tools-grid');
        if (t) t.scrollIntoView({behavior:'smooth', block:'start'});
      }, 100);
    } else {
      showModule(mod);
    }
  });
});
// Group header toggles collapse/expand
document.querySelectorAll('.nav-group-header').forEach(h => {
  h.addEventListener('click', () => {
    const group = h.parentElement;
    const open = group.classList.toggle('open');
    h.setAttribute('aria-expanded', String(open));
  });
});
$('burger').onclick = () => $('sidebar').classList.toggle('open');

// ---- Role switcher ----
function setRole(role) {
  _activeRole = role;
  localStorage.setItem('padhai_role', role);
  document.querySelectorAll('.role-btn').forEach(b => b.classList.toggle('active', b.dataset.role === role));
  // Show the right home panel
  ['student','teacher','parent','admin'].forEach(r => {
    const el = $('home-' + r);
    if (el) el.style.display = r === role ? '' : 'none';
  });
  const titles = { student:'Welcome back', teacher:'Teacher Dashboard', parent:'Parent Overview', admin:'Admin Console' };
  const subs = {
    student:'Your personalised learning hub. One goal, one next action.',
    teacher:'Manage classes, create lessons, and track student progress.',
    parent:'Monitor your child\'s learning and school updates.',
    admin:'Manage organisation, roles, billing, and compliance.'
  };
  const pt = document.querySelector('#mod-home .page-title');
  const ps = $('home-sub');
  if (pt) pt.textContent = titles[role] || 'Home';
  if (ps) ps.textContent = subs[role] || '';
}
document.querySelectorAll('.role-btn').forEach(b => {
  b.addEventListener('click', () => setRole(b.dataset.role));
});

// ---- Goal picker → exam hub ----
const _goalMeta = {
  cbse10:     { name:'CBSE Class 10', sub:'All subjects · Boards', readiness:0 },
  cbse12:     { name:'CBSE Class 12', sub:'All streams · Boards', readiness:0 },
  icse10:     { name:'ICSE Class 10', sub:'Full syllabus', readiness:0 },
  state_board:{ name:'State Board', sub:'Your state syllabus', readiness:0 },
  upsc:       { name:'UPSC CSE', sub:'Prelims + Mains + Interview', readiness:0 },
  ssc_cgl:    { name:'SSC CGL', sub:'Tier I + Tier II + Tier III', readiness:0 },
  banking:    { name:'Banking (IBPS/SBI)', sub:'PO + Clerk + SO tracks', readiness:0 },
  railways:   { name:'Railways (RRB)', sub:'NTPC + Group D + ALP', readiness:0 },
  defence:    { name:'Defence (NDA/CDS)', sub:'NDA + CDS + AFCAT', readiness:0 },
  teaching:   { name:'Teaching (CTET)', sub:'Paper I + Paper II', readiness:0 },
  jee:        { name:'JEE Main / Advanced', sub:'Maths + Physics + Chemistry', readiness:0 },
  neet:       { name:'NEET UG', sub:'Biology + Physics + Chemistry', readiness:0 },
  cuet:       { name:'CUET', sub:'Domain + Language + General', readiness:0 },
  gate:       { name:'GATE', sub:'Your engineering branch', readiness:0 },
  cat:        { name:'CAT', sub:'QA + DILR + VARC', readiness:0 },
  clat:       { name:'CLAT', sub:'English + Legal + Reasoning', readiness:0 },
  placement:  { name:'Campus Placements', sub:'Aptitude + Coding + HR', readiness:0 },
  ugc_net:    { name:'UGC NET / CSIR NET', sub:'Paper I + Paper II', readiness:0 },
  phd:        { name:'PhD / Research', sub:'Research methodology + thesis', readiness:0 },
};
function setExamGoal(key) {
  const meta = _goalMeta[key];
  if (!meta) return;
  localStorage.setItem('padhai_goal', key);
  $('sh-exam-name').textContent = meta.name;
  $('sh-exam-sub').textContent = meta.sub;
  const pct = meta.readiness;
  $('sh-bar-fill').style.width = pct + '%';
  const tag = $('sh-readiness-tag');
  tag.textContent = 'Readiness ' + pct + '%';
  tag.className = 'sh-tag ' + (pct >= 70 ? 'green' : pct >= 40 ? 'amber' : 'red');
  $('sh-today-goal').textContent = 'You\'re preparing for ' + meta.name + '. Set up your first lesson to start tracking readiness.';
  $('sh-today-sub').textContent = meta.sub + ' — use the tools below to build your plan.';
}
const _goalPicker = $('sh-goal-picker');
if (_goalPicker) {
  const savedGoal = localStorage.getItem('padhai_goal');
  if (savedGoal) { _goalPicker.value = savedGoal; setExamGoal(savedGoal); }
  _goalPicker.addEventListener('change', () => setExamGoal(_goalPicker.value));
}

// ---- Home module initializer ----
function initHome() {
  setRole(_activeRole);
  // Load lesson count for badges
  fetch('/jobs?limit=1', { headers: authHeaders() })
    .then(r => r.ok ? r.json() : null)
    .then(j => {
      if (j && j.jobs) {
        // use total if API exposes it; otherwise show what we got
        const ct = j.total != null ? j.total : (j.jobs.length > 0 ? j.jobs.length + '+' : '0');
        const el = $('sh-lessons-count');
        if (el) el.textContent = ct;
      }
    }).catch(() => {});
  // Streak from localStorage (incremented when user visits)
  const today = new Date().toDateString();
  const last = localStorage.getItem('padhai_last_visit');
  let streak = parseInt(localStorage.getItem('padhai_streak') || '0', 10);
  if (last !== today) {
    const yesterday = new Date(Date.now() - 86400000).toDateString();
    streak = last === yesterday ? streak + 1 : 1;
    localStorage.setItem('padhai_streak', streak);
    localStorage.setItem('padhai_last_visit', today);
  }
  const se = $('sh-streak');
  if (se) se.textContent = streak + (streak === 1 ? ' day' : ' days');
  const te = $('sh-time-today');
  if (te) te.textContent = '—';
}
initHome();

// ---- auth ----
function renderAuthCorner() {
  // The bell (#notif-bell) must survive this re-render — only swap
  // the trailing identity block, not the whole #auth-corner.
  let identity = document.getElementById('auth-identity');
  if (!identity) {
    identity = document.createElement('span');
    identity.id = 'auth-identity';
    identity.style.display = 'inline-flex';
    identity.style.gap = '8px';
    identity.style.alignItems = 'center';
    $('auth-corner').appendChild(identity);
  }
  if (token && email) {
    identity.innerHTML = `<span class="who">${escapeHtml(email)}</span>
                          <button class="btn-ghost" id="logout">Log out</button>`;
    $('logout').onclick = () => {
      token = email = null;
      localStorage.removeItem('pathshala_token');
      localStorage.removeItem('pathshala_email');
      renderAuthCorner();
      // hide bell on sign-out
      document.getElementById('notif-bell').style.display = 'none';
    };
    // restart polling now that we have a token
    if (typeof notifStartPolling === 'function') notifStartPolling();
  } else {
    identity.innerHTML = `<button class="btn-ghost" id="signin-btn">Sign in</button>`;
    $('signin-btn').onclick = () => $('auth-modal').classList.add('open');
    document.getElementById('notif-bell').style.display = 'none';
  }
}
renderAuthCorner();

// AI capability check — runs once on load, gates AI-dependent module UIs.
let _aiStatus = null;
async function checkAiStatus() {
  try {
    const r = await fetch('/api/ai-status');
    _aiStatus = await r.json();
  } catch(_) {
    _aiStatus = { anthropic_configured: false, features: {} };
  }
  if (!_aiStatus.anthropic_configured) {
    // Show a dismissable notice so admins know what to configure
    const banner = document.createElement('div');
    banner.id = 'ai-setup-banner';
    banner.style.cssText = (
      'position:fixed;bottom:16px;right:16px;max-width:340px;'
      + 'background:#1e293b;color:#e2e8f0;padding:14px 16px;border-radius:10px;'
      + 'font-size:13px;line-height:1.5;z-index:9999;box-shadow:0 4px 24px rgba(0,0,0,.35);'
    );
    banner.innerHTML = (
      '<div style="font-weight:700;margin-bottom:6px;">⚙ Full AI not configured</div>'
      + '<div style="color:#94a3b8;">Set <code style="background:#334155;padding:1px 5px;border-radius:3px;">ANTHROPIC_API_KEY</code> '
      + 'to enable Voice Tutor, Live Lecture, Math Vision, and AI question synthesis. '
      + 'Essay Grader and Mock Interview run in basic mode without it.</div>'
      + '<button onclick="this.parentElement.remove()" style="margin-top:10px;background:#334155;'
      + 'border:0;color:#e2e8f0;padding:4px 12px;border-radius:6px;cursor:pointer;font-size:12px;">Dismiss</button>'
    );
    document.body.appendChild(banner);
  }
}
checkAiStatus();

// Per-module AI gate helper — call on moduleShow to inject a status note
function showAiNote(statusElId, featureKey) {
  if (!_aiStatus) return;   // not loaded yet; will show on first interaction
  const el = document.getElementById(statusElId);
  if (!el) return;
  const degraded = (_aiStatus.degraded_without_ai || []).includes(featureKey);
  if (!_aiStatus.features[featureKey]) {
    el.textContent = 'AI not configured — set ANTHROPIC_API_KEY on the server to enable this feature.';
    el.className = 'status error';
  } else if (degraded) {
    el.textContent = 'Running in basic mode — set ANTHROPIC_API_KEY on the server for full AI-powered results.';
    el.className = 'status';
  } else {
    if (el.textContent.includes('AI not configured') || el.textContent.includes('basic mode')) el.textContent = '';
  }
}

// URL hash navigation — allows external pages (design overview, email
// links, etc.) to deep-link into any module: /?m=quizmaker or #quizmaker
(function() {
  const params = new URLSearchParams(window.location.search);
  const fromQ = params.get('m') || params.get('module');
  const fromH = window.location.hash.replace('#','');
  const target = fromQ || fromH;
  const validMods = ['home','studio','explainer','chat','library','quizmaker',
    'flashcards','match','recap','notes','live','voice','path','curriculum',
    'teacher','parent','school','create'];
  if (target && validMods.includes(target)) showModule(target);
})();
// doesn't support it. The SW caches the SPA shell + lets users "Save
// offline" video lessons that survive disconnections.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(err => {
      console.warn('SW registration failed (non-fatal):', err);
    });
  });
}

// v1.0 E9: Apply org-specific branding when served on a custom
// subdomain. Single fetch on boot; result drives the CSS variables
// + page title + favicon. Platform defaults serve as fallback.
(async function applyBranding() {
  try {
    const r = await fetch('/api/branding/resolve');
    if (!r.ok) return;
    const b = await r.json();
    const root = document.documentElement.style;
    root.setProperty('--brand', b.brand_color);
    root.setProperty('--purple', b.brand_accent);
    const brandH1 = document.querySelector('.brand h1');
    if (brandH1 && b.brand_name !== 'AI Pathshala') {
      brandH1.firstChild.nodeValue = b.brand_name + ' ';
    }
    document.title = b.brand_name + ' — your AI teacher';
  } catch (e) { /* offline / first-boot — fine, defaults stay */ }
})();

$('close-modal').onclick = () => $('auth-modal').classList.remove('open');
document.querySelectorAll('.tab').forEach(t => {
  t.onclick = () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    mode = t.dataset.mode;
    $('auth-status').textContent = '';
  };
});
$('auth-form').addEventListener('submit', async e => {
  e.preventDefault();
  const s = $('auth-status'); s.textContent = 'Working…'; s.className = 'status';
  const fd = new FormData($('auth-form'));
  try {
    const r = await fetch(`/auth/${mode}`, { method:'POST', body: fd });
    if (r.status === 503) {
      s.textContent = 'Auth not configured on this deploy. Video flow works anyway as anonymous.';
      s.className = 'status error';
      return;
    }
    if (!r.ok) {
      const j = await r.json().catch(()=>({}));
      s.textContent = `${r.status}: ${j.detail || 'failed'}`;
      s.className = 'status error';
      return;
    }
    const j = await r.json();
    // DPDP §9: under-13 accounts come back without a token until the
    // parent verifies. Surface that explicitly so the kid sees what
    // happens next.
    if (j.consent_required) {
      s.innerHTML = `
        Your account is created but locked until your parent verifies it.
        We sent a one-time link to <strong>${escapeHtml(j.parent_email || '')}</strong>.
        Ask them to check email (or spam folder) and click the link.`;
      s.className = 'status';
      return;
    }
    token = j.token; email = j.email;
    localStorage.setItem('pathshala_token', token);
    localStorage.setItem('pathshala_email', email);
    renderAuthCorner();
    $('auth-modal').classList.remove('open');
    // Refresh notifications now that we have a token
    if (typeof notifRefresh === 'function') notifRefresh();
  } catch (err) {
    s.textContent = 'Error: ' + err.message;
    s.className = 'status error';
  }
});

// ---- DPDP signup DOB → under-18 toggle (India DPDP Act 2023 §2(f)) ----
(function dpdpSignupHook() {
  const dob = $('auth-dob');
  const block = $('signup-dpdp');
  if (!dob || !block) return;
  // Set max attribute to today so the picker can't pick a future date
  dob.max = new Date().toISOString().slice(0, 10);
  dob.addEventListener('change', () => {
    const v = dob.value;
    if (!v) { block.style.display = 'none'; return; }
    // Compute age clientside for instant UX; server re-validates.
    const today = new Date();
    const birth = new Date(v);
    let age = today.getFullYear() - birth.getFullYear();
    const m = today.getMonth() - birth.getMonth();
    if (m < 0 || (m === 0 && today.getDate() < birth.getDate())) age--;
    block.style.display = (age >= 0 && age < 18) ? 'block' : 'none';
    $('auth-parent-email').required = (age >= 0 && age < 18);
  });
  // Only show DOB on signup tab, not login
  document.querySelectorAll('.tab').forEach(t => {
    t.addEventListener('click', () => {
      document.querySelectorAll('.signup-only').forEach(el => {
        el.style.display = (t.dataset.mode === 'signup') ? 'block' : 'none';
      });
      if (t.dataset.mode === 'login') block.style.display = 'none';
    });
  });
})();

// ---- E7: SSO providers discovery ----
(async function ssoBootstrap() {
  try {
    const r = await fetch('/auth/sso/providers');
    if (!r.ok) return;
    const j = await r.json();
    if (!j.providers || !j.providers.length) return;
    $('sso-block').style.display = 'flex';
    if (j.providers.includes('google'))    $('sso-google').style.display = 'flex';
    if (j.providers.includes('microsoft')) $('sso-microsoft').style.display = 'flex';
  } catch (e) { /* SSO is optional — silent failure is fine */ }
})();

// ---- E2: Notifications bell + drawer ----
let notifPollTimer = null;

async function notifRefresh() {
  if (!token) {
    $('notif-bell').style.display = 'none';
    return;
  }
  try {
    const r = await fetch('/api/notifications/me', { headers: authHeaders() });
    if (!r.ok) return;
    const j = await r.json();
    const badge = $('notif-badge');
    const bell = $('notif-bell');
    // Only show the bell when the user belongs to at least one org —
    // otherwise notifications are dormant.
    if (j.notifications.length === 0 && j.unread_count === 0) {
      bell.style.display = 'none';
      return;
    }
    bell.style.display = 'inline-flex';
    if (j.unread_count > 0) {
      badge.textContent = String(j.unread_count > 99 ? '99+' : j.unread_count);
      badge.style.display = 'inline-block';
    } else {
      badge.style.display = 'none';
    }
    notifRenderList(j.notifications);
  } catch (e) { /* polling is best-effort */ }
}

function notifRenderList(items) {
  const wrap = $('notif-list');
  if (!items.length) {
    wrap.innerHTML = `<div class="notif-empty">No notifications yet.</div>`;
    return;
  }
  wrap.innerHTML = items.map(n => {
    const ago = notifAgo(n.send_at);
    return `
      <div class="notif-row ${n.read ? '' : 'unread'}" data-nid="${escapeHtml(n.id)}">
        <div class="notif-title">${escapeHtml(n.title)}</div>
        ${n.body ? `<div class="notif-body">${escapeHtml(n.body)}</div>` : ''}
        <div class="notif-meta">
          <span class="notif-kind-pill ${n.kind}">${escapeHtml(n.kind.replace('_',' '))}</span>
          <span>${ago}</span>
        </div>
      </div>`;
  }).join('');
  wrap.querySelectorAll('.notif-row').forEach(el => {
    el.addEventListener('click', () => notifMarkRead(el.dataset.nid, el));
  });
}

function notifAgo(epoch) {
  const sec = Math.max(0, Math.round((Date.now()/1000) - epoch));
  if (sec < 60) return 'just now';
  if (sec < 3600) return Math.floor(sec/60) + 'm ago';
  if (sec < 86400) return Math.floor(sec/3600) + 'h ago';
  return Math.floor(sec/86400) + 'd ago';
}

async function notifMarkRead(nid, rowEl) {
  if (!nid) return;
  try {
    await fetch(`/api/notifications/${nid}/read`,
                { method:'POST', headers: authHeaders() });
    rowEl?.classList.remove('unread');
    notifRefresh();  // update badge count
  } catch (e) {}
}

$('notif-bell').addEventListener('click', () => {
  $('notif-drawer').style.display = 'flex';
  notifRefresh();
});
$('notif-close').addEventListener('click', () => {
  $('notif-drawer').style.display = 'none';
});
$('notif-mark-all').addEventListener('click', async () => {
  try {
    await fetch('/api/notifications/read-all',
                { method:'POST', headers: authHeaders() });
    notifRefresh();
  } catch (e) {}
});

// Start polling once the user is signed in. 60s is fine; bell badge
// is the priority UI surface, not real-time push.
function notifStartPolling() {
  if (notifPollTimer) return;
  notifRefresh();
  notifPollTimer = setInterval(notifRefresh, 60_000);
}

if (token) notifStartPolling();

function authHeaders() {
  return token ? { 'Authorization': `Bearer ${token}` } : {};
}

// Gate for features that require sign-in: shows the auth modal and returns false
// when the user is anonymous; returns true when authenticated.
function requireAuthOrPrompt() {
  if (token) return true;
  $('auth-modal').classList.add('open');
  return false;
}

// ---- create lesson ----
const f = $('f'), go = $('go'), st = $('status'), v = $('v');
function setStatus(msg, kind='') { st.textContent = msg; st.className = 'status ' + kind; }

async function pollJob(jobId) {
  let tries = 0;
  while (tries < 600) {
    await new Promise(r => setTimeout(r, 2000));
    tries++;
    const r = await fetch(`/jobs/${jobId}`);
    if (!r.ok) { setStatus(`Status ${r.status}`, 'error'); return null; }
    const j = await r.json();
    if (j.status === 'succeeded') return j;
    if (j.status === 'failed') { setStatus(`Render failed: ${j.error||'unknown'}`, 'error'); return null; }
    setStatus(`Generating lesson… ${j.status} (${tries*2}s)`);
  }
  setStatus('Timed out after 10 minutes.', 'error'); return null;
}

f.addEventListener('submit', async e => {
  e.preventDefault();
  v.hidden = true; v.src = '';
  $('chat-inline').style.display = 'none';
  $('messages-inline').innerHTML = '';
  currentLessonId = null;
  go.disabled = true; setStatus('Uploading…');
  try {
    const r = await fetch('/lessons', { method:'POST', body:new FormData(f), headers:authHeaders() });
    if (r.status === 200) {
      v.src = URL.createObjectURL(await r.blob()); v.hidden = false;
      setStatus('Served from cache ✓', 'ok'); return;
    }
    if (r.status !== 202) {
      const t = await r.text();
      setStatus(`Upload failed (${r.status}): ${t.slice(0,200)}`, 'error'); return;
    }
    const j = await r.json();
    setStatus(`Queued as ${j.job_id.slice(0,8)}…`);
    const done = await pollJob(j.job_id);
    if (!done) return;
    v.src = done.direct_url || `/jobs/${j.job_id}/video`;
    v.hidden = false;
    setStatus(done.cache_hit ? 'Served from cache ✓' : 'Ready ✓', 'ok');
    if (done.lesson_id) {
      currentLessonId = done.lesson_id;
      $('chat-inline').style.display = 'block';
    }
  } catch (err) { setStatus('Error: ' + err.message, 'error'); }
  finally { go.disabled = false; }
});

// ---- chat (inline + page) with source citations ----
function addMsg(container, text, who, sources, confidence) {
  const div = document.createElement('div');
  div.className = `msg ${who}`;
  // Sanitise output — textContent for user messages, structured for AI
  if (who === 'ai' && (sources && sources.length > 0 || confidence)) {
    div.innerHTML = escapeHtml(text);
    if (sources && sources.length > 0) {
      const sp = document.createElement('div');
      sp.className = 'msg-sources';
      sources.forEach(s => {
        const pill = document.createElement('span');
        pill.className = 'msg-source-pill';
        pill.textContent = s;
        sp.appendChild(pill);
      });
      div.appendChild(sp);
    }
    if (confidence != null) {
      const cf = document.createElement('div');
      cf.className = 'msg-confidence';
      cf.textContent = 'Confidence: ' + (typeof confidence === 'number' ? confidence.toFixed(2) : confidence);
      div.appendChild(cf);
    }
  } else {
    div.textContent = text;
  }
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}
async function askChat(lessonId, q, container, btn) {
  if (!q || !lessonId) return;
  addMsg(container, q, 'you');
  btn.disabled = true;
  try {
    const fd = new FormData(); fd.set('question', q);
    const r = await fetch(`/chat/${lessonId}`, { method:'POST', body:fd, headers:authHeaders() });
    if (!r.ok) {
      const t = await r.text();
      addMsg(container, `Error ${r.status}: ${t.slice(0,200)}`, 'ai');
    } else {
      const j = await r.json();
      // Render answer with citations if the backend provides them
      addMsg(container, j.answer || '(empty response)', 'ai',
             j.sources || j.citations || null,
             j.confidence != null ? j.confidence : null);
    }
  } catch (err) { addMsg(container, 'Error: ' + err.message, 'ai'); }
  finally { btn.disabled = false; }
}
$('send-inline').addEventListener('click', () => {
  const q = $('q-inline').value.trim(); $('q-inline').value = '';
  askChat(currentLessonId, q, $('messages-inline'), $('send-inline'));
});
$('q-inline').addEventListener('keypress', e => {
  if (e.key === 'Enter') { e.preventDefault(); $('send-inline').click(); }
});
$('send-page').addEventListener('click', () => {
  const q = $('q-page').value.trim(); $('q-page').value = '';
  const lid = $('chat-lesson-id').value.trim();
  if (!lid) { addMsg($('messages-page'), 'Enter a lesson_id first.', 'ai'); return; }
  askChat(lid, q, $('messages-page'), $('send-page'));
});
$('q-page').addEventListener('keypress', e => {
  if (e.key === 'Enter') { e.preventDefault(); $('send-page').click(); }
});

// ---- library ----
async function loadLibrary() {
  const box = $('lib-list'); box.innerHTML = '<div class="status">Loading…</div>';
  try {
    const r = await fetch('/jobs?limit=20', { headers:authHeaders() });
    if (!r.ok) { box.innerHTML = `<div class="status error">Error ${r.status}</div>`; return; }
    const j = await r.json();
    if (!j.jobs.length) {
      box.innerHTML = `<div class="status">No lessons yet. ${j.authenticated ? '' : 'Sign in to save lessons across devices.'} <button class="btn-ghost" onclick="showModule('create')">Create your first lesson</button></div>`;
      return;
    }
    box.innerHTML = '';
    for (const job of j.jobs) {
      const item = document.createElement('div');
      item.className = 'lib-item';
      const icon = job.status === 'succeeded' ? '🎬' : (job.status === 'failed' ? '⚠️' : '⏳');
      const dt = new Date(job.created_at * 1000).toLocaleString();
      item.innerHTML = `
        <div class="ico">${icon}</div>
        <div>
          <div class="title">${(job.language||'').toUpperCase()} · ${job.level||''}</div>
          <div class="meta">${dt} · ${job.id.slice(0,8)} · <span class="chip ${job.status==='succeeded'?'success':(job.status==='failed'?'error':'')}">${job.status}</span> ${job.cache_hit?'<span class="chip">cache hit</span>':''}</div>
        </div>
        <div>
          ${job.status==='succeeded' && job.video_url ? `<button class="btn-ghost" data-vid="${job.video_url}">Watch</button>` : ''}
          ${job.lesson_id ? `<button class="btn-ghost" data-chat="${job.lesson_id}">Chat</button>` : ''}
        </div>`;
      box.appendChild(item);
    }
    box.addEventListener('click', e => {
      if (e.target.dataset.vid) {
        $('v').src = e.target.dataset.vid;
        $('v').hidden = false;
        showModule('create');
        window.scrollTo({top:0,behavior:'smooth'});
      } else if (e.target.dataset.chat) {
        $('chat-lesson-id').value = e.target.dataset.chat;
        showModule('chat');
      }
    });
  } catch (err) { box.innerHTML = `<div class="status error">Error: ${err.message}</div>`; }
}

// ---- parent dashboard ----
const LANG_NAMES = { en:'English', hi:'Hindi', ta:'Tamil', te:'Telugu',
                     bn:'Bengali', mr:'Marathi', gu:'Gujarati',
                     kn:'Kannada', ml:'Malayalam', pa:'Punjabi' };
const LEVEL_NAMES = { kg:'Kindergarten', primary:'Primary', middle:'Middle',
                      secondary:'Secondary', neet_jee:'NEET / JEE / UPSC',
                      eli5:"ELI5" };

// v0.14 E8: parent can switch between "my activity" and a linked
// child's view via the children bar.
let pdActiveChild = null;     // user_id of currently-viewed child, or null = me
let pdLinks = [];             // list of verified + pending links

async function pdLoad() {
  await pdLoadChildren();
  await pdLoadStats();
}

async function pdLoadChildren() {
  // Children bar only matters when the user is signed in.
  if (!token) {
    $('pd-children-bar').style.display = 'none';
    return;
  }
  try {
    const r = await fetch('/api/parents/children',
                          { headers: authHeaders() });
    if (!r.ok) {
      $('pd-children-bar').style.display = 'none';
      return;
    }
    const j = await r.json();
    pdLinks = j.links || [];
    pdRenderChildrenBar();
  } catch (e) {
    $('pd-children-bar').style.display = 'none';
  }
}

function pdRenderChildrenBar() {
  // Show the bar whenever the user is signed in (so they can
  // discover the "+ Link a child" affordance), not just when they
  // already have children.
  $('pd-children-bar').style.display = 'flex';
  $('pd-child-list').innerHTML = pdLinks.map(link => {
    const isActive = pdActiveChild === link.child_user_id;
    const pending = link.status === 'pending';
    const label = pending
      ? `⏳ ${escapeHtml(link.child_email || '—')}`
      : `👧 ${escapeHtml(link.child_email || '—')}`;
    return `<button class="pd-child-chip ${isActive ? 'active' : ''} ${pending ? 'pending' : ''}"
                    data-child="${escapeHtml(link.child_user_id)}"
                    data-status="${link.status}"
                    title="${pending ? 'Awaiting verification' : 'Linked child'}">
              ${label}
            </button>`;
  }).join('');
  document.querySelectorAll('#pd-children-bar .pd-child-chip').forEach(chip => {
    chip.addEventListener('click', async () => {
      if (chip.id === 'pd-link-child-btn') return;
      const status = chip.dataset.status;
      if (status === 'pending') {
        alert('This link is awaiting verification by the child. '
             + 'Ask them to sign in and click the verification email.');
        return;
      }
      const target = chip.dataset.child;
      pdActiveChild = (target === 'me') ? null : target;
      document.querySelectorAll('#pd-children-bar .pd-child-chip')
              .forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      await pdLoadStats();
    });
  });
}

async function pdLoadStats() {
  const url = pdActiveChild
    ? `/api/parents/children/${pdActiveChild}/stats?days=7`
    : '/me/stats?days=7';
  try {
    const r = await fetch(url, { headers:authHeaders() });
    if (!r.ok) {
      $('pd-tiles').innerHTML =
        `<div class="status error">Failed (${r.status})</div>`;
      return;
    }
    const j = await r.json();
    pdRender(j);
  } catch (err) {
    $('pd-tiles').innerHTML = `<div class="status error">Error: ${err.message}</div>`;
  }
}

// Link-a-child modal
$('pd-link-child-btn')?.addEventListener('click',
  () => $('pd-link-modal').style.display = 'flex');
document.querySelectorAll('[data-close-pd]').forEach(b =>
  b.addEventListener('click',
    () => $('pd-link-modal').style.display = 'none'));
$('pd-link-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const status = $('pd-link-status');
  status.textContent = 'Sending…'; status.className = 'status';
  try {
    const fd = new FormData(e.target);
    const r = await fetch('/api/parents/link',
                          { method:'POST', headers: authHeaders(), body: fd });
    if (!r.ok && r.status !== 201) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || ('HTTP ' + r.status));
    }
    const j = await r.json();
    status.innerHTML = `Invite sent! Your child will receive a verification email and in-app notification.`;
    status.className = 'status ok';
    e.target.reset();
    await pdLoadChildren();
  } catch (ex) {
    status.textContent = 'Error: ' + ex.message;
    status.className = 'status error';
  }
});

function pdRender(d) {
  const s = d.summary;
  // Tiles
  $('pd-tiles').innerHTML = `
    <div class="pd-tile tile-week">
      <div class="lbl">This Week</div>
      <div class="val">${s.lessons_in_window}</div>
      <div class="sub">lessons watched</div>
    </div>
    <div class="pd-tile tile-streak">
      <div class="lbl">🔥 Streak</div>
      <div class="val">${s.streak_days}</div>
      <div class="sub">consecutive days</div>
    </div>
    <div class="pd-tile tile-total">
      <div class="lbl">Total Lessons</div>
      <div class="val">${s.lessons_total}</div>
      <div class="sub">${s.cache_hits} cache hits</div>
    </div>
    <div class="pd-tile tile-lang">
      <div class="lbl">🌐 Languages</div>
      <div class="val">${s.languages_count}</div>
      <div class="sub">used so far</div>
    </div>`;

  // Activity chart — bars sized relative to max
  const maxLessons = Math.max(1, ...d.activity.map(a => a.lessons));
  $('pd-chart').innerHTML = d.activity.map(a => {
    const pct = (a.lessons / maxLessons) * 100;
    const date = new Date(a.date + 'T00:00:00');
    const day = date.toLocaleDateString('en', { weekday: 'short' }).slice(0,3);
    return `<div class="pd-bar ${a.lessons === 0 ? 'zero' : ''}"
                 style="height:${Math.max(6, pct)}%;"
                 title="${a.date}: ${a.lessons} lesson${a.lessons === 1 ? '' : 's'}">
              <span class="val-label">${a.lessons || ''}</span>
              <span class="day-label">${day}</span>
            </div>`;
  }).join('');

  // Languages bars
  const langMax = Math.max(1, ...d.top_languages.map(l => l.count));
  $('pd-langs').innerHTML = d.top_languages.length
    ? d.top_languages.map(l => `
        <div class="pd-stripe">
          <span class="label">${LANG_NAMES[l.code] || l.code}</span>
          <span class="bar-wrap"><span class="bar-fill" style="width:${(l.count/langMax)*100}%;"></span></span>
          <span class="count">${l.count}</span>
        </div>`).join('')
    : '<div class="status">No lessons generated yet — try Create Lesson.</div>';

  // Levels bars
  const levelMax = Math.max(1, ...d.top_levels.map(l => l.count));
  $('pd-levels').innerHTML = d.top_levels.length
    ? d.top_levels.map(l => `
        <div class="pd-stripe">
          <span class="label">${LEVEL_NAMES[l.level] || l.level}</span>
          <span class="bar-wrap"><span class="bar-fill" style="width:${(l.count/levelMax)*100}%;"></span></span>
          <span class="count">${l.count}</span>
        </div>`).join('')
    : '<div class="status">—</div>';

  // Recent lessons
  $('pd-recent').innerHTML = d.recent_lessons.length
    ? d.recent_lessons.map(r => {
        const when = new Date(r.created_at * 1000).toLocaleString();
        return `<div class="pd-recent-item">
                  <span style="font-size:18px;">🎬</span>
                  <div>
                    <div style="font-weight:600;">${(LANG_NAMES[r.language]||r.language||'').toUpperCase()} · ${LEVEL_NAMES[r.level]||r.level||''}</div>
                    <div class="when">${when}</div>
                  </div>
                  ${r.video_url ? `<button class="btn-ghost" onclick="document.getElementById('v').src='${r.video_url}'; document.getElementById('v').hidden=false; showModule('create'); window.scrollTo({top:0,behavior:'smooth'});">Watch</button>` : ''}
                </div>`;
      }).join('')
    : '<div class="status">No recent lessons. <button class="btn-ghost" onclick="showModule(\'create\')">Create one</button></div>';
}

// Lazy-load on first click
document.querySelector('.nav-item[data-module="parent"]')
  .addEventListener('click', pdLoad);

// ---- learning path ----
function lpRenderPlan(plan) {
  const out = $('lp-output');
  const totalMins = plan.weeks.reduce((acc, w) =>
    acc + w.daily_tasks.reduce((a, t) => a + (t.estimated_minutes||0), 0), 0);
  let html = `
    <div class="lp-summary">
      <h3>${plan.title}</h3>
      <p>${plan.summary}</p>
      <p style="margin-top:8px;"><strong>${plan.total_weeks} weeks</strong> · ${plan.weeks.reduce((a,w)=>a+w.daily_tasks.length,0)} tasks · ~${Math.round(totalMins/60)}h total</p>
    </div>`;
  for (const w of plan.weeks) {
    const wkMins = w.daily_tasks.reduce((a,t) => a + (t.estimated_minutes||0), 0);
    html += `
      <div class="lp-week">
        <div class="lp-week-head">
          <div style="display:flex; align-items:center; flex:1;">
            <span class="wknum">${w.week_number}</span>
            <span class="lp-theme">${w.theme}</span>
          </div>
          <span class="lp-time">${wkMins} min · ${w.daily_tasks.length} tasks</span>
        </div>
        <div class="lp-tasks">`;
    for (const t of w.daily_tasks) {
      const ref = t.chapter_ref ? `<span class="sub-task"> · ${t.chapter_ref}</span>` : '';
      html += `
        <div class="lp-task">
          <span class="day">${t.day}</span>
          <div class="topic">${t.topic}${ref}</div>
          <span class="type-badge type-${t.type}">${t.type}</span>
          <span class="mins">${t.estimated_minutes} min</span>
        </div>`;
    }
    html += '</div></div>';
  }
  out.innerHTML = html;
}

$('lpf').addEventListener('submit', async e => {
  e.preventDefault();
  const subs = Array.from(document.querySelectorAll('input[name=subj]:checked')).map(x => x.value);
  if (!subs.length) {
    const s = $('lp-status'); s.textContent = 'Pick at least one subject.'; s.className = 'status error'; return;
  }
  const s = $('lp-status'); s.textContent = 'Building your plan… (Opus 4.7, ~30s)'; s.className = 'status';
  $('lp-go').disabled = true;
  $('lp-output').innerHTML = '';
  try {
    const fd = new FormData();
    fd.set('student_class', $('lp-class').value);
    fd.set('subjects', subs.join(','));
    fd.set('weeks', $('lp-weeks').value);
    fd.set('daily_minutes', $('lp-daily').value);
    fd.set('focus_topics', $('lp-focus').value);
    const r = await fetch('/learning-path', { method:'POST', body:fd, headers:authHeaders() });
    if (!r.ok) {
      const t = await r.text();
      s.textContent = `Failed (${r.status}): ${t.slice(0,200)}`;
      s.className = 'status error';
      return;
    }
    const j = await r.json();
    lpRenderPlan(j.plan);
    s.textContent = j.cached ? 'Loaded from cache ✓' : `Plan ready ✓`;
    s.className = 'status ok';
  } catch (err) {
    s.textContent = 'Error: ' + err.message; s.className = 'status error';
  } finally {
    $('lp-go').disabled = false;
  }
});

// ---- curriculum mapper ----
let cmIndex = [];
async function cmLoadIndex() {
  try {
    const r = await fetch('/curriculum/index');
    const j = await r.json();
    cmIndex = j.entries;
    // Populate filters
    const bs = $('cm-board'), cs = $('cm-class'), ss = $('cm-subject');
    bs.innerHTML = '<option value="">All boards</option>' +
      j.boards.map(b => `<option value="${b}">${b}</option>`).join('');
    cs.innerHTML = '<option value="">All classes</option>' +
      j.classes.map(c => `<option value="${c}">Class ${c}</option>`).join('');
    ss.innerHTML = '<option value="">All subjects</option>' +
      j.subjects.map(s => `<option value="${s}">${s}</option>`).join('');
    cmRenderIndex();
  } catch (err) {
    $('cm-index').innerHTML = `<div class="status error">Couldn't load catalogue: ${err.message}</div>`;
  }
}
function cmRenderIndex() {
  const board = $('cm-board').value;
  const cls = $('cm-class').value;
  const subj = $('cm-subject').value;
  const filtered = cmIndex.filter(r =>
    (!board || r.board === board) &&
    (!cls || String(r['class']) === cls) &&
    (!subj || r.subject === subj)
  );
  const box = $('cm-index');
  if (!filtered.length) {
    box.innerHTML = '<div class="status">No matching chapters.</div>';
    return;
  }
  box.innerHTML = filtered.map(r => `
    <div class="cm-row">
      <span class="cm-chip">${r.board} · Cl ${r['class']} · ${r.subject}</span>
      <div class="cm-info">
        <div class="cm-title">Ch ${r.chapter_no}: ${r.chapter_title}</div>
        <div class="cm-summary">${r.summary}</div>
        <div class="cm-tags">${(r.topics||[]).map(t=>`<span class="cm-tag">${t}</span>`).join('')}</div>
      </div>
    </div>`).join('');
}
['cm-board','cm-class','cm-subject'].forEach(id => {
  $(id).addEventListener('change', cmRenderIndex);
});

async function cmMatch(lessonId) {
  const s = $('cm-status'); s.textContent = 'Matching… (Haiku 4.5, ~₹0.20)'; s.className = 'status';
  $('cm-results').innerHTML = '';
  try {
    const r = await fetch(`/lessons/${lessonId}/curriculum`, { method:'POST', headers:authHeaders() });
    if (!r.ok) {
      const t = await r.text();
      s.textContent = `Failed (${r.status}): ${t.slice(0,200)}`; s.className = 'status error';
      return;
    }
    const j = await r.json();
    s.textContent = j.cached ? 'Loaded from cache ✓' : `Matched ✓`;
    s.className = 'status ok';
    if (!j.matches.length) {
      $('cm-results').innerHTML = '<div class="status">No strong matches — the lesson may be from outside the indexed syllabus (Phase-1 catalogue: CBSE Class 6-10 Maths/Science only).</div>';
      return;
    }
    $('cm-results').innerHTML = j.matches.map((m, i) => `
      <div class="cm-match-card">
        <span class="cm-rank">${i+1}</span>
        <span class="cm-conf">${Math.round(m.confidence*100)}% match</span>
        <div class="cm-title">${m.chapter_title || m.id}</div>
        <div class="cm-meta">${m.board || ''} · Class ${m['class']||''} · ${m.subject||''} · Chapter ${m.chapter_no||''}</div>
        <div class="cm-reason">${m.reason}</div>
      </div>`).join('');
  } catch (err) {
    s.textContent = 'Error: ' + err.message; s.className = 'status error';
  }
}

$('cm-match').addEventListener('click', () => {
  const lid = $('cm-lesson-id').value.trim();
  if (!lid) { $('cm-status').textContent = 'Enter a lesson_id first.'; $('cm-status').className='status error'; return; }
  cmMatch(lid);
});
$('cm-use-latest').addEventListener('click', async () => {
  const r = await fetch('/jobs?limit=10', { headers: authHeaders() });
  const j = await r.json();
  const latest = j.jobs.find(x => x.lesson_id);
  if (latest) { $('cm-lesson-id').value = latest.lesson_id; }
  else { $('cm-status').textContent = 'No completed lessons yet.'; $('cm-status').className='status error'; }
});

// Lazy-load the catalogue the first time the user clicks the Curriculum
// Map nav item — don't pay for the network request on every page load.
document.querySelector('.nav-item[data-module="curriculum"]')
  .addEventListener('click', () => {
    if (cmIndex.length === 0) cmLoadIndex();
  });

// ---- flashcards ----
let fcDeck = [];      // [{front, back, hint, tags, srs:{interval, ease, due}}]
let fcIndex = 0;
let fcLessonId = null;
let fcFlipped = false;

function fcSetStatus(msg, kind='') {
  const s = $('fc-status');
  s.textContent = msg;
  s.className = 'status ' + kind;
}

// localStorage shape: { [lessonId]: { cards, srs: { [cardIndex]: {ease, interval, due} } } }
function fcLoadState(lessonId) {
  try { return JSON.parse(localStorage.getItem('pathshala_fc_' + lessonId) || 'null'); }
  catch { return null; }
}
function fcSaveState(lessonId, state) {
  try { localStorage.setItem('pathshala_fc_' + lessonId, JSON.stringify(state)); } catch {}
}

function fcDefaultSrs() { return { ease: 2.5, interval: 0, due: Date.now() }; }

function fcRender() {
  if (!fcDeck.length) {
    $('fc-deck-wrap').style.display = 'none';
    return;
  }
  $('fc-deck-wrap').style.display = 'block';
  const card = fcDeck[fcIndex];
  $('fc-front').textContent = card.front;
  $('fc-back').textContent = card.back;
  const hint = $('fc-hint');
  hint.textContent = card.hint ? '💡 ' + card.hint : '';
  hint.style.display = card.hint ? 'block' : 'none';
  for (const where of ['front', 'back']) {
    const tagBox = $('fc-tags-' + where);
    tagBox.innerHTML = '';
    for (const t of (card.tags || [])) {
      const el = document.createElement('span');
      el.className = 'fc-tag'; el.textContent = t;
      tagBox.appendChild(el);
    }
  }
  $('fc-pos').textContent = `${fcIndex + 1} / ${fcDeck.length}`;
  $('fc-card').classList.remove('flipped');
  fcFlipped = false;
  // hide SRS until flipped
  $('fc-srs').style.opacity = '0.4';
  $('fc-srs').style.pointerEvents = 'none';

  // Compute upcoming intervals for the SR button labels
  const srs = card.srs || fcDefaultSrs();
  const previewAgain = '<1d';
  const previewHard  = Math.max(1, Math.round(srs.interval * 1.2)) + 'd';
  const previewGood  = Math.max(1, Math.round((srs.interval || 1) * srs.ease)) + 'd';
  const previewEasy  = Math.max(1, Math.round((srs.interval || 1) * srs.ease * 1.3)) + 'd';
  $('when-again').textContent = previewAgain;
  $('when-hard').textContent  = previewHard;
  $('when-good').textContent  = previewGood;
  $('when-easy').textContent  = previewEasy;

  // Stats
  const mastered = fcDeck.filter(c => (c.srs?.interval || 0) >= 7).length;
  const learning = fcDeck.length - mastered;
  $('fc-stats').innerHTML =
    `<span>Lesson <strong>${fcLessonId.slice(0,8)}</strong></span>` +
    `<span><strong>${fcDeck.length}</strong> cards</span>` +
    `<span><strong>${mastered}</strong> mastered</span>` +
    `<span><strong>${learning}</strong> learning</span>`;
}

function fcFlip() {
  $('fc-card').classList.toggle('flipped');
  fcFlipped = !fcFlipped;
  if (fcFlipped) {
    $('fc-srs').style.opacity = '1';
    $('fc-srs').style.pointerEvents = 'auto';
  }
}

function fcMove(delta) {
  if (!fcDeck.length) return;
  fcIndex = (fcIndex + delta + fcDeck.length) % fcDeck.length;
  fcRender();
}

function fcRate(rating) {
  if (!fcFlipped) return;
  const card = fcDeck[fcIndex];
  const srs = card.srs || fcDefaultSrs();
  // Anki-style SM-2 lite
  if (rating === 'again') {
    srs.interval = 0;
    srs.ease = Math.max(1.3, srs.ease - 0.2);
    srs.due = Date.now() + 60_000; // bring back in ~1 min for in-session re-test
  } else if (rating === 'hard') {
    srs.interval = Math.max(1, Math.round(srs.interval * 1.2));
    srs.ease = Math.max(1.3, srs.ease - 0.15);
    srs.due = Date.now() + srs.interval * 86_400_000;
  } else if (rating === 'good') {
    srs.interval = Math.max(1, Math.round((srs.interval || 1) * srs.ease));
    srs.due = Date.now() + srs.interval * 86_400_000;
  } else if (rating === 'easy') {
    srs.interval = Math.max(1, Math.round((srs.interval || 1) * srs.ease * 1.3));
    srs.ease = Math.min(3.5, srs.ease + 0.15);
    srs.due = Date.now() + srs.interval * 86_400_000;
  }
  card.srs = srs;
  // persist
  const state = fcLoadState(fcLessonId) || { cards: null, srs: {} };
  state.srs = state.srs || {};
  state.srs[fcIndex] = srs;
  fcSaveState(fcLessonId, state);
  // auto-advance
  setTimeout(() => fcMove(1), 220);
}

async function fcGenerate(lessonId, count) {
  fcLessonId = lessonId;
  fcSetStatus('Generating flashcards… (Haiku 4.5, ~₹0.30)');
  $('fc-generate').disabled = true;
  try {
    const url = `/lessons/${lessonId}/flashcards?count=${count}`;
    const r = await fetch(url, { method: 'POST', headers: authHeaders() });
    if (!r.ok) {
      const t = await r.text();
      fcSetStatus(`Failed (${r.status}): ${t.slice(0,200)}`, 'error');
      return;
    }
    const j = await r.json();
    fcDeck = j.cards;
    // Hydrate per-card SRS state from localStorage
    const state = fcLoadState(lessonId) || {};
    for (let i = 0; i < fcDeck.length; i++) {
      fcDeck[i].srs = (state.srs && state.srs[i]) || fcDefaultSrs();
    }
    fcIndex = 0;
    fcRender();
    fcSetStatus(j.cached ? `Loaded ${j.count} cards from cache ✓` : `Generated ${j.count} cards ✓`, 'ok');
  } catch (err) { fcSetStatus('Error: ' + err.message, 'error'); }
  finally { $('fc-generate').disabled = false; }
}

$('fc-generate').addEventListener('click', () => {
  const lid = $('fc-lesson-id').value.trim();
  if (!lid) { fcSetStatus('Enter a lesson_id (or click "Use latest")', 'error'); return; }
  fcGenerate(lid, parseInt($('fc-count').value, 10));
});

$('fc-use-latest').addEventListener('click', async () => {
  fcSetStatus('Looking up latest lesson…');
  try {
    const r = await fetch('/jobs?limit=10', { headers: authHeaders() });
    const j = await r.json();
    const latest = j.jobs.find(x => x.lesson_id);
    if (!latest) {
      fcSetStatus('No completed lessons yet. Generate one in Create Lesson first.', 'error');
      return;
    }
    $('fc-lesson-id').value = latest.lesson_id;
    fcSetStatus(`Using lesson ${latest.lesson_id.slice(0,8)}…`);
  } catch (err) { fcSetStatus('Error: ' + err.message, 'error'); }
});

$('fc-card').addEventListener('click', e => {
  // Don't flip if a SRS button is the actual target
  if (e.target.closest('.srs-btn')) return;
  fcFlip();
});
$('fc-prev').addEventListener('click', () => fcMove(-1));
$('fc-next').addEventListener('click', () => fcMove(1));
document.querySelectorAll('.srs-btn').forEach(b => {
  b.addEventListener('click', () => fcRate(b.dataset.rating));
});
$('fc-shuffle').addEventListener('click', () => {
  for (let i = fcDeck.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [fcDeck[i], fcDeck[j]] = [fcDeck[j], fcDeck[i]];
  }
  fcIndex = 0; fcRender();
});
$('fc-restart').addEventListener('click', () => {
  for (const c of fcDeck) c.srs = fcDefaultSrs();
  if (fcLessonId) fcSaveState(fcLessonId, { srs: {} });
  fcIndex = 0; fcRender();
});
$('fc-export').addEventListener('click', () => {
  if (!fcDeck.length) return;
  // Anki-compatible TSV: front<TAB>back<TAB>tags
  const lines = fcDeck.map(c =>
    `${c.front.replace(/\t/g,' ')}\t${c.back.replace(/\t/g,' ')}\t${(c.tags||[]).join(' ')}`
  );
  const blob = new Blob([lines.join('\n')], { type: 'text/tab-separated-values' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `flashcards_${fcLessonId.slice(0,8)}.tsv`;
  a.click();
});

// keyboard shortcuts when flashcards module is active
document.addEventListener('keydown', e => {
  if (!$('mod-flashcards').classList.contains('active')) return;
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === ' ') { e.preventDefault(); fcFlip(); }
  else if (e.key === 'ArrowLeft') fcMove(-1);
  else if (e.key === 'ArrowRight') fcMove(1);
  else if (fcFlipped) {
    if (e.key === '1') fcRate('again');
    else if (e.key === '2') fcRate('hard');
    else if (e.key === '3') fcRate('good');
    else if (e.key === '4') fcRate('easy');
  }
});

// ---- teacher studio (mostly UI; submits same backend) ----
$('tf').addEventListener('submit', async e => {
  e.preventDefault();
  const ts = $('tstatus'); ts.textContent = ''; ts.className = 'status';
  const langs = Array.from(document.querySelectorAll('input[name=tl]:checked')).map(x => x.value);
  if (!langs.length) { ts.textContent = 'Pick at least one language.'; ts.className='status error'; return; }
  const file = $('tf-image').files[0];
  if (!file) { ts.textContent = 'Pick a chapter file.'; ts.className='status error'; return; }
  $('tgo').disabled = true;
  const jobs = [];
  for (const lang of langs) {
    ts.textContent = `Queuing ${lang.toUpperCase()}…`;
    const fd = new FormData();
    fd.set('image', file);
    fd.set('language', lang);
    fd.set('level', $('tf-level').value);
    fd.set('teacher', 'true');
    fd.set('include_quiz', 'true');
    try {
      const r = await fetch('/lessons', { method:'POST', body:fd, headers:authHeaders() });
      if (r.status === 202) {
        const j = await r.json(); jobs.push({ lang, job_id:j.job_id });
      } else {
        jobs.push({ lang, error: `${r.status}` });
      }
    } catch (err) { jobs.push({ lang, error: err.message }); }
  }
  $('tgo').disabled = false;
  ts.innerHTML = '<strong>Queued:</strong><br>' + jobs.map(j =>
    j.error
      ? `${j.lang.toUpperCase()}: error (${j.error})`
      : `${j.lang.toUpperCase()}: <a href="/jobs/${j.job_id}" target="_blank">${j.job_id.slice(0,8)}</a>`
  ).join('<br>');
  ts.className = 'status ok';
});

// ---- shared helper: latest lesson id ----
async function fetchLatestLessonId() {
  const r = await fetch('/jobs?limit=10', { headers: authHeaders() });
  const j = await r.json();
  const latest = (j.jobs || []).find(x => x.lesson_id);
  return latest ? latest.lesson_id : null;
}

function bindUseLatest(btnId, inputId, statusId) {
  $(btnId).addEventListener('click', async () => {
    const s = $(statusId);
    s.textContent = 'Looking up latest lesson…'; s.className = 'status';
    try {
      const id = await fetchLatestLessonId();
      if (!id) {
        s.textContent = 'No completed lessons yet. Generate one in Create Lesson first.';
        s.className = 'status error'; return;
      }
      $(inputId).value = id;
      s.textContent = `Using lesson ${id.slice(0,8)}…`; s.className = 'status ok';
    } catch (err) {
      s.textContent = 'Error: ' + err.message; s.className = 'status error';
    }
  });
}

// ===== QUIZ MAKER =====
let qzData = null, qzIdx = 0, qzScore = 0, qzAnswers = [];
const QZ_LETTERS = ['A','B','C','D','E','F'];

bindUseLatest('qz-use-latest', 'qz-lesson-id', 'qz-status');

$('qz-start').addEventListener('click', async () => {
  const lid = $('qz-lesson-id').value.trim();
  if (!lid) { $('qz-status').textContent = 'Enter a lesson_id (or click "Use latest")'; $('qz-status').className='status error'; return; }
  $('qz-status').textContent = 'Loading quiz…'; $('qz-status').className = 'status';
  $('qz-start').disabled = true;
  try {
    const r = await fetch(`/lessons/${lid}/quiz`, { method:'POST', headers:authHeaders() });
    if (!r.ok) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    qzData = await r.json();
    if (!qzData.questions || qzData.questions.length === 0) {
      $('qz-status').textContent = 'This lesson has no quiz questions.';
      $('qz-status').className = 'status error';
      return;
    }
    qzIdx = 0; qzScore = 0; qzAnswers = [];
    $('qz-picker').style.display = 'none';
    $('qz-result').style.display = 'none';
    $('qz-runner').style.display = 'block';
    $('qz-title').textContent = qzData.title || 'Quiz';
    qzRender();
  } catch (err) {
    $('qz-status').textContent = 'Error: ' + err.message;
    $('qz-status').className = 'status error';
  } finally {
    $('qz-start').disabled = false;
  }
});

function qzRender() {
  const q = qzData.questions[qzIdx];
  $('qz-counter').textContent = `${qzIdx + 1} / ${qzData.questions.length}`;
  $('qz-bar-fill').style.width = `${(qzIdx / qzData.questions.length) * 100}%`;
  $('qz-question').textContent = q.question || q.q || '';
  const opts = q.options || q.choices || [];
  $('qz-options').innerHTML = opts.map((opt, i) => `
    <button class="qz-opt" data-i="${i}">
      <span class="qz-opt-letter">${QZ_LETTERS[i] || (i+1)}</span>
      <span>${opt}</span>
    </button>`).join('');
  document.querySelectorAll('#qz-options .qz-opt').forEach(btn => {
    btn.addEventListener('click', () => qzPick(parseInt(btn.dataset.i, 10)));
  });
  $('qz-feedback').className = 'qz-feedback';
  $('qz-feedback').textContent = '';
  $('qz-next').disabled = true;
}

function qzPick(i) {
  const q = qzData.questions[qzIdx];
  // Find correct index: support {answer:'A'} or {correct: 0} or {correct_answer: 'B'}
  let correctIdx = -1;
  if (typeof q.answer === 'string') {
    correctIdx = QZ_LETTERS.indexOf(q.answer.toUpperCase());
  } else if (typeof q.correct_answer === 'string') {
    correctIdx = QZ_LETTERS.indexOf(q.correct_answer.toUpperCase());
  } else if (typeof q.correct === 'number') {
    correctIdx = q.correct;
  } else if (typeof q.answer === 'number') {
    correctIdx = q.answer;
  }
  const isCorrect = i === correctIdx;
  if (isCorrect) qzScore++;
  qzAnswers.push({ q: q.question || q.q, picked: i, correct: correctIdx, isCorrect });

  document.querySelectorAll('#qz-options .qz-opt').forEach((btn, idx) => {
    btn.classList.add('locked');
    if (idx === correctIdx) btn.classList.add('correct');
    if (idx === i && !isCorrect) btn.classList.add('wrong');
    if (idx === i) btn.classList.add('selected');
  });

  const fb = $('qz-feedback');
  fb.classList.add('show');
  if (isCorrect) {
    fb.classList.add('correct');
    fb.textContent = '✓ Correct! ' + (q.explanation || '');
  } else {
    fb.classList.add('wrong');
    const correctText = (q.options || q.choices || [])[correctIdx] || '';
    fb.textContent = `✗ The correct answer is ${QZ_LETTERS[correctIdx]}: ${correctText}. ${q.explanation || ''}`;
  }
  $('qz-next').disabled = false;
}

$('qz-next').addEventListener('click', () => {
  qzIdx++;
  if (qzIdx >= qzData.questions.length) qzFinish();
  else qzRender();
});

function qzFinish() {
  const total = qzData.questions.length;
  const pct = Math.round((qzScore / total) * 100);
  $('qz-bar-fill').style.width = '100%';
  $('qz-runner').style.display = 'none';
  $('qz-result').style.display = 'block';
  $('qz-score-big').textContent = `${qzScore} / ${total}  ·  ${pct}%`;
  let msg;
  if (pct === 100) msg = "Perfect! You've mastered this chapter.";
  else if (pct >= 75) msg = "Great work — a quick flashcard review and you're set.";
  else if (pct >= 50) msg = "Good start. Re-watch the lesson and try the flashcards.";
  else msg = "No worries — re-watch the lesson, listen to the audio recap, then retake.";
  $('qz-score-msg').textContent = msg;
  $('qz-review').innerHTML = '<h3 style="margin-top:18px;font-size:15px;color:var(--ink);">Review</h3>' +
    qzAnswers.map((a, i) => `
      <div class="qz-review-q ${a.isCorrect ? 'right' : 'wrong'}">
        <strong>Q${i+1}.</strong> ${a.q}
        <p>${a.isCorrect ? '✓ You answered ' + QZ_LETTERS[a.picked] : '✗ You picked ' + QZ_LETTERS[a.picked] + ', correct was ' + QZ_LETTERS[a.correct]}</p>
      </div>`).join('');
}

$('qz-retake').addEventListener('click', () => {
  qzIdx = 0; qzScore = 0; qzAnswers = [];
  $('qz-result').style.display = 'none';
  $('qz-runner').style.display = 'block';
  qzRender();
});

// ===== MATCH GAME =====
let mgPairs = [], mgCells = [], mgFlipped = [], mgMatched = 0, mgMoves = 0, mgStart = 0, mgTimer = null, mgLocked = false;

bindUseLatest('mg-use-latest', 'mg-lesson-id', 'mg-status');

$('mg-start').addEventListener('click', async () => {
  const lid = $('mg-lesson-id').value.trim();
  if (!lid) { $('mg-status').textContent = 'Enter a lesson_id'; $('mg-status').className='status error'; return; }
  $('mg-status').textContent = 'Loading cards…'; $('mg-status').className = 'status';
  $('mg-start').disabled = true;
  try {
    const r = await fetch(`/lessons/${lid}/flashcards?count=6`, { method:'POST', headers:authHeaders() });
    if (!r.ok) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const j = await r.json();
    let cards = (j.cards || []).slice(0, 6);
    if (cards.length < 3) {
      $('mg-status').textContent = 'Need at least 3 flashcards. Try a richer lesson.';
      $('mg-status').className = 'status error';
      return;
    }
    mgPairs = cards.slice(0, 6);
    mgStart_game();
  } catch (err) {
    $('mg-status').textContent = 'Error: ' + err.message;
    $('mg-status').className = 'status error';
  } finally { $('mg-start').disabled = false; }
});

function mgStart_game() {
  $('mg-picker').style.display = 'none';
  $('mg-result').style.display = 'none';
  $('mg-runner').style.display = 'block';
  // Build cells: each pair → two cards (question + answer) sharing pairId
  const cells = [];
  mgPairs.forEach((c, i) => {
    cells.push({ pairId: i, side: 'q', text: c.front });
    cells.push({ pairId: i, side: 'a', text: c.back });
  });
  // shuffle
  for (let i = cells.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [cells[i], cells[j]] = [cells[j], cells[i]];
  }
  mgCells = cells;
  mgFlipped = []; mgMatched = 0; mgMoves = 0; mgLocked = false;
  $('mg-pairs-total').textContent = mgPairs.length;
  $('mg-pairs').textContent = '0';
  $('mg-moves').textContent = '0';
  $('mg-time').textContent = '0:00';

  $('mg-grid').innerHTML = mgCells.map((c, i) => `
    <div class="mg-cell" data-i="${i}">
      <div class="mg-cell-inner">
        <div class="mg-face mg-face-back">?</div>
        <div class="mg-face mg-face-front is-${c.side}">${c.text}</div>
      </div>
    </div>`).join('');
  document.querySelectorAll('#mg-grid .mg-cell').forEach(el => {
    el.addEventListener('click', () => mgFlip(parseInt(el.dataset.i, 10)));
  });

  mgStart = Date.now();
  if (mgTimer) clearInterval(mgTimer);
  mgTimer = setInterval(() => {
    const sec = Math.floor((Date.now() - mgStart) / 1000);
    $('mg-time').textContent = `${Math.floor(sec/60)}:${String(sec%60).padStart(2,'0')}`;
  }, 1000);
}

function mgFlip(i) {
  if (mgLocked) return;
  const el = document.querySelector(`#mg-grid .mg-cell[data-i="${i}"]`);
  if (!el || el.classList.contains('flipped') || el.classList.contains('matched')) return;
  el.classList.add('flipped');
  mgFlipped.push(i);
  if (mgFlipped.length === 2) {
    mgMoves++;
    $('mg-moves').textContent = mgMoves;
    const [a, b] = mgFlipped;
    if (mgCells[a].pairId === mgCells[b].pairId && mgCells[a].side !== mgCells[b].side) {
      // match
      document.querySelector(`#mg-grid .mg-cell[data-i="${a}"]`).classList.add('matched');
      document.querySelector(`#mg-grid .mg-cell[data-i="${b}"]`).classList.add('matched');
      mgMatched++;
      $('mg-pairs').textContent = mgMatched;
      mgFlipped = [];
      if (mgMatched === mgPairs.length) mgFinish();
    } else {
      mgLocked = true;
      setTimeout(() => {
        document.querySelector(`#mg-grid .mg-cell[data-i="${a}"]`).classList.remove('flipped');
        document.querySelector(`#mg-grid .mg-cell[data-i="${b}"]`).classList.remove('flipped');
        mgFlipped = [];
        mgLocked = false;
      }, 900);
    }
  }
}

function mgFinish() {
  if (mgTimer) { clearInterval(mgTimer); mgTimer = null; }
  const sec = Math.floor((Date.now() - mgStart) / 1000);
  $('mg-runner').style.display = 'none';
  $('mg-result').style.display = 'block';
  $('mg-final-time').textContent = `${Math.floor(sec/60)}:${String(sec%60).padStart(2,'0')}  ·  ${mgMoves} moves`;
  let msg;
  if (mgMoves <= mgPairs.length + 2) msg = 'Perfect memory! Lightning fast.';
  else if (mgMoves <= mgPairs.length * 2) msg = 'Sharp recall — well done.';
  else msg = 'Good practice. Try the flashcards module for spaced repetition.';
  $('mg-final-msg').textContent = msg;
}

$('mg-restart').addEventListener('click', () => {
  if (mgPairs.length) mgStart_game();
  else { $('mg-result').style.display = 'none'; $('mg-picker').style.display = 'block'; }
});

// ===== AUDIO RECAP =====
bindUseLatest('rc-use-latest', 'rc-lesson-id', 'rc-status');

async function rcGenerate(regenerate = false) {
  const lid = $('rc-lesson-id').value.trim();
  if (!lid) { $('rc-status').textContent = 'Enter a lesson_id'; $('rc-status').className='status error'; return; }
  $('rc-status').textContent = regenerate ? 'Regenerating recap…' : 'Generating recap (~15 seconds)…';
  $('rc-status').className = 'status';
  $('rc-generate').disabled = true;
  try {
    const r = await fetch(`/lessons/${lid}/recap${regenerate ? '?regenerate=true' : ''}`,
      { method:'POST', headers:authHeaders() });
    if (!r.ok) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const j = await r.json();
    $('rc-title').textContent = 'Lesson ' + lid.slice(0, 8);
    $('rc-text').textContent = j.text || '(no text)';
    if (j.audio_url) {
      const audio = $('rc-audio');
      audio.src = j.audio_url + '?t=' + Date.now();
      audio.load();
      $('rc-status').textContent = j.cached ? 'Loaded from cache (free, instant).' : 'Generated fresh recap.';
      $('rc-status').className = 'status ok';
    } else {
      $('rc-status').textContent = 'Text ready — but audio synthesis failed: ' + (j.audio_error || 'unknown');
      $('rc-status').className = 'status error';
    }
    $('rc-player').style.display = 'block';
  } catch (err) {
    $('rc-status').textContent = 'Error: ' + err.message;
    $('rc-status').className = 'status error';
  } finally { $('rc-generate').disabled = false; }
}

$('rc-generate').addEventListener('click', () => rcGenerate(false));
$('rc-regen').addEventListener('click', () => rcGenerate(true));
$('rc-download').addEventListener('click', () => {
  const lid = $('rc-lesson-id').value.trim();
  if (!lid) return;
  const a = document.createElement('a');
  a.href = `/lessons/${lid}/recap.mp3`;
  a.download = `recap-${lid.slice(0,8)}.mp3`;
  a.click();
});

// ===== NOTES =====
let ntCurrent = null, ntSaveTimer = null, ntRemoteAvailable = false;

bindUseLatest('nt-use-latest', 'nt-lesson-id', 'nt-status');

$('nt-open').addEventListener('click', async () => {
  const lid = $('nt-lesson-id').value.trim();
  if (!lid) { $('nt-status').textContent = 'Enter a lesson_id'; $('nt-status').className='status error'; return; }
  ntCurrent = lid;
  $('nt-picker').style.display = 'none';
  $('nt-editor').style.display = 'block';
  $('nt-title').textContent = 'Notes · ' + lid.slice(0, 8);
  $('nt-textarea').value = '';
  $('nt-save-state').textContent = 'Loading…';
  // try server first, fall back to localStorage
  ntRemoteAvailable = false;
  try {
    const r = await fetch(`/lessons/${lid}/notes`, { headers: authHeaders() });
    if (r.ok) {
      const j = await r.json();
      $('nt-textarea').value = j.notes || '';
      ntRemoteAvailable = true;
    }
  } catch {}
  if (!ntRemoteAvailable) {
    const local = localStorage.getItem('pathshala_notes_' + lid);
    if (local) $('nt-textarea').value = local;
  }
  $('nt-save-state').textContent = 'All changes saved';
  $('nt-save-state').className = 'nt-save-state saved';
  $('nt-textarea').focus();
});

$('nt-textarea').addEventListener('input', () => {
  if (!ntCurrent) return;
  $('nt-save-state').textContent = 'Saving…';
  $('nt-save-state').className = 'nt-save-state saving';
  if (ntSaveTimer) clearTimeout(ntSaveTimer);
  ntSaveTimer = setTimeout(ntSave, 1500);
});

async function ntSave() {
  if (!ntCurrent) return;
  const text = $('nt-textarea').value;
  // always mirror to localStorage as a safety net
  localStorage.setItem('pathshala_notes_' + ntCurrent, text);
  if (ntRemoteAvailable || token) {
    try {
      const fd = new FormData();
      fd.set('notes', text);
      const r = await fetch(`/lessons/${ntCurrent}/notes`, {
        method:'POST', headers:authHeaders(), body:fd,
      });
      if (r.ok) ntRemoteAvailable = true;
    } catch {}
  }
  $('nt-save-state').textContent = 'All changes saved';
  $('nt-save-state').className = 'nt-save-state saved';
}

$('nt-download').addEventListener('click', () => {
  if (!ntCurrent) return;
  const blob = new Blob([$('nt-textarea').value], { type:'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `notes_${ntCurrent.slice(0,8)}.txt`;
  a.click();
});

$('nt-clear').addEventListener('click', () => {
  if (!ntCurrent) return;
  if (!confirm('Clear all notes for this lesson?')) return;
  $('nt-textarea').value = '';
  ntSave();
});

// Tab to indent in textarea
$('nt-textarea').addEventListener('keydown', e => {
  if (e.key === 'Tab') {
    e.preventDefault();
    const start = e.target.selectionStart, end = e.target.selectionEnd;
    e.target.value = e.target.value.slice(0, start) + '  ' + e.target.value.slice(end);
    e.target.selectionStart = e.target.selectionEnd = start + 2;
  }
});

// ===== LIVE LECTURE =====
let liveRecognition = null, liveHistory = [], liveSpeaking = null;
const liveSupported = ('webkitSpeechRecognition' in window) || ('SpeechRecognition' in window);

function liveSetStatus(text, kind) {
  const el = $('live-status');
  el.textContent = text;
  el.className = 'status' + (kind ? (' ' + kind) : '');
}

function liveSetButton(state, label) {
  const btn = $('live-mic');
  btn.classList.remove('listening', 'thinking', 'speaking');
  if (state) btn.classList.add(state);
  $('live-mic-label').textContent = label;
}

function liveAddTurn(who, text) {
  $('live-transcript-card').style.display = 'block';
  const div = document.createElement('div');
  div.className = 'live-turn ' + who;
  div.innerHTML = `<span class="who">${who === 'user' ? 'You said' : 'AI tutor'}</span>${text}`;
  $('live-transcript').appendChild(div);
  $('live-transcript').scrollTop = $('live-transcript').scrollHeight;
}

async function liveHandleTranscript(transcript) {
  liveAddTurn('user', transcript);
  liveSetButton('thinking', 'Thinking…');
  liveSetStatus('AI tutor is thinking…', '');
  try {
    const fd = new FormData();
    fd.set('transcript', transcript);
    fd.set('history_json', JSON.stringify(liveHistory.slice(-8)));
    const r = await fetch('/live/respond', { method:'POST', headers:authHeaders(), body:fd });
    if (!r.ok) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const j = await r.json();
    liveHistory.push({ role:'user', content: transcript });
    liveHistory.push({ role:'assistant', content: j.reply });
    liveAddTurn('tutor', j.reply);
    liveSpeak(j.reply);
  } catch (err) {
    const msg = err.message.includes('not configured')
      ? 'AI service not set up on this server — set ANTHROPIC_API_KEY to enable AI replies.'
      : 'Error: ' + err.message;
    liveSetStatus(msg, 'error');
    liveSetButton(null, 'Tap to speak');
  }
}

function liveSpeak(text) {
  if (!('speechSynthesis' in window)) {
    liveSetStatus('Browser does not support text-to-speech.', 'error');
    liveSetButton(null, 'Tap to speak');
    return;
  }
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  const lang = $('live-lang-sel').value;
  u.lang = lang;
  u.rate = 0.95; u.pitch = 1.0;
  liveSpeaking = u;
  u.onstart = () => { liveSetButton('speaking', 'Speaking…'); liveSetStatus('AI is speaking…', ''); };
  u.onend = () => {
    liveSpeaking = null;
    liveSetButton(null, 'Tap to speak');
    liveSetStatus('Ready. Tap the mic to continue the conversation.', 'ok');
  };
  u.onerror = () => {
    liveSpeaking = null;
    liveSetButton(null, 'Tap to speak');
    liveSetStatus('TTS error. Tap the mic again to retry.', 'error');
  };
  window.speechSynthesis.speak(u);
}

function liveStart() {
  if (!liveSupported) {
    liveSetStatus('Voice input requires Chrome, Edge, or Safari.', 'error'); return;
  }
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  const rec = new Rec();
  rec.lang = $('live-lang-sel').value;
  rec.continuous = false;
  rec.interimResults = false;
  rec.maxAlternatives = 1;
  rec.onstart = () => {
    liveSetButton('listening', 'Listening… tap to stop');
    liveSetStatus('Listening — speak naturally.', '');
  };
  rec.onresult = (e) => {
    const transcript = e.results[0][0].transcript;
    if (transcript && transcript.trim()) liveHandleTranscript(transcript.trim());
    else { liveSetButton(null, 'Tap to speak'); liveSetStatus("Didn't catch that. Try again.", 'error'); }
  };
  rec.onerror = (e) => {
    liveSetButton(null, 'Tap to speak');
    liveSetStatus('Mic error: ' + e.error + '. Check permissions.', 'error');
  };
  rec.onend = () => {
    if ($('live-mic').classList.contains('listening')) {
      liveSetButton(null, 'Tap to speak');
      liveSetStatus('Ready. Tap the mic to start.', '');
    }
  };
  rec.start();
  liveRecognition = rec;
}

function liveStop() {
  if (liveRecognition) {
    try { liveRecognition.stop(); } catch {}
    liveRecognition = null;
  }
}

$('live-mic').addEventListener('click', () => {
  if (!requireAuthOrPrompt()) return;
  const btn = $('live-mic');
  if (btn.classList.contains('listening')) { liveStop(); return; }
  if (btn.classList.contains('thinking') || btn.classList.contains('speaking')) {
    window.speechSynthesis.cancel();
    liveSetButton(null, 'Tap to speak');
    liveSetStatus('Stopped. Tap the mic to ask again.', '');
    return;
  }
  liveStart();
});

$('live-clear').addEventListener('click', () => {
  liveHistory = [];
  $('live-transcript').innerHTML = '';
  $('live-transcript-card').style.display = 'none';
  liveSetStatus('New conversation. Tap the mic to start.', '');
});

$('live-stop-speak').addEventListener('click', () => {
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  liveSetButton(null, 'Tap to speak');
  liveSetStatus('Voice stopped.', '');
});

if (!liveSupported) {
  liveSetStatus('Voice input requires Chrome, Edge, or Safari. The conversation still works if you type — coming in next update.', 'error');
}

// ===== VOICE TUTOR =====
let vtRecognition = null, vtHistory = [], vtSpeaking = null;
const vtSupported = ('webkitSpeechRecognition' in window) || ('SpeechRecognition' in window);

function vtSetStatus(text, kind) {
  const el = $('vt-status');
  el.textContent = text;
  el.className = 'status' + (kind ? (' ' + kind) : '');
}

function vtSetButton(state, label) {
  const btn = $('vt-mic');
  btn.classList.remove('listening', 'thinking', 'speaking');
  if (state) btn.classList.add(state);
  $('vt-mic-label').textContent = label;
}

function vtAddTurn(who, text) {
  $('vt-transcript-card').style.display = 'block';
  const div = document.createElement('div');
  div.className = 'live-turn ' + who;
  div.innerHTML = `<span class="who">${who === 'user' ? 'You said' : 'AI tutor'}</span>${text}`;
  $('vt-transcript').appendChild(div);
  $('vt-transcript').scrollTop = $('vt-transcript').scrollHeight;
}

async function vtHandleTranscript(transcript) {
  vtAddTurn('user', transcript);
  vtSetButton('thinking', 'Thinking…');
  vtSetStatus('AI tutor is thinking…', '');
  try {
    const fd = new FormData();
    fd.set('transcript', transcript);
    fd.set('history_json', JSON.stringify(vtHistory.slice(-8)));
    const lessonId = $('vt-lesson-id') ? $('vt-lesson-id').value.trim() : '';
    if (lessonId) fd.set('lesson_id', lessonId);
    const r = await fetch('/voice/respond', { method: 'POST', headers: authHeaders(), body: fd });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const j = await r.json();
    vtHistory.push({ role: 'user', content: transcript });
    vtHistory.push({ role: 'assistant', content: j.reply });
    vtAddTurn('tutor', j.reply);
    vtSpeak(j.reply);
  } catch (err) {
    const msg = err.message.includes('not configured')
      ? 'AI service not set up on this server — set ANTHROPIC_API_KEY to enable voice replies.'
      : 'Error: ' + err.message;
    vtSetStatus(msg, 'error');
    vtSetButton(null, 'Tap to speak');
  }
}

function vtSpeak(text) {
  if (!('speechSynthesis' in window)) {
    vtSetStatus('Browser does not support text-to-speech.', 'error');
    vtSetButton(null, 'Tap to speak');
    return;
  }
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  const lang = $('vt-lang-sel').value;
  u.lang = lang;
  u.rate = 0.92; u.pitch = 1.0;
  vtSpeaking = u;
  u.onstart = () => { vtSetButton('speaking', 'Speaking…'); vtSetStatus('AI tutor is speaking…', ''); };
  u.onend = () => {
    vtSpeaking = null;
    vtSetButton(null, 'Tap to speak');
    vtSetStatus('Ready. Tap the mic to continue.', 'ok');
  };
  u.onerror = () => {
    vtSpeaking = null;
    vtSetButton(null, 'Tap to speak');
    vtSetStatus('TTS error. Tap the mic again to retry.', 'error');
  };
  window.speechSynthesis.speak(u);
}

function vtStart() {
  if (!vtSupported) {
    vtSetStatus('Voice input requires Chrome, Edge, or Safari.', 'error'); return;
  }
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  const rec = new Rec();
  rec.lang = $('vt-lang-sel').value;
  rec.continuous = false;
  rec.interimResults = false;
  rec.maxAlternatives = 1;
  rec.onstart = () => {
    vtSetButton('listening', 'Listening… tap to stop');
    vtSetStatus('Listening — speak naturally.', '');
  };
  rec.onresult = (e) => {
    const transcript = e.results[0][0].transcript;
    if (transcript && transcript.trim()) vtHandleTranscript(transcript.trim());
    else { vtSetButton(null, 'Tap to speak'); vtSetStatus("Didn't catch that. Try again.", 'error'); }
  };
  rec.onerror = (e) => {
    vtSetButton(null, 'Tap to speak');
    vtSetStatus('Mic error: ' + e.error + '. Check permissions.', 'error');
  };
  rec.onend = () => {
    if ($('vt-mic').classList.contains('listening')) {
      vtSetButton(null, 'Tap to speak');
      vtSetStatus('Ready. Tap the mic to start.', '');
    }
  };
  rec.start();
  vtRecognition = rec;
}

function vtStop() {
  if (vtRecognition) {
    try { vtRecognition.stop(); } catch {}
    vtRecognition = null;
  }
}

$('vt-mic').addEventListener('click', () => {
  if (!requireAuthOrPrompt()) return;
  const btn = $('vt-mic');
  if (btn.classList.contains('listening')) { vtStop(); return; }
  if (btn.classList.contains('thinking') || btn.classList.contains('speaking')) {
    window.speechSynthesis.cancel();
    vtSetButton(null, 'Tap to speak');
    vtSetStatus('Stopped. Tap the mic to ask again.', '');
    return;
  }
  vtStart();
});

$('vt-clear').addEventListener('click', () => {
  vtHistory = [];
  $('vt-transcript').innerHTML = '';
  $('vt-transcript-card').style.display = 'none';
  vtSetStatus('New conversation. Tap the mic to start.', '');
});

$('vt-stop-speak').addEventListener('click', () => {
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  vtSetButton(null, 'Tap to speak');
  vtSetStatus('Voice stopped.', '');
});

if (!vtSupported) {
  vtSetStatus('Voice input requires Chrome, Edge, or Safari.', 'error');
}

// ===== ESSAY GRADER =====
let egRubrics = [];

async function egLoadRubrics() {
  const exam = $('eg-exam').value;
  try {
    const r = await fetch(`/api/essay/rubrics?exam=${encodeURIComponent(exam)}`, { headers: authHeaders() });
    const j = await r.json().catch(() => ({ rows: [] }));
    egRubrics = j.rows || [];
    const sel = $('eg-rubric-sel');
    sel.innerHTML = '';
    if (!egRubrics.length) {
      sel.innerHTML = '<option value="">No rubrics for this exam yet</option>';
      $('eg-submit').disabled = true;
    } else {
      egRubrics.forEach(rb => {
        const opt = document.createElement('option');
        opt.value = rb.id;
        opt.textContent = `${rb.paper}${rb.topic ? ' — ' + rb.topic : ''} (${rb.max_marks} marks)`;
        sel.appendChild(opt);
      });
      $('eg-submit').disabled = false;
    }
  } catch {}
}

$('eg-exam').addEventListener('change', egLoadRubrics);

document.addEventListener('moduleShow', (e) => {
  if (e.detail === 'essay') {
    egLoadRubrics();
    showAiNote('eg-status', 'essay_grader');
  }
  if (e.detail === 'voice') showAiNote('vt-status', 'voice_tutor');
  if (e.detail === 'live') showAiNote('live-status', 'live_lecture');
  if (e.detail === 'mathvision') showAiNote('mv-status', 'math_vision');
  if (e.detail === 'interview') showAiNote('mi-status', 'mock_interview');
});

$('eg-submit').addEventListener('click', async () => {
  if (!requireAuthOrPrompt()) return;
  const rubricId = $('eg-rubric-sel').value;
  const text = $('eg-text').value.trim();
  if (!rubricId) {
    $('eg-status').textContent = 'No rubric available for this exam. Ask your teacher to add one.';
    $('eg-status').className = 'status error';
    return;
  }
  if (!text || text.split(/\s+/).length < 10) {
    $('eg-status').textContent = 'Please write at least 10 words for grading.';
    $('eg-status').className = 'status error';
    return;
  }
  $('eg-status').textContent = 'Grading your answer… this may take 10-20 seconds.';
  $('eg-status').className = 'status';
  $('eg-submit').disabled = true;
  try {
    const fd = new FormData();
    fd.set('rubric_id', rubricId);
    fd.set('text', text);
    fd.set('grade_now', 'true');
    const r = await fetch('/api/essay/submissions', { method: 'POST', headers: authHeaders(), body: fd });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const j = await r.json();
    $('eg-status').textContent = '';
    $('eg-form-card').style.display = 'none';
    $('eg-result').style.display = '';
    const grade = j.ai_grade || {};
    const rubric = egRubrics.find(rb => rb.id === rubricId) || {};
    const score = grade.score !== undefined ? grade.score : null;
    $('eg-score-display').textContent = score !== null ? `${score} / ${rubric.max_marks || 100}` : 'Graded';
    const byC = grade.by_criterion || {};
    const criteria = Array.isArray(byC)
      ? byC
      : Object.entries(byC).map(([name, v]) => ({ name, ...v }));
    $('eg-criteria-list').innerHTML = criteria.map(c =>
      `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line);font-size:13px;">
        <span>${c.name || c.criterion}</span><span style="font-weight:600;">${c.score !== undefined ? c.score + '/' + (c.weight || c.max_marks || c.max || '?') : '—'}</span>
      </div>`
    ).join('');
    $('eg-feedback').textContent = grade.summary || grade.overall_feedback || 'Graded — see criteria above.';
    const suggestions = grade.suggestions || [];
    if (suggestions.length) {
      $('eg-feedback').textContent += '\n\nSuggestions:\n' + suggestions.map((s, i) => `${i+1}. ${s}`).join('\n');
    }
    if (grade.error) {
      $('eg-feedback').textContent = 'Grading encountered an issue: ' + grade.error;
    }
    $('eg-model-answer-card').style.display = 'none';
  } catch (err) {
    $('eg-status').textContent = 'Error: ' + err.message;
    $('eg-status').className = 'status error';
  } finally {
    $('eg-submit').disabled = false;
  }
});

$('eg-try-again').addEventListener('click', () => {
  $('eg-result').style.display = 'none';
  $('eg-form-card').style.display = '';
  $('eg-text').value = '';
  $('eg-status').textContent = '';
});

// ===== MATH VISION =====
async function mvSubmitAndShow() {
  if (!requireAuthOrPrompt()) return;
  const imageUrl = $('mv-image-url').value.trim();
  if (!imageUrl.startsWith('http')) {
    $('mv-status').textContent = 'Please enter a valid image URL starting with https://';
    $('mv-status').className = 'status error';
    return;
  }
  $('mv-status').textContent = 'Reading your handwritten math… (10-30 seconds)';
  $('mv-status').className = 'status';
  $('mv-submit').disabled = true;
  try {
    const fd = new FormData();
    fd.set('image_url', imageUrl);
    fd.set('expected_language', $('mv-lang').value);
    fd.set('auto_extract', 'true');
    const r = await fetch('/api/math-vision/submit', { method: 'POST', headers: authHeaders(), body: fd });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const j = await r.json();
    // Handle no-provider / errored state before showing result panel
    if (j.status === 'errored') {
      const errMsg = j.error === 'no_provider'
        ? 'AI not configured — set ANTHROPIC_API_KEY on the server to enable Math Vision.'
        : ('Extraction failed: ' + (j.error || 'unknown error'));
      throw new Error(errMsg);
    }
    if (j.status === 'rejected') {
      throw new Error('Image confidence too low — try a clearer, well-lit photo of the handwritten math.');
    }
    $('mv-status').textContent = '';
    $('mv-form-card').style.display = 'none';
    $('mv-result').style.display = '';
    window._mvSubmissionId = j.id;
    const steps = j.steps || [];
    if (steps.length) {
      $('mv-steps').innerHTML = steps.map((s, i) =>
        `<div style="padding:4px 0;"><span style="color:var(--muted);margin-right:8px;">Step ${i+1}:</span>${s}</div>`
      ).join('');
    } else {
      $('mv-steps').textContent = j.extracted_latex || 'No steps extracted — the image may be unclear.';
    }
    $('mv-validation').textContent = 'Click "Validate steps" to check each step.';
  } catch (err) {
    $('mv-status').textContent = 'Error: ' + err.message;
    $('mv-status').className = 'status error';
  } finally {
    $('mv-submit').disabled = false;
  }
}

$('mv-submit').addEventListener('click', mvSubmitAndShow);

$('mv-validate-btn').addEventListener('click', async () => {
  const sid = window._mvSubmissionId;
  if (!sid) return;
  $('mv-validate-btn').disabled = true;
  $('mv-validation').textContent = 'Validating each step…';
  try {
    const r = await fetch(`/api/math-vision/${sid}/validate`, { method: 'POST', headers: authHeaders() });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const j = await r.json();
    const perStep = j.per_step || [];
    if (perStep.length) {
      $('mv-validation').innerHTML = perStep.map((s, i) => {
        const ok = s.valid === true;
        const col = ok ? '#2e7d32' : '#c62828';
        return `<div style="padding:6px 0;border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:baseline;">
          <span style="font-weight:700;color:${col};">${ok ? '✓' : '✗'}</span>
          <span style="font-size:13px;">${s.step || ('Step ' + (i+1))}</span>
          ${s.note ? `<span style="font-size:12px;color:var(--muted);">${s.note}</span>` : ''}
        </div>`;
      }).join('');
      if (j.first_wrong_step !== null && j.first_wrong_step !== undefined) {
        $('mv-validation').innerHTML += `<div style="margin-top:10px;padding:10px;background:#fff3e0;border-radius:8px;font-size:13px;">First error at step ${j.first_wrong_step + 1}. Check your working from that point.</div>`;
      } else {
        $('mv-validation').innerHTML += `<div style="margin-top:10px;padding:10px;background:#e8f5e9;border-radius:8px;font-size:13px;">All steps valid!</div>`;
      }
    } else {
      $('mv-validation').textContent = j.overall ? 'All steps look correct!' : 'Could not validate — try again.';
    }
  } catch (err) {
    $('mv-validation').textContent = 'Validation error: ' + err.message;
  } finally {
    $('mv-validate-btn').disabled = false;
  }
});

$('mv-try-again').addEventListener('click', () => {
  $('mv-result').style.display = 'none';
  $('mv-form-card').style.display = '';
  $('mv-image-url').value = '';
  $('mv-status').textContent = '';
  window._mvSubmissionId = null;
});

// ===== MOCK INTERVIEW =====
let miInterviewId = null, miCurrentTurnIndex = 0, miRecognition = null;
const miSupported = ('webkitSpeechRecognition' in window) || ('SpeechRecognition' in window);

$('mi-start').addEventListener('click', async () => {
  if (!requireAuthOrPrompt()) return;
  const track = $('mi-track').value;
  $('mi-status').textContent = 'Starting interview…';
  $('mi-status').className = 'status';
  $('mi-start').disabled = true;
  try {
    const fd = new FormData();
    fd.set('track', track);
    const r = await fetch('/api/mock-interviews', { method: 'POST', headers: authHeaders(), body: fd });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const j = await r.json();
    // API returns { interview_id, track, started_at, opener: { turn_index, question_text } }
    miInterviewId = j.interview_id;
    miCurrentTurnIndex = j.opener ? j.opener.turn_index : 0;
    $('mi-status').textContent = '';
    $('mi-start-card').style.display = 'none';
    $('mi-session').style.display = '';
    $('mi-question').textContent = j.opener ? j.opener.question_text : 'Interview started.';
    $('mi-turn-label').textContent = `Question ${miCurrentTurnIndex + 1}`;
    $('mi-transcript-log').innerHTML = '';
  } catch (err) {
    $('mi-status').textContent = 'Error: ' + err.message;
    $('mi-status').className = 'status error';
  } finally {
    $('mi-start').disabled = false;
  }
});

async function miSubmitAnswer(answer) {
  if (!answer.trim() || !miInterviewId) return;
  $('mi-answer-status').textContent = 'Evaluating…';
  $('mi-submit-answer').disabled = true;
  try {
    const fd = new FormData();
    fd.set('turn_index', miCurrentTurnIndex);
    fd.set('answer_text', answer);
    const r = await fetch(`/api/mock-interviews/${miInterviewId}/answer`, { method: 'POST', headers: authHeaders(), body: fd });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    // API returns { feedback, interview_ended, next: { turn_index, question_text } | null }
    const j = await r.json();
    $('mi-answer-status').textContent = '';
    $('mi-text-input').value = '';
    const log = $('mi-transcript-log');
    log.innerHTML += `<div class="card" style="margin-bottom:8px;padding:12px;">
      <div style="font-size:12px;color:var(--muted);margin-bottom:4px;">Your answer</div>
      <div style="font-size:14px;">${answer}</div>
      ${j.feedback ? `<div style="margin-top:8px;font-size:12px;color:#2e7d32;background:#e8f5e9;padding:6px 10px;border-radius:6px;">${typeof j.feedback === 'string' ? j.feedback : (j.feedback.comment || JSON.stringify(j.feedback))}</div>` : ''}
    </div>`;
    log.scrollTop = log.scrollHeight;
    if (j.interview_ended || !j.next) {
      miAutoEnd();
    } else {
      miCurrentTurnIndex = j.next.turn_index;
      $('mi-turn-label').textContent = `Question ${miCurrentTurnIndex + 1}`;
      $('mi-question').textContent = j.next.question_text;
    }
  } catch (err) {
    $('mi-answer-status').textContent = 'Error: ' + err.message;
    $('mi-answer-status').className = 'status error';
  } finally {
    $('mi-submit-answer').disabled = false;
  }
}

async function miAutoEnd() {
  try {
    const r = await fetch(`/api/mock-interviews/${miInterviewId}/end`, { method: 'POST', headers: authHeaders() });
    const j = await r.json().catch(() => ({}));
    miShowReport(j);
  } catch {}
}

function miShowReport(data) {
  $('mi-session').style.display = 'none';
  $('mi-report').style.display = '';
  const score = data.overall_score !== undefined ? data.overall_score : (data.feedback && data.feedback.overall_score);
  $('mi-overall-score').textContent = score !== undefined && score !== null
    ? `Overall score: ${Math.round(score * 10) / 10} / 100`
    : 'Interview complete';
  const fb = data.feedback || {};
  let html = '';
  if (typeof fb === 'string') {
    html = `<p>${fb}</p>`;
  } else {
    // Summaries array (heuristic path) or single summary string
    const summaries = fb.summaries || (fb.summary ? [fb.summary] : []);
    if (summaries.length) html += `<p style="margin-bottom:10px;">${summaries.join(' ')}</p>`;
    if (fb.detailed_feedback) html += `<p>${fb.detailed_feedback}</p>`;

    // criteria_avg is a dict {clarity:8, depth:2, ...}; criteria_averages/criteria may be an array
    const critRaw = fb.criteria_averages || fb.criteria;
    const critDict = fb.criteria_avg;
    let crit = [];
    if (Array.isArray(critRaw) && critRaw.length) {
      crit = critRaw;
    } else if (critDict && typeof critDict === 'object') {
      crit = Object.entries(critDict).map(([name, avg]) => ({ name, avg }));
    }
    if (crit.length) {
      html += '<div style="margin-top:12px;">';
      crit.forEach(c => {
        const val = c.score !== undefined ? c.score : (c.avg !== undefined ? c.avg : null);
        const pct = val !== null ? Math.round((val / (c.max || 10)) * 100) : 50;
        html += `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line);font-size:13px;">
          <span style="text-transform:capitalize;">${c.name || c.criterion}</span>
          <span style="font-weight:600;color:${pct>=70?'#2e7d32':pct>=40?'var(--brand)':'#c62828'}">${val !== null ? val + ' / 10' : '—'}</span>
        </div>`;
      });
      html += '</div>';
    }

    // Improvement tips
    const tips = fb.top_improvements || fb.improvements || [];
    if (tips.length) {
      html += `<div style="margin-top:14px;font-size:13px;"><strong>Areas to improve:</strong><ul style="margin:6px 0 0 18px;">`;
      tips.forEach(t => { html += `<li style="margin-bottom:4px;">${t}</li>`; });
      html += '</ul></div>';
    }

    if (!html) html = `<p>Interview ended. ${score !== undefined ? 'See your score above.' : 'No detailed report available.'}</p>`;
  }
  $('mi-report-body').innerHTML = html;
}

$('mi-submit-answer').addEventListener('click', () => {
  miSubmitAnswer($('mi-text-input').value.trim());
});

$('mi-text-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); miSubmitAnswer($('mi-text-input').value.trim()); }
});

$('mi-mic').addEventListener('click', () => {
  if (!miSupported) { $('mi-answer-status').textContent = 'Voice needs Chrome/Edge/Safari.'; return; }
  const btn = $('mi-mic');
  if (btn.classList.contains('listening')) {
    if (miRecognition) { try { miRecognition.stop(); } catch {} miRecognition = null; }
    btn.classList.remove('listening');
    $('mi-mic-label').textContent = 'Tap to answer';
    return;
  }
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  const rec = new Rec();
  rec.lang = 'en-IN'; rec.continuous = false; rec.interimResults = false;
  rec.onstart = () => { btn.classList.add('listening'); $('mi-mic-label').textContent = 'Listening…'; };
  rec.onresult = (e) => {
    const t = e.results[0][0].transcript;
    $('mi-text-input').value = t;
    btn.classList.remove('listening');
    $('mi-mic-label').textContent = 'Tap to answer';
  };
  rec.onerror = () => { btn.classList.remove('listening'); $('mi-mic-label').textContent = 'Tap to answer'; };
  rec.onend = () => { btn.classList.remove('listening'); $('mi-mic-label').textContent = 'Tap to answer'; };
  rec.start(); miRecognition = rec;
});

$('mi-end').addEventListener('click', () => miAutoEnd());

$('mi-restart').addEventListener('click', () => {
  miInterviewId = null;
  $('mi-report').style.display = 'none';
  $('mi-session').style.display = 'none';
  $('mi-start-card').style.display = '';
  $('mi-status').textContent = '';
  $('mi-transcript-log').innerHTML = '';
});

// ===== ADAPTIVE PRACTICE =====
async function apLoadExamPacks() {
  try {
    const r = await fetch('/api/exam-packs');
    const j = await r.json().catch(() => ({ packs: [] }));
    const packs = j.packs || [];
    const sel = $('ap-pack');
    sel.innerHTML = '';
    if (!packs.length) {
      sel.innerHTML = '<option value="">No exam packs available</option>';
      return;
    }
    packs.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.code;
      opt.textContent = p.title;
      sel.appendChild(opt);
    });
  } catch {}
}

async function apLoadMyPacks() {
  if (!token) {
    $('ap-list').innerHTML = '<div style="color:var(--muted);font-size:13px;">Sign in to see your adaptive packs.</div>';
    $('ap-list-card').style.display = '';
    return;
  }
  try {
    const r = await fetch('/api/adaptive-packs/me', { headers: authHeaders() });
    if (!r.ok) return;
    const j = await r.json();
    const packs = j.packs || [];
    if (packs.length === 0) {
      $('ap-list').innerHTML = '<div style="color:var(--muted);font-size:13px;">No packs yet — select an exam pack above and click Create.</div>';
    } else {
      $('ap-list').innerHTML = packs.map(p =>
        `<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--line);">
          <div>
            <div style="font-weight:600;font-size:14px;">${p.title || p.base_pack_code}</div>
            <div style="font-size:12px;color:var(--muted);">${p.base_pack_code} · Difficulty: ${p.current_difficulty || 'auto'}</div>
          </div>
          <button class="btn-ghost" style="font-size:12px;padding:5px 10px;" onclick="apOpenPack('${p.base_pack_code}')">Topics →</button>
        </div>`
      ).join('');
    }
    $('ap-list-card').style.display = '';
  } catch {}
}

async function apOpenPack(packCode) {
  try {
    const r = await fetch(`/api/adaptive-packs/${packCode}/topics`, { headers: authHeaders() });
    if (!r.ok) {
      $('ap-list').innerHTML = `<div style="color:var(--muted);font-size:13px;">Topics not yet available — the pack is adapting. Try again after your first practice session.</div><button class="btn-ghost" style="margin-top:10px;font-size:12px;" onclick="apLoadMyPacks()">← Back</button>`;
      return;
    }
    const j = await r.json();
    const topics = j.topics || [];
    $('ap-list').innerHTML = `<div style="font-weight:600;font-size:14px;margin-bottom:8px;">${packCode} — Topics &amp; Weightage</div>` +
      (topics.length ? topics.map(t => {
        const label = t.title || t.topic || t.topic_code || '—';
        // mastery (0-1) if present; otherwise show adjusted_weightage as percentage of total
        const mastery = t.mastery !== undefined ? t.mastery : null;
        const wt = t.adjusted_weightage || t.base_weightage || 0;
        const pct = mastery !== null ? Math.round(mastery * 100) : null;
        const color = mastery !== null ? (mastery >= 0.8 ? '#2e7d32' : mastery >= 0.5 ? 'var(--brand)' : '#c62828') : 'var(--muted)';
        const right = pct !== null
          ? `<span style="font-weight:600;color:${color};">${pct}% mastery</span>`
          : `<span style="color:var(--muted);font-size:12px;">${t.is_personalised ? '★ ' : ''}${wt}% weight</span>`;
        return `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line);font-size:13px;">
          <span>${label}</span>${right}
        </div>`;
      }).join('') : '<div style="color:var(--muted);font-size:13px;">No topic data yet.</div>') +
      `<button class="btn-ghost" style="margin-top:10px;font-size:12px;" onclick="apLoadMyPacks()">← Back to my packs</button>`;
  } catch {}
}

$('ap-create').addEventListener('click', async () => {
  if (!requireAuthOrPrompt()) return;
  const pack = $('ap-pack').value;
  if (!pack) {
    $('ap-status').textContent = 'Please select an exam pack.';
    $('ap-status').className = 'status error';
    return;
  }
  $('ap-status').textContent = 'Creating adaptive pack…';
  $('ap-status').className = 'status';
  $('ap-create').disabled = true;
  try {
    const fd = new FormData();
    fd.set('base_pack_code', pack);
    const r = await fetch('/api/adaptive-packs', { method: 'POST', headers: authHeaders(), body: fd });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    $('ap-status').textContent = 'Pack created! You can now view your topic mastery.';
    $('ap-status').className = 'status ok';
    await apLoadMyPacks();
  } catch (err) {
    $('ap-status').textContent = 'Error: ' + err.message;
    $('ap-status').className = 'status error';
  } finally {
    $('ap-create').disabled = false;
  }
});

$('ap-refresh').addEventListener('click', apLoadMyPacks);

// Load on module show
document.addEventListener('moduleShow', (e) => {
  if (e.detail === 'adaptive') {
    apLoadExamPacks(); apLoadMyPacks();
    showAiNote('ap-status', 'ai_synthesis');
  }
});

// ===== PRACTICE TESTS =====
// answers format: {question_id: "a"} — letter chosen, maps to correct_answer comparison
let ptCurrentTest = null;
let ptTimerInterval = null;
const PT_LETTERS = ['a','b','c','d','e'];

async function ptLoadHistory() {
  if (!token) {
    $('pt-history').innerHTML = '<em style="color:var(--muted-text);">Sign in to see your test history.</em>';
    return;
  }
  try {
    const r = await fetch('/api/practice-tests', { headers: authHeaders() });
    const data = await r.json();
    if (!data.rows || data.rows.length === 0) {
      $('pt-history').innerHTML = '<em style="color:var(--muted-text);">No tests yet.</em>';
      return;
    }
    $('pt-history').innerHTML = data.rows.map(t => {
      const scoreStr = (t.score != null && t.max != null) ? `${t.score}/${t.max}` : '—';
      const statusHtml = t.status === 'submitted'
        ? `<span style="color:var(--green,#22c55e);">${scoreStr}</span>`
        : `<span style="color:var(--accent);">${t.status}</span>`;
      const dt = t.submitted_at ? new Date(t.submitted_at * 1000).toLocaleDateString() : 'in progress';
      return `<div style="padding:8px 0; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; font-size:13px;">
        <span><strong>${t.exam}</strong> · ${t.subject} · ${t.question_count} Qs · ${t.target_minutes}min</span>
        <span>${statusHtml} <span style="color:var(--muted-text);">${dt}</span></span>
      </div>`;
    }).join('');
  } catch(e) {
    $('pt-history').innerHTML = `<em style="color:var(--error);">Could not load history: ${e.message}</em>`;
  }
}

function ptRenderQuestions(questions) {
  $('pt-questions').innerHTML = questions.map((q, i) => {
    const qid = q.id || `q${i}`;
    const opts = (q.options && q.options.length)
      ? q.options.map((o, j) => `
          <label style="display:flex;gap:8px;align-items:flex-start;margin:6px 0;cursor:pointer;">
            <input type="radio" name="ptq-${qid}" data-qid="${qid}" data-letter="${PT_LETTERS[j]}" style="margin-top:3px;"> <span>${o}</span>
          </label>`).join('')
      : `<textarea id="pttext-${qid}" data-qid="${qid}" rows="3" style="width:100%;margin-top:8px;padding:8px;" placeholder="Your answer…"></textarea>`;
    return `<div class="card" style="margin-bottom:10px;padding:14px;">
      <div style="font-weight:600;margin-bottom:8px;line-height:1.5;">Q${i+1}. ${q.question_text}</div>
      ${opts}
    </div>`;
  }).join('');
}

function ptStartTimer(minutes) {
  let remaining = minutes * 60;
  const update = () => {
    if (remaining < 0) return;
    const m = Math.floor(remaining / 60);
    const s = remaining % 60;
    $('pt-timer').textContent = `${m}:${s.toString().padStart(2,'0')}`;
    if (remaining === 0) {
      clearInterval(ptTimerInterval);
      $('pt-timer').textContent = 'Time up!';
      $('pt-timer').style.color = 'var(--error, #ef4444)';
      ptSubmitTest();
    }
    remaining--;
  };
  update();
  ptTimerInterval = setInterval(update, 1000);
}

async function ptSubmitTest() {
  if (!ptCurrentTest) return;
  clearInterval(ptTimerInterval);

  // Build answers dict: {question_id: chosen_letter}
  const answers = {};
  for (const q of ptCurrentTest.questions) {
    const qid = q.id;
    // MCQ: find checked radio
    const checked = document.querySelector(`input[name="ptq-${qid}"]:checked`);
    if (checked) {
      answers[qid] = checked.dataset.letter;
      continue;
    }
    // Free text: look for textarea
    const ta = document.getElementById(`pttext-${qid}`);
    if (ta && ta.value.trim()) {
      answers[qid] = ta.value.trim();
    }
  }

  ptSetSubmitStatus('Submitting…', '');
  try {
    const fd = new FormData();
    fd.append('answers_json', JSON.stringify(answers));
    const r = await fetch(`/api/practice-tests/${ptCurrentTest.id}/submit`, { method: 'POST', headers: authHeaders(), body: fd });
    if (!r.ok) { const e = await r.json(); throw new Error(e.detail || r.statusText); }
    const result = await r.json();
    const score = result.score || {};
    const pct = score.pct != null ? Math.round(score.pct * 100) : null;

    // Build per-question review from score + stored questions
    const qMap = Object.fromEntries((ptCurrentTest.questions || []).map(q => [q.id, q]));
    const reviewHtml = (score.per_question || []).map((rv, i) => {
      const origQ = qMap[rv.question_id] || {};
      return `<div style="padding:10px; margin-bottom:6px; border-radius:6px; background:${rv.is_correct ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)'}; border-left:3px solid ${rv.is_correct ? '#22c55e' : '#ef4444'};">
        <div style="font-weight:600; margin-bottom:4px;">${rv.is_correct ? '✓' : '✗'} Q${i+1}: ${origQ.question_text || ''}</div>
        <div style="font-size:12px; color:var(--muted-text);">Your answer: <strong>${rv.chosen || '(blank)'}</strong>${rv.is_correct ? '' : ` · Correct: <strong>${rv.correct}</strong>`}</div>
      </div>`;
    }).join('');

    $('pt-test-card').style.display = 'none';
    $('pt-report-card').style.display = '';
    $('pt-report').innerHTML = `
      <div style="font-size:2.4em; font-weight:700; color:var(--accent); margin-bottom:4px;">${score.total ?? '?'} / ${score.max ?? '?'}</div>
      <div style="color:var(--muted-text); margin-bottom:18px; font-size:1.1em;">${pct != null ? pct + '% correct' : ''}</div>
      ${reviewHtml}`;
    await ptLoadHistory();
  } catch(e) {
    ptSetSubmitStatus('Error: ' + e.message, 'error');
  }
}

function ptSetStatus(msg, cls) {
  const el = $('pt-status');
  el.textContent = msg; el.className = 'status ' + (cls || '');
}

function ptSetSubmitStatus(msg, cls) {
  const el = $('pt-submit-status');
  el.textContent = msg; el.className = 'status ' + (cls || '');
}

$('pt-create').addEventListener('click', async () => {
  if (!requireAuthOrPrompt()) return;
  const exam = $('pt-exam').value;
  const subject = ($('pt-subject').value || '').trim();
  const minutes = parseInt($('pt-minutes').value);
  if (!subject) { ptSetStatus('Please enter a subject.', 'error'); return; }

  ptSetStatus('Generating test…', '');
  $('pt-create').disabled = true;
  try {
    // 1. Create the test record
    const fd = new FormData();
    fd.append('exam', exam);
    fd.append('subject', subject);
    fd.append('target_minutes', minutes);
    const r = await fetch('/api/practice-tests', { method: 'POST', headers: authHeaders(), body: fd });
    if (!r.ok) { const e = await r.json(); throw new Error(e.detail || r.statusText); }
    const test = await r.json();

    // 2. Start the test (marks it in_progress)
    const r2 = await fetch(`/api/practice-tests/${test.id}/start`, { method: 'POST', headers: authHeaders(), body: new FormData() });
    if (!r2.ok) { const e2 = await r2.json(); throw new Error(e2.detail || r2.statusText); }

    // 3. Fetch full test with questions
    const r3 = await fetch(`/api/practice-tests/${test.id}`, { headers: authHeaders() });
    if (!r3.ok) { const e3 = await r3.json(); throw new Error(e3.detail || r3.statusText); }
    const fullTest = await r3.json();
    ptCurrentTest = fullTest;

    if (!fullTest.questions || fullTest.questions.length === 0) {
      ptSetStatus('No questions found for this exam/subject combination. Try "generic" exam or a different subject.', 'error');
      $('pt-create').disabled = false;
      return;
    }

    ptSetStatus('', '');
    $('pt-form-card').style.display = 'none';
    $('pt-test-card').style.display = '';
    $('pt-report-card').style.display = 'none';
    $('pt-test-title').textContent = `${exam.toUpperCase()} · ${subject} · ${minutes} min`;
    // Warn if server generated placeholder questions (no question bank + no API key)
    if (fullTest.generation_method === 'placeholder') {
      $('pt-submit-status').textContent =
        'Note: real questions require ANTHROPIC_API_KEY on the server. '
        + 'These are placeholder questions for layout testing.';
      $('pt-submit-status').className = 'status';
    }
    ptRenderQuestions(fullTest.questions);
    clearInterval(ptTimerInterval);
    ptStartTimer(minutes);
    await ptLoadHistory();
  } catch(e) {
    ptSetStatus('Error: ' + e.message, 'error');
  } finally {
    $('pt-create').disabled = false;
  }
});

$('pt-submit').addEventListener('click', ptSubmitTest);

$('pt-new').addEventListener('click', () => {
  ptCurrentTest = null;
  clearInterval(ptTimerInterval);
  $('pt-form-card').style.display = '';
  $('pt-test-card').style.display = 'none';
  $('pt-report-card').style.display = 'none';
  ptSetStatus('', '');
});

$('pt-refresh').addEventListener('click', ptLoadHistory);

document.addEventListener('moduleShow', (e) => {
  if (e.detail === 'practice') {
    ptLoadHistory();
    showAiNote('pt-status', 'ai_synthesis');
  }
});

// ===== EXPLAINER =====
let exLast = null;

document.querySelectorAll('.ex-chip').forEach(c => {
  c.addEventListener('click', () => {
    $('ex-topic').value = c.dataset.topic;
    $('ex-topic').focus();
  });
});

async function exRun(regenerate) {
  const topic = $('ex-topic').value.trim();
  if (topic.length < 2) {
    $('ex-status').textContent = 'Enter a topic (at least 2 characters).';
    $('ex-status').className = 'status error'; return;
  }
  $('ex-status').textContent = regenerate ? 'Regenerating…' : 'Thinking… (cached topics return instantly)';
  $('ex-status').className = 'status';
  $('ex-go').disabled = true;
  try {
    const fd = new FormData();
    fd.set('topic', topic);
    fd.set('language', $('ex-lang').value);
    fd.set('level', $('ex-level').value);
    if (regenerate) fd.set('regenerate', 'true');
    const r = await fetch('/explain', { method:'POST', headers:authHeaders(), body:fd });
    if (!r.ok) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const j = await r.json();
    exLast = j;
    exRender(j);
    $('ex-status').textContent = j.cached
      ? 'From cache — instant, free.'
      : 'Generated fresh.';
    $('ex-status').className = 'status ok';
  } catch (err) {
    $('ex-status').textContent = 'Error: ' + err.message;
    $('ex-status').className = 'status error';
  } finally { $('ex-go').disabled = false; }
}

function exRender(j) {
  $('ex-output').style.display = 'block';
  $('ex-out-topic').textContent = j.topic || $('ex-topic').value;
  const levelLabel = $('ex-level').selectedOptions[0]?.textContent || j.level || '';
  const langLabel = $('ex-lang').selectedOptions[0]?.textContent || j.language || '';
  $('ex-out-meta').textContent = `${langLabel} · ${levelLabel}`;
  const badge = $('ex-out-cached');
  badge.classList.toggle('show', !!j.cached);
  badge.textContent = j.cached ? '⚡ Cached' : '🆕 Fresh';
  $('ex-out-oneliner').textContent = j.one_liner || '';
  $('ex-out-explanation').textContent = j.explanation || '';
  $('ex-out-keypoints').innerHTML = (j.key_points || []).map(k =>
    `<li>${exEscape(k)}</li>`).join('');
  $('ex-out-example').textContent = j.worked_example || '';
  $('ex-out-analogy').textContent = j.analogy || '';
  const mistakes = j.common_mistakes || [];
  $('ex-mistakes-section').style.display = mistakes.length ? 'block' : 'none';
  $('ex-out-mistakes').innerHTML = mistakes.map(m =>
    `<li>${exEscape(m)}</li>`).join('');
  $('ex-output').scrollIntoView({ behavior:'smooth', block:'start' });
}

function exEscape(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}

$('exf').addEventListener('submit', e => { e.preventDefault(); exRun(false); });
$('ex-regen').addEventListener('click', () => exRun(true));
$('ex-copy').addEventListener('click', async () => {
  if (!exLast) return;
  const j = exLast;
  const text = [
    `# ${j.topic}`,
    '',
    j.one_liner,
    '',
    '## In plain words',
    j.explanation,
    '',
    '## Key points',
    ...(j.key_points || []).map(k => `- ${k}`),
    '',
    '## Worked example',
    j.worked_example,
    '',
    '## Analogy',
    j.analogy,
    ...((j.common_mistakes || []).length
      ? ['', '## Common mistakes', ...(j.common_mistakes).map(m => `- ${m}`)] : []),
  ].join('\n');
  try {
    await navigator.clipboard.writeText(text);
    $('ex-status').textContent = 'Copied to clipboard.';
    $('ex-status').className = 'status ok';
  } catch {
    $('ex-status').textContent = 'Clipboard blocked — select & copy the text manually.';
    $('ex-status').className = 'status error';
  }
});
function exStepMark(stepName, state) {
  const el = document.querySelector(`.ex-step[data-step="${stepName}"]`);
  if (!el) return;
  el.classList.remove('active', 'done');
  if (state) el.classList.add(state);
}
function exVideoStatus(text, kind) {
  const el = $('ex-video-status');
  el.textContent = text;
  el.classList.remove('done', 'error');
  if (kind) el.classList.add(kind);
}

let exVideoPollTimer = null, exVideoLessonId = null;

async function exMakeVideo() {
  if (!exLast) return;
  const topic = exLast.topic || $('ex-topic').value;
  const language = $('ex-lang').value;
  const level = $('ex-level').value;

  $('ex-video-card').style.display = 'block';
  $('ex-video-player').style.display = 'none';
  $('ex-video-actions').style.display = 'none';
  exStepMark('generate', 'active');
  exStepMark('narrate', null);
  exStepMark('render', null);
  exStepMark('encode', null);
  exVideoStatus('Queuing render — this takes 60-90 seconds for a fresh topic, or returns instantly if cached.', null);
  $('ex-video-card').scrollIntoView({ behavior:'smooth', block:'start' });
  $('ex-make-video').disabled = true;

  try {
    const fd = new FormData();
    fd.set('topic', topic);
    fd.set('language', language);
    fd.set('level', level);
    fd.set('teacher', 'true');
    const r = await fetch('/explain/video', { method:'POST', headers:authHeaders(), body:fd });
    if (!r.ok && r.status !== 202) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const j = await r.json();
    exStepMark('generate', 'done');
    exStepMark('narrate', 'active');
    exVideoPoll(j.job_id);
  } catch (err) {
    exVideoStatus('Error: ' + err.message, 'error');
    $('ex-make-video').disabled = false;
  }
}

function exVideoPoll(jobId) {
  if (exVideoPollTimer) clearInterval(exVideoPollTimer);
  let ticks = 0;
  exVideoPollTimer = setInterval(async () => {
    ticks++;
    try {
      const r = await fetch(`/jobs/${jobId}`, { headers:authHeaders() });
      const j = await r.json();
      if (j.status === 'succeeded') {
        clearInterval(exVideoPollTimer); exVideoPollTimer = null;
        exStepMark('narrate', 'done');
        exStepMark('render', 'done');
        exStepMark('encode', 'done');
        exVideoLessonId = (j.result && j.result.lesson_id) || null;
        const cacheHit = j.result && j.result.cache_hit;
        exVideoStatus(cacheHit
          ? '⚡ From cache — instant, free. The same topic was rendered earlier.'
          : '✓ Render complete. Press play.',
          'done');
        const v = $('ex-video-player');
        v.src = `/jobs/${jobId}/video`;
        v.style.display = 'block';
        $('ex-video-actions').style.display = 'flex';
        $('ex-make-video').disabled = false;
      } else if (j.status === 'failed') {
        clearInterval(exVideoPollTimer); exVideoPollTimer = null;
        exVideoStatus('Render failed: ' + (j.error || 'unknown'), 'error');
        $('ex-make-video').disabled = false;
      } else {
        // Animate the step indicators based on elapsed ticks
        if (ticks > 4) exStepMark('narrate', 'done');
        if (ticks > 4) exStepMark('render', 'active');
        if (ticks > 12) { exStepMark('render', 'done'); exStepMark('encode', 'active'); }
        exVideoStatus(`Working… status: ${j.status} (${ticks * 2}s elapsed)`, null);
      }
    } catch (err) {
      // transient network — just keep polling
    }
  }, 2000);
}

$('ex-make-video').addEventListener('click', exMakeVideo);
$('ex-video-download').addEventListener('click', () => {
  const src = $('ex-video-player').src;
  if (!src) return;
  const a = document.createElement('a');
  a.href = src; a.download = `explainer_${(exLast?.topic||'video').slice(0,30).replace(/[^a-z0-9]+/gi,'_')}.mp4`;
  a.click();
});
$('ex-video-flashcards').addEventListener('click', () => {
  if (!exVideoLessonId) return;
  showModule('flashcards');
  $('fc-lesson-id').value = exVideoLessonId;
});
$('ex-video-recap').addEventListener('click', () => {
  if (!exVideoLessonId) return;
  showModule('recap');
  $('rc-lesson-id').value = exVideoLessonId;
});
$('ex-video-chat').addEventListener('click', () => {
  if (!exVideoLessonId) return;
  showModule('chat');
  if ($('chat-lesson-id')) $('chat-lesson-id').value = exVideoLessonId;
});

$('ex-save-notes').addEventListener('click', () => {
  if (!exLast) return;
  const j = exLast;
  const key = 'explainer_' + (j.topic || $('ex-topic').value).slice(0, 40).replace(/[^a-z0-9]+/gi, '_').toLowerCase();
  const text = [
    `${j.topic}`,
    '',
    j.one_liner,
    '',
    '— In plain words —',
    j.explanation,
    '',
    '— Key points —',
    ...(j.key_points || []).map(k => `• ${k}`),
    '',
    '— Worked example —',
    j.worked_example,
    '',
    '— Analogy —',
    j.analogy,
  ].join('\n');
  localStorage.setItem('pathshala_notes_' + key, text);
  $('nt-lesson-id').value = key;
  $('ex-status').textContent = 'Saved to Notes. Open the Notes module to view.';
  $('ex-status').className = 'status ok';
});

// ============================================================================
// VIDEO STUDIO — PRD §15 Screens 1-5 unified flow (the v0.7 customer)
// ============================================================================
let vsCurrentRequestId = null;
let vsCurrentResult = null;
let vsPollHandle = null;
let vsSourceType = 'topic';

function vsSetStep(n) {
  for (let i = 1; i <= 4; i++) {
    $('vs-step-' + i).style.display = i === n ? 'block' : 'none';
  }
  document.querySelectorAll('.vs-step').forEach(el => {
    const s = parseInt(el.dataset.step, 10);
    el.classList.toggle('active', s === n);
    el.classList.toggle('done', s < n);
  });
}

// Source picker
document.querySelectorAll('.vs-source-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.vs-source-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    vsSourceType = btn.dataset.source;
    $('vs-source-topic').style.display = vsSourceType === 'topic' ? 'block' : 'none';
    $('vs-source-file').style.display  = vsSourceType === 'file'  ? 'block' : 'none';
  });
});

// Populate mode dropdown from /api/v2/video-modes
let vsModes = [];
async function vsLoadModes() {
  try {
    const r = await fetch('/api/v2/video-modes');
    const j = await r.json();
    vsModes = j.video_modes;
    const sel = $('vs-mode');
    sel.innerHTML = vsModes.map(m =>
      `<option value="${m.id}">${m.name}</option>`).join('');
    sel.value = 'teaching';
    vsUpdateModeHint();
  } catch {
    // graceful fallback to hardcoded list if server unreachable
    $('vs-mode').innerHTML = ['teaching','explainer','revision','kids','teacher','parent','training','awareness','reel']
      .map(m => `<option value="${m}">${m}</option>`).join('');
  }
}
function vsUpdateModeHint() {
  const m = vsModes.find(x => x.id === $('vs-mode').value);
  if (!m) { $('vs-mode-hint').textContent = ''; return; }
  $('vs-mode-hint').textContent =
    `${m.description} · ${m.scene_count} scenes · ~${m.default_duration_seconds}s` +
    (m.needs_quiz ? ' · quiz at end' : '') +
    (m.needs_cta ? ' · ends with CTA' : '');
}
$('vs-mode').addEventListener('change', vsUpdateModeHint);
vsLoadModes();

// Step 1 → 2
$('vs-go-customize').addEventListener('click', () => {
  if (vsSourceType === 'topic' && !$('vs-topic').value.trim()) {
    alert('Please type a topic to explain.'); return;
  }
  if (vsSourceType === 'file' && !$('vs-file').files[0]) {
    alert('Please choose a file.'); return;
  }
  vsSetStep(2);
});

// Step 2 → 1 (back)
$('vs-back-1').addEventListener('click', () => vsSetStep(1));

// Step 2 → 3 (generate)
$('vs-generate').addEventListener('click', async () => {
  const s = $('vs-customize-status');
  s.textContent = 'Queuing your video request…'; s.className = 'status';
  $('vs-generate').disabled = true;
  try {
    const fd = new FormData();
    if (vsSourceType === 'topic') {
      fd.set('topic', $('vs-topic').value.trim());
    } else {
      fd.set('image', $('vs-file').files[0]);
    }
    fd.set('video_mode',   $('vs-mode').value);
    fd.set('user_type',    $('vs-user-type').value);
    fd.set('age',          $('vs-age').value);
    fd.set('grade',        $('vs-grade').value);
    fd.set('language',     $('vs-language').value);
    if ($('vs-tone').value)     fd.set('tone', $('vs-tone').value);
    if ($('vs-duration').value) fd.set('duration_seconds', $('vs-duration').value);
    fd.set('output_format', $('vs-format').value);
    const r = await fetch('/api/v2/video-requests', { method:'POST', headers:authHeaders(), body:fd });
    if (!r.ok && r.status !== 202) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const j = await r.json();
    vsCurrentRequestId = j.video_request_id;
    s.textContent = '';
    vsSetStep(3);
    vsStartPolling(j);
  } catch (err) {
    s.textContent = 'Error: ' + err.message; s.className = 'status error';
  } finally { $('vs-generate').disabled = false; }
});

function vsStartPolling(initial) {
  $('vs-progress-title').textContent = 'Generating your video…';
  $('vs-progress-meta').textContent =
    `~${initial.estimated_time_seconds || 90}s estimated · request ${vsCurrentRequestId.slice(0,8)}`;
  $('vs-progress-fill').style.width = '0%';
  document.querySelectorAll('#vs-progress-list li').forEach(li => {
    li.classList.remove('active', 'done');
  });
  if (vsPollHandle) clearInterval(vsPollHandle);
  let ticks = 0;
  vsPollHandle = setInterval(async () => {
    ticks++;
    try {
      const r = await fetch(`/api/v2/video-requests/${vsCurrentRequestId}/status`,
                            { headers: authHeaders() });
      const j = await r.json();
      vsApplyProgress(j);
      if (j.status === 'succeeded') {
        clearInterval(vsPollHandle); vsPollHandle = null;
        await vsFetchResult();
        vsSetStep(4);
      } else if (j.status === 'failed') {
        clearInterval(vsPollHandle); vsPollHandle = null;
        $('vs-progress-status').textContent =
          'Generation failed: ' + (j.error || 'unknown');
        $('vs-progress-status').className = 'status error';
      }
    } catch (err) {
      if (ticks > 90) {  // 3 minutes
        clearInterval(vsPollHandle); vsPollHandle = null;
        $('vs-progress-status').textContent =
          'Polling timed out. Try refreshing.';
        $('vs-progress-status').className = 'status error';
      }
    }
  }, 2000);
}

function vsApplyProgress(j) {
  $('vs-progress-fill').style.width = (j.progress || 0) + '%';
  $('vs-progress-meta').textContent =
    `${j.progress || 0}% · ${j.current_step || 'queued'}`;
  const items = document.querySelectorAll('#vs-progress-list li');
  let foundActive = false;
  items.forEach(li => {
    const s = li.dataset.step;
    li.classList.remove('active', 'done');
    if (s === j.current_step) { li.classList.add('active'); foundActive = true; }
    else if (!foundActive)    { li.classList.add('done'); }
  });
}

async function vsFetchResult() {
  const r = await fetch(`/api/v2/video-requests/${vsCurrentRequestId}/result`,
                        { headers: authHeaders() });
  const j = await r.json();
  vsCurrentResult = j;
  $('vs-result-title').textContent = 'Your video is ready';
  const p = j.profile || {};
  $('vs-result-meta').textContent =
    `${p.video_mode || 'video'} · ${p.language_code || ''} · ${p.duration_seconds || '~'}s · ${p.output_format || '16:9'}` +
    (p.sensitive_domain ? ` · ⚠ ${p.sensitive_domain} disclaimer included` : '');
  // Clear any old <track> tags before re-binding source + subtitles
  const player = $('vs-player');
  player.querySelectorAll('track').forEach(t => t.remove());
  player.src = j.video_url;
  if (j.subtitle_vtt_url || j.subtitle_url) {
    // PRD §15 Screen 5 "subtitles toggle" — exposed via the native
    // <video> controls. Source = the .vtt sidecar (HTML5 spec format),
    // falls back to .srt for older endpoints.
    const track = document.createElement('track');
    track.kind = 'subtitles';
    track.label = (p.language_code || 'en').toUpperCase();
    track.srclang = p.language_code || 'en';
    track.src = j.subtitle_vtt_url || j.subtitle_url;
    track.default = true;
    player.appendChild(track);
  }
}

// Actions (PRD §13.5)
async function vsRegen(action, extra = {}) {
  if (!vsCurrentRequestId) return;
  const s = $('vs-progress-status'); s.textContent = ''; s.className = 'status';
  try {
    const fd = new FormData();
    fd.set('change', action);
    for (const [k, v] of Object.entries(extra)) fd.set(k, v);
    const r = await fetch(`/api/v2/video-requests/${vsCurrentRequestId}/regenerate`,
      { method:'POST', headers:authHeaders(), body:fd });
    if (!r.ok && r.status !== 202) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const j = await r.json();
    vsCurrentRequestId = j.video_request_id;
    vsSetStep(3);
    vsStartPolling(j);
  } catch (err) {
    alert('Regenerate failed: ' + err.message);
  }
}
$('vs-act-easier').addEventListener('click',   () => vsRegen('make_easier'));
$('vs-act-advanced').addEventListener('click', () => vsRegen('make_advanced'));
$('vs-act-lang').addEventListener('click', () => {
  const lang = prompt('Change to which language? (en, hi, mr, ta, te, bn, gu, kn, ml, pa)', 'hi');
  if (lang) vsRegen('change_language', { language: lang });
});
$('vs-act-short').addEventListener('click', () => vsRegen('create_short'));
$('vs-act-exam').addEventListener('click', () => vsRegen('exam_focused'));
$('vs-act-download').addEventListener('click', () => {
  if (!vsCurrentResult) return;
  const a = document.createElement('a');
  a.href = vsCurrentResult.video_url; a.download = 'lesson.mp4'; a.click();
});
$('vs-act-audio').addEventListener('click', () => {
  if (!vsCurrentResult?.audio_url) return;
  const a = document.createElement('a');
  a.href = vsCurrentResult.audio_url; a.download = 'lesson.mp3'; a.click();
});
$('vs-act-subs').addEventListener('click', () => {
  if (!vsCurrentResult?.subtitle_url) return;
  const a = document.createElement('a');
  a.href = vsCurrentResult.subtitle_url; a.download = 'lesson.srt'; a.click();
});
// D2: WhatsApp share — Web Share API first (native sheet on Android +
// iOS + most modern browsers), fall back to wa.me deep link.
$('vs-act-share').addEventListener('click', async () => {
  if (!vsCurrentResult?.video_url) return;
  const p = vsCurrentResult.profile || {};
  const title = 'AI Pathshala — ' + (p.video_mode || 'video');
  const url = new URL(vsCurrentResult.video_url, window.location.origin).href;
  const text = `Watch this ${p.video_mode || 'lesson'} from AI Pathshala (${p.language_code || 'en'}): ${url}`;
  try {
    if (navigator.share) {
      await navigator.share({ title, text, url });
      return;
    }
  } catch (e) {
    // user cancelled — fall through to wa.me
  }
  window.open(
    'https://wa.me/?text=' + encodeURIComponent(text),
    '_blank', 'noopener',
  );
});
$('vs-act-chat').addEventListener('click', () => {
  if (!vsCurrentResult?.lesson_id) return;
  showModule('chat');
  const el = document.getElementById('chat-lesson-id');
  if (el) el.value = vsCurrentResult.lesson_id;
});
$('vs-new').addEventListener('click', () => {
  vsCurrentRequestId = null; vsCurrentResult = null;
  $('vs-topic').value = ''; $('vs-file').value = '';
  $('vs-player').src = '';
  vsSetStep(1);
});

// ============================================================================
// SCHOOL / COACHING portal (v0.9.0 — PRD §3.6)
// ============================================================================
let schCurrentOrg = null;
let schClasses = [];

function schAuthHeaders() {
  const h = { 'Accept': 'application/json' };
  if (token) h['Authorization'] = 'Bearer ' + token;
  return h;
}

async function schBoot() {
  // Called every time the user navigates to the School tab.
  const landing  = $('sch-landing');
  const dash     = $('sch-dashboard');
  const notSigned = document.querySelector('.sch-not-signed-in');

  if (!token) {
    landing.style.display = 'block';
    dash.style.display = 'none';
    notSigned.style.display = 'block';
    $('sch-create-form').style.display = 'none';
    return;
  }
  notSigned.style.display = 'none';
  $('sch-create-form').style.display = 'block';

  try {
    const r = await fetch('/api/orgs/me', { headers: schAuthHeaders() });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    if (!data.orgs.length) {
      landing.style.display = 'block';
      dash.style.display = 'none';
      return;
    }
    landing.style.display = 'none';
    dash.style.display = 'block';
    schCurrentOrg = data.orgs[0];
    schRenderHeader();
    await schLoadStats();
    await schLoadClasses();
    await schSwitchTab('members');
  } catch (ex) {
    console.error('schBoot failed', ex);
  }
}

function schRenderHeader() {
  if (!schCurrentOrg) return;
  $('sch-org-name').textContent = schCurrentOrg.name;
  const bits = [
    schCurrentOrg.kind === 'school' ? 'School'
      : schCurrentOrg.kind === 'coaching' ? 'Coaching institute'
      : schCurrentOrg.kind === 'ngo' ? 'NGO'
      : 'Government',
    schCurrentOrg.board, schCurrentOrg.city,
    schCurrentOrg.contact_email,
  ].filter(Boolean);
  $('sch-org-meta').textContent = bits.join(' · ');
  $('sch-plan-pill').textContent = schCurrentOrg.plan_tier;
}

async function schLoadStats() {
  if (!schCurrentOrg) return;
  try {
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}`, { headers: schAuthHeaders() });
    if (!r.ok) return;
    const data = await r.json();
    const s = data.stats || {};
    $('sch-kpi-students').textContent    = s.students || 0;
    $('sch-kpi-teachers').textContent    = s.teachers || 0;
    $('sch-kpi-classes').textContent     = s.classes || 0;
    $('sch-kpi-assignments').textContent = s.assignments || 0;
    $('sch-kpi-videos').textContent      = s.videos_last_7d || 0;
  } catch (ex) { console.error(ex); }
}

async function schLoadClasses() {
  if (!schCurrentOrg) return;
  try {
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}/classes`, { headers: schAuthHeaders() });
    if (!r.ok) { schClasses = []; return; }
    const data = await r.json();
    schClasses = data.classes;
    // Populate class selectors in modals
    const opts = '<option value="">— none —</option>' + schClasses.map(c =>
      `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
    $('sch-member-class').innerHTML = opts;
    $('sch-assignment-class').innerHTML = schClasses.map(c =>
      `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
  } catch (ex) { console.error(ex); }
}

function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, m => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]
  ));
}

async function schSwitchTab(name) {
  document.querySelectorAll('.sch-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.schTab === name));
  document.querySelectorAll('.sch-tab-panel').forEach(p =>
    p.style.display = (p.id === 'sch-panel-' + name) ? 'block' : 'none');
  if (name === 'members')     await schRenderMembers();
  if (name === 'classes')     await schRenderClassGrid();
  if (name === 'assignments') await schRenderAssignments();
  if (name === 'attendance')  await schRenderAttendance();
  if (name === 'timetable')   await schRenderTimetable();
  if (name === 'exams')       await schRenderExams();
  if (name === 'fees')        await schRenderFees();
}

async function schRenderMembers() {
  if (!schCurrentOrg) return;
  const tbody = document.querySelector('#sch-members-table tbody');
  tbody.innerHTML = `<tr><td colspan="5" style="color:var(--muted); padding:14px;">Loading…</td></tr>`;
  try {
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}/members`, { headers: schAuthHeaders() });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    if (!data.members.length) {
      tbody.innerHTML = `<tr><td colspan="5" style="color:var(--muted); padding:20px; text-align:center;">
        No members yet. Use <strong>Upload CSV</strong> or <strong>+ Add one</strong> to invite students and teachers.
      </td></tr>`;
      return;
    }
    const classNameById = Object.fromEntries(schClasses.map(c => [c.id, c.name]));
    tbody.innerHTML = data.members.map(m => `
      <tr>
        <td>${escapeHtml(m.display_name) || '<span style="color:var(--muted);">—</span>'}</td>
        <td><code>${escapeHtml(m.invited_email || m.user_id || '—')}</code></td>
        <td><span class="sch-role-pill ${m.role}">${m.role}</span></td>
        <td>${escapeHtml(classNameById[m.class_id] || '—')}</td>
        <td style="color:var(--muted); font-size:12px;">${new Date(m.joined_at * 1000).toLocaleDateString()}</td>
      </tr>
    `).join('');
  } catch (ex) {
    tbody.innerHTML = `<tr><td colspan="5" style="color:var(--err);">Load failed: ${escapeHtml(ex.message)}</td></tr>`;
  }
}

async function schRenderClassGrid() {
  await schLoadClasses();
  const grid = $('sch-class-grid');
  if (!schClasses.length) {
    grid.innerHTML = `<div style="grid-column:1/-1; color:var(--muted); text-align:center; padding:24px;">
      No class groups yet. Click <strong>+ New class</strong> to create your first one.
    </div>`;
    return;
  }
  grid.innerHTML = schClasses.map(c => `
    <div class="sch-class-card">
      <h5>${escapeHtml(c.name)}</h5>
      <div class="grade">${escapeHtml(c.grade_level || '—')}${c.section ? ' · sec ' + escapeHtml(c.section) : ''}</div>
    </div>
  `).join('');
}

async function schRenderAssignments() {
  if (!schCurrentOrg) return;
  const wrap = $('sch-assignments-list');
  wrap.innerHTML = '<div style="color:var(--muted); padding:12px;">Loading…</div>';
  try {
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}/assignments`, { headers: schAuthHeaders() });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    if (!data.assignments.length) {
      wrap.innerHTML = `<div style="color:var(--muted); text-align:center; padding:24px;">
        No assignments yet. Click <strong>+ Create assignment</strong> to push your first video task to a class.
      </div>`;
      return;
    }
    const classNameById = Object.fromEntries(schClasses.map(c => [c.id, c.name]));
    const today = new Date().toISOString().slice(0, 10);
    wrap.innerHTML = data.assignments.map(a => {
      const dueLate = a.due_date && a.due_date < today;
      const dueChip = a.due_date
        ? `<div class="due ${dueLate ? 'overdue' : ''}">Due ${escapeHtml(a.due_date)}${dueLate ? ' · late' : ''}</div>`
        : '';
      return `
        <div class="sch-assignment-row" data-aid="${escapeHtml(a.id)}">
          <div>
            <strong>${escapeHtml(a.title)}</strong>
            <div class="meta">${escapeHtml(classNameById[a.class_id] || '—')} ·
              topic: <em>${escapeHtml(a.topic)}</em> ·
              ${escapeHtml(a.language)} · ${escapeHtml(a.level)}</div>
          </div>
          ${dueChip}
        </div>
      `;
    }).join('');
    // Wire click → open per-assignment analytics drawer
    wrap.querySelectorAll('.sch-assignment-row').forEach(row => {
      row.addEventListener('click', () => schOpenAssignmentDrawer(row.dataset.aid));
    });
  } catch (ex) {
    wrap.innerHTML = `<div style="color:var(--err);">Load failed: ${escapeHtml(ex.message)}</div>`;
  }
}


// ---- E1: Per-assignment analytics drawer ----
async function schOpenAssignmentDrawer(aid) {
  if (!schCurrentOrg || !aid) return;
  const modal = $('sch-modal-stats');
  modal.style.display = 'flex';
  const wrap = $('sch-stats-content');
  wrap.innerHTML = '<div style="color:var(--muted); padding:14px;">Loading…</div>';
  try {
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}/assignments/${aid}/stats`,
                          { headers: schAuthHeaders() });
    if (!r.ok) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || ('HTTP ' + r.status));
    }
    const s = await r.json();
    const studentRows = s.students.map(st => `
      <tr>
        <td>${escapeHtml(st.display_name || st.email || '—')}</td>
        <td><span class="sch-status-pill ${st.status}">${st.status.replace('_', ' ')}</span></td>
        <td style="text-align:right; font-variant-numeric:tabular-nums;">${st.watch_pct != null ? st.watch_pct + '%' : '—'}</td>
        <td style="text-align:right; font-variant-numeric:tabular-nums;">${st.quiz_score != null ? st.quiz_score + '/100' : '—'}</td>
      </tr>
    `).join('');
    wrap.innerHTML = `
      <div class="sch-stats-kpis">
        <div><div class="lbl">Completed</div><div class="val">${s.completed}/${s.total}</div><div class="sub">${s.completion_pct}%</div></div>
        <div><div class="lbl">In progress</div><div class="val">${s.in_progress}</div></div>
        <div><div class="lbl">Not started</div><div class="val">${s.not_started}</div></div>
        <div><div class="lbl">Avg quiz</div><div class="val">${s.avg_quiz_score != null ? s.avg_quiz_score + '%' : '—'}</div></div>
      </div>
      <table class="sch-table">
        <thead><tr><th>Student</th><th>Status</th><th style="text-align:right;">Watched</th><th style="text-align:right;">Quiz</th></tr></thead>
        <tbody>${studentRows || '<tr><td colspan="4" style="color:var(--muted); padding:18px; text-align:center;">No students in this class yet.</td></tr>'}</tbody>
      </table>
    `;
  } catch (ex) {
    wrap.innerHTML = `<div style="color:var(--err); padding:14px;">Load failed: ${escapeHtml(ex.message)}</div>`;
  }
}

// ---- Modals ----
function schOpenModal(id)  { $(id).style.display = 'flex'; }
function schCloseModal(id) { $(id).style.display = 'none'; }
document.querySelectorAll('[data-close-sch]').forEach(b => {
  b.addEventListener('click', () => b.closest('.sch-modal').style.display = 'none');
});

// ---- Create org ----
$('sch-create-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const status = $('sch-create-status');
  status.textContent = 'Creating…';
  try {
    const fd = new FormData(e.target);
    const r = await fetch('/api/orgs', { method:'POST', headers: schAuthHeaders(), body: fd });
    if (!r.ok) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || 'HTTP ' + r.status);
    }
    status.textContent = '';
    await schBoot();
  } catch (ex) {
    status.textContent = 'Error: ' + ex.message;
    status.className = 'status error';
  }
});

// ---- Sign-in shortcut ----
$('sch-open-signin').addEventListener('click', () => {
  if (typeof openAuthModal === 'function') openAuthModal('login');
});

// ---- Tab clicks ----
document.querySelectorAll('.sch-tab').forEach(t => {
  t.addEventListener('click', () => schSwitchTab(t.dataset.schTab));
});

// ---- Add member ----
$('sch-add-member').addEventListener('click', () => schOpenModal('sch-modal-member'));
$('sch-member-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!schCurrentOrg) return;
  const status = $('sch-member-status'); status.textContent = '';
  try {
    const fd = new FormData(e.target);
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}/members`,
                          { method:'POST', headers: schAuthHeaders(), body: fd });
    if (!r.ok) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || 'HTTP ' + r.status);
    }
    schCloseModal('sch-modal-member');
    e.target.reset();
    await schLoadStats();
    await schRenderMembers();
  } catch (ex) {
    status.textContent = 'Error: ' + ex.message;
    status.className = 'status error';
  }
});

// ---- Add class ----
$('sch-add-class').addEventListener('click', () => schOpenModal('sch-modal-class'));
$('sch-class-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!schCurrentOrg) return;
  const status = $('sch-class-status'); status.textContent = '';
  try {
    const fd = new FormData(e.target);
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}/classes`,
                          { method:'POST', headers: schAuthHeaders(), body: fd });
    if (!r.ok) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || 'HTTP ' + r.status);
    }
    schCloseModal('sch-modal-class');
    e.target.reset();
    await schLoadStats();
    await schLoadClasses();
    await schRenderClassGrid();
  } catch (ex) {
    status.textContent = 'Error: ' + ex.message;
    status.className = 'status error';
  }
});

// ---- Create assignment ----
$('sch-add-assignment').addEventListener('click', async () => {
  await schLoadClasses();
  if (!schClasses.length) {
    alert('Create a class first — assignments belong to a class group.');
    return;
  }
  schOpenModal('sch-modal-assignment');
});
$('sch-assignment-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!schCurrentOrg) return;
  const status = $('sch-assignment-status'); status.textContent = '';
  try {
    const fd = new FormData(e.target);
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}/assignments`,
                          { method:'POST', headers: schAuthHeaders(), body: fd });
    if (!r.ok) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || 'HTTP ' + r.status);
    }
    schCloseModal('sch-modal-assignment');
    e.target.reset();
    await schLoadStats();
    await schRenderAssignments();
  } catch (ex) {
    status.textContent = 'Error: ' + ex.message;
    status.className = 'status error';
  }
});

// ---- CSV roster upload ----
$('sch-roster-csv').addEventListener('change', async (e) => {
  if (!schCurrentOrg) return;
  const file = e.target.files[0];
  if (!file) return;
  const status = $('sch-members-status');
  status.textContent = 'Uploading…';
  try {
    const fd = new FormData();
    fd.set('csv', file);
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}/roster`,
                          { method:'POST', headers: schAuthHeaders(), body: fd });
    if (!r.ok) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || 'HTTP ' + r.status);
    }
    const res = await r.json();
    let msg = `Imported ${res.added}. ${res.duplicates} duplicate${res.duplicates===1?'':'s'} skipped.`;
    if (res.skipped && res.skipped.length) {
      msg += ` ${res.skipped.length} row${res.skipped.length===1?'':'s'} rejected.`;
    }
    status.textContent = msg;
    status.className = 'status ok';
    e.target.value = ''; // reset so re-upload of same file fires change
    await schLoadStats();
    await schLoadClasses();
    await schRenderMembers();
  } catch (ex) {
    status.textContent = 'Upload failed: ' + ex.message;
    status.className = 'status error';
  }
});

// ---- E3 Attendance UI ----
async function schRenderAttendance() {
  if (!schCurrentOrg) return;
  await schLoadClasses();
  const classSel = $('sch-att-class');
  classSel.innerHTML = schClasses.map(c =>
    `<option value="${escapeHtml(c.id)}">${escapeHtml(c.name)}</option>`).join('');
  // Default the date picker to today
  if (!$('sch-att-date').value) {
    $('sch-att-date').value = new Date().toISOString().slice(0, 10);
  }
  await schLoadAttendanceRoster();
  classSel.onchange = schLoadAttendanceRoster;
  $('sch-att-date').onchange = schLoadAttendanceRoster;
  $('sch-att-save').onclick = schSaveAttendance;
}

let _attEdits = {};  // user_id -> status (the unsaved local state)

async function schLoadAttendanceRoster() {
  if (!schCurrentOrg) return;
  const cid = $('sch-att-class').value;
  const date = $('sch-att-date').value;
  if (!cid || !date) return;
  _attEdits = {};
  const grid = $('sch-att-grid');
  grid.innerHTML = `<div style="color:var(--muted); padding:14px;">Loading…</div>`;
  try {
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}/classes/${cid}/attendance?date=${date}`,
                          { headers: schAuthHeaders() });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    if (!data.students.length) {
      grid.innerHTML = `<div style="color:var(--muted); padding:24px; text-align:center;">
        No students in this class yet. Add students via the Members tab.
      </div>`;
      return;
    }
    grid.innerHTML = data.students.map(s => {
      const cur = s.status || '';
      _attEdits[s.user_id] = cur;
      return `
        <div class="sch-att-row" data-uid="${escapeHtml(s.user_id)}">
          <div class="name">${escapeHtml(s.display_name || s.email || '—')}</div>
          <div class="pills">
            ${['present','late','absent','excused'].map(st =>
              `<button class="sch-att-pill ${st} ${cur === st ? 'active' : ''}"
                       data-status="${st}">${st}</button>`
            ).join('')}
          </div>
        </div>`;
    }).join('');
    grid.querySelectorAll('.sch-att-pill').forEach(btn => {
      btn.addEventListener('click', () => {
        const row = btn.closest('.sch-att-row');
        const uid = row.dataset.uid;
        const status = btn.dataset.status;
        row.querySelectorAll('.sch-att-pill').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        _attEdits[uid] = status;
      });
    });
  } catch (ex) {
    grid.innerHTML = `<div style="color:var(--err);">${escapeHtml(ex.message)}</div>`;
  }
}

async function schSaveAttendance() {
  if (!schCurrentOrg) return;
  const cid = $('sch-att-class').value;
  const date = $('sch-att-date').value;
  if (!cid || !date) return;
  const records = Object.entries(_attEdits)
    .filter(([uid, status]) => status)
    .map(([user_id, status]) => ({ user_id, date, status }));
  if (!records.length) {
    $('sch-att-status').textContent = 'Mark at least one student first.';
    $('sch-att-status').className = 'status error';
    return;
  }
  const status = $('sch-att-status');
  status.textContent = 'Saving…'; status.className = 'status';
  try {
    const fd = new FormData();
    fd.set('records_json', JSON.stringify(records));
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}/classes/${cid}/attendance`,
      { method:'POST', headers: schAuthHeaders(), body: fd });
    if (!r.ok && r.status !== 201) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || ('HTTP ' + r.status));
    }
    const j = await r.json();
    status.textContent = `Saved ${j.marked} entries.` +
      (j.errors.length ? ` ${j.errors.length} skipped.` : '');
    status.className = 'status ok';
  } catch (ex) {
    status.textContent = 'Save failed: ' + ex.message;
    status.className = 'status error';
  }
}

// ---- E6 Timetable UI ----
async function schRenderTimetable() {
  if (!schCurrentOrg) return;
  await schLoadClasses();
  const classSel = $('sch-tt-class');
  classSel.innerHTML = schClasses.map(c =>
    `<option value="${escapeHtml(c.id)}">${escapeHtml(c.name)}</option>`).join('');
  classSel.onchange = schLoadTimetableGrid;
  await schLoadTimetableGrid();
}

const TT_DAYS = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
async function schLoadTimetableGrid() {
  if (!schCurrentOrg) return;
  const cid = $('sch-tt-class').value;
  if (!cid) return;
  const grid = $('sch-tt-grid');
  grid.innerHTML = `<div style="color:var(--muted); padding:14px; grid-column:1/-1;">Loading…</div>`;
  try {
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}/classes/${cid}/timetable`,
                          { headers: schAuthHeaders() });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    const byDay = {};
    TT_DAYS.forEach((_, i) => byDay[i + 1] = []);
    for (const s of data.slots) {
      if (byDay[s.day_of_week]) byDay[s.day_of_week].push(s);
    }
    grid.innerHTML = TT_DAYS.map((name, i) => {
      const slots = byDay[i + 1] || [];
      return `
        <div class="sch-tt-day">
          <h5>${name}</h5>
          ${slots.length ? slots.map(s => `
            <div class="sch-tt-slot">
              <div class="time">${escapeHtml(s.start_time)}–${escapeHtml(s.end_time)}</div>
              <div class="subject">${escapeHtml(s.subject)}</div>
              ${s.room ? `<div class="room">${escapeHtml(s.room)}</div>` : ''}
            </div>
          `).join('') : '<div class="sch-tt-empty">no classes</div>'}
        </div>`;
    }).join('');
  } catch (ex) {
    grid.innerHTML = `<div style="color:var(--err); grid-column:1/-1;">${escapeHtml(ex.message)}</div>`;
  }
}

// ---- E4 Exams + S4 anti-cheat ----
let _examActive = null;       // current exam being taken (student view)
let _examAnswers = {};
let _examAttemptStartedAt = null;
let _examDeadlineAt = null;
let _examTimerHandle = null;
let _examTabBlurCount = 0;
let _examFullscreenExitCount = 0;
let _examFlags = [];

const _SAMPLE_QUESTIONS_JSON = JSON.stringify([
  {kind:"mcq", q:"What gas do plants release during photosynthesis?",
   options:{A:"Carbon dioxide", B:"Oxygen", C:"Nitrogen", D:"Hydrogen"},
   answer:"B", marks:2},
  {kind:"mcq", q:"Where does photosynthesis happen?",
   options:{A:"Roots", B:"Stem", C:"Chloroplasts", D:"Soil"},
   answer:"C", marks:2},
  {kind:"free", q:"Describe photosynthesis in 2 sentences.", marks:4},
], null, 2);

async function schRenderExams() {
  if (!schCurrentOrg) return;
  await schLoadClasses();

  // Class filter dropdown — populate once per visit
  const cf = $('sch-ex-class-filter');
  cf.innerHTML = '<option value="">All classes</option>' +
    schClasses.map(c => `<option value="${escapeHtml(c.id)}">${escapeHtml(c.name)}</option>`).join('');
  cf.onchange = schLoadExamsList;
  await schLoadExamsList();
}

async function schLoadExamsList() {
  if (!schCurrentOrg) return;
  const filter = $('sch-ex-class-filter').value;
  const wrap = $('sch-exams-list');
  wrap.innerHTML = '<div style="color:var(--muted); padding:12px;">Loading…</div>';
  try {
    const url = `/api/orgs/${schCurrentOrg.id}/exams` +
                (filter ? `?class_id=${filter}` : '');
    const r = await fetch(url, { headers: schAuthHeaders() });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    if (!data.exams.length) {
      wrap.innerHTML = `<div style="color:var(--muted); text-align:center; padding:24px;">
        No exams yet. Click <strong>+ Create exam</strong>.
      </div>`;
      return;
    }
    const classNameById = Object.fromEntries(schClasses.map(c => [c.id, c.name]));
    wrap.innerHTML = data.exams.map(e => `
      <div class="sch-exam-row" data-eid="${escapeHtml(e.id)}">
        <div style="flex:1;">
          <strong>${escapeHtml(e.title)}</strong>
          <span class="status-pill ${escapeHtml(e.status)}">${escapeHtml(e.status)}</span>
          <div class="meta">
            ${escapeHtml(classNameById[e.class_id] || '—')} ·
            ${escapeHtml(e.topic)} · ${e.duration_min}min · ${e.max_marks} marks ·
            ${e.questions.length} questions
          </div>
        </div>
        <div style="display:flex; gap:6px;">
          <button class="btn-ghost exam-take-btn" data-eid="${escapeHtml(e.id)}">▶ Take</button>
          <button class="btn-ghost exam-attempts-btn" data-eid="${escapeHtml(e.id)}">📋 Attempts</button>
        </div>
      </div>`).join('');
    wrap.querySelectorAll('.exam-take-btn').forEach(b =>
      b.addEventListener('click', () => examBegin(b.dataset.eid)));
    wrap.querySelectorAll('.exam-attempts-btn').forEach(b =>
      b.addEventListener('click', () => examShowAttempts(b.dataset.eid)));
  } catch (ex) {
    wrap.innerHTML = `<div style="color:var(--err);">${escapeHtml(ex.message)}</div>`;
  }
}

// ---- create exam modal ----
$('sch-add-exam')?.addEventListener('click', () => {
  // Populate class dropdown + sample questions placeholder
  const sel = $('sch-exam-class');
  sel.innerHTML = schClasses.map(c =>
    `<option value="${escapeHtml(c.id)}">${escapeHtml(c.name)}</option>`).join('');
  const ta = document.querySelector('#sch-exam-form textarea[name="questions_json"]');
  if (!ta.value) ta.value = _SAMPLE_QUESTIONS_JSON;
  schOpenModal('sch-modal-exam-create');
});
$('sch-exam-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!schCurrentOrg) return;
  const status = $('sch-exam-status'); status.textContent = '';
  try {
    const fd = new FormData(e.target);
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}/exams`,
      { method:'POST', headers: schAuthHeaders(), body: fd });
    if (!r.ok && r.status !== 201) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || ('HTTP ' + r.status));
    }
    schCloseModal('sch-modal-exam-create');
    e.target.reset();
    await schLoadExamsList();
  } catch (ex) {
    status.textContent = 'Error: ' + ex.message;
    status.className = 'status error';
  }
});

// ---- Take exam (student) — full anti-cheat instrumentation ----
async function examBegin(eid) {
  if (!schCurrentOrg) return;
  try {
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}/exams/${eid}/begin`,
      { method:'POST', headers: schAuthHeaders() });
    if (!r.ok) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || ('HTTP ' + r.status));
    }
    const data = await r.json();
    _examActive = data.exam;
    _examAnswers = data.attempt.answers || {};
    _examAttemptStartedAt = data.attempt.started_at;
    _examDeadlineAt = data.deadline_at;
    _examTabBlurCount = data.attempt.tab_blur_count || 0;
    _examFullscreenExitCount = data.attempt.fullscreen_exit_count || 0;
    _examFlags = data.attempt.flags || [];

    $('exam-take-title').textContent = data.exam.title;
    examRenderQuestions();
    schOpenModal('sch-modal-exam-take');
    examStartTimer();
    examInstallAntiCheat();
  } catch (ex) {
    alert('Could not begin exam: ' + ex.message);
  }
}

function examRenderQuestions() {
  const wrap = $('exam-questions');
  wrap.innerHTML = _examActive.questions.map((q, i) => {
    const opts = q.options || {};
    const optionList = ['A','B','C','D'].filter(L => opts[L] !== undefined).map(L => `
      <label class="opt ${_examAnswers[i] === L ? 'selected' : ''}" data-i="${i}">
        <input type="radio" name="q_${i}" value="${L}" ${_examAnswers[i] === L ? 'checked' : ''}>
        <span class="letter">${L}.</span><span>${escapeHtml(opts[L])}</span>
      </label>`).join('');
    return `
      <div class="exam-q">
        <div class="q-num">Question ${i + 1} · ${q.marks || 1} marks · ${q.kind === 'free' ? 'Free response' : 'Multiple choice'}</div>
        <div class="q-text">${escapeHtml(q.q)}</div>
        ${q.kind === 'free'
          ? `<textarea data-i="${i}" placeholder="Type your answer…">${escapeHtml(_examAnswers[i] || '')}</textarea>`
          : optionList}
      </div>`;
  }).join('');
  wrap.querySelectorAll('input[type="radio"]').forEach(inp => {
    inp.addEventListener('change', () => {
      const i = inp.closest('.opt').dataset.i;
      _examAnswers[i] = inp.value;
      inp.closest('.exam-q').querySelectorAll('.opt').forEach(o => o.classList.remove('selected'));
      inp.closest('.opt').classList.add('selected');
    });
  });
  wrap.querySelectorAll('textarea[data-i]').forEach(ta => {
    ta.addEventListener('input', () => { _examAnswers[ta.dataset.i] = ta.value; });
  });
}

function examStartTimer() {
  if (_examTimerHandle) clearInterval(_examTimerHandle);
  _examTimerHandle = setInterval(() => {
    const remain = Math.max(0, Math.round(_examDeadlineAt - Date.now() / 1000));
    const m = Math.floor(remain / 60);
    const s = remain % 60;
    const t = $('exam-timer');
    t.textContent = `${m}:${String(s).padStart(2, '0')}`;
    t.classList.toggle('warning', remain < 60);
    if (remain <= 0) {
      clearInterval(_examTimerHandle);
      examSubmit({ auto: true });
    }
  }, 500);
}

function examInstallAntiCheat() {
  // S4: visibilitychange + blur fire when the student switches tabs
  // or windows. We count + log; the teacher dashboard surfaces it.
  const onVis = () => {
    if (document.hidden && _examActive) {
      _examTabBlurCount++;
      _examFlags.push({ t: 'tab_blur', at: Date.now() / 1000 });
      $('exam-anticheat-warning').style.display = 'block';
      setTimeout(() => { $('exam-anticheat-warning').style.display = 'none'; }, 4000);
    }
  };
  document.addEventListener('visibilitychange', onVis);
  // Cleanup function — store on _examActive so submit can call it
  _examActive._cleanup = () => {
    document.removeEventListener('visibilitychange', onVis);
  };
}

$('exam-submit-btn')?.addEventListener('click', () => examSubmit({ auto: false }));

async function examSubmit({ auto }) {
  if (!_examActive || !schCurrentOrg) return;
  const status = $('exam-take-status');
  status.textContent = auto ? 'Time up — submitting…' : 'Submitting…';
  status.className = 'status';
  try {
    const fd = new FormData();
    fd.set('answers_json', JSON.stringify(_examAnswers));
    fd.set('tab_blur_count', _examTabBlurCount);
    fd.set('fullscreen_exit_count', _examFullscreenExitCount);
    fd.set('flags_json', JSON.stringify(_examFlags));
    const r = await fetch(
      `/api/orgs/${schCurrentOrg.id}/exams/${_examActive.id}/submit`,
      { method:'POST', headers: schAuthHeaders(), body: fd },
    );
    if (!r.ok) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || ('HTTP ' + r.status));
    }
    const att = await r.json();
    const free = _examActive.questions.filter(q => q.kind === 'free').length;
    const msg = free
      ? `Submitted. You scored ${att.auto_score} on MCQs; the ${free} free-response question${free === 1 ? '' : 's'} await${free === 1 ? 's' : ''} teacher grading.`
      : `Submitted. You scored ${att.auto_score}/${_examActive.max_marks}.`;
    status.textContent = msg;
    status.className = 'status ok';
    if (_examTimerHandle) { clearInterval(_examTimerHandle); _examTimerHandle = null; }
    if (_examActive._cleanup) _examActive._cleanup();
    setTimeout(() => {
      schCloseModal('sch-modal-exam-take');
      _examActive = null;
      schLoadExamsList();
    }, 2500);
  } catch (ex) {
    status.textContent = 'Submit failed: ' + ex.message;
    status.className = 'status error';
  }
}

// ---- Attempts review (teacher) ----
async function examShowAttempts(eid) {
  if (!schCurrentOrg) return;
  schOpenModal('sch-modal-exam-attempts');
  const wrap = $('exam-attempts-content');
  wrap.innerHTML = '<div style="color:var(--muted); padding:14px;">Loading…</div>';
  try {
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}/exams/${eid}/attempts`,
      { headers: schAuthHeaders() });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    $('exam-attempts-title').textContent = `Exam attempts (${data.attempts.length})`;
    if (!data.attempts.length) {
      wrap.innerHTML = `<div style="color:var(--muted); padding:14px;">No attempts yet.</div>`;
      return;
    }
    wrap.innerHTML = `
      <div class="exam-attempt-row" style="font-weight:700; color:var(--muted); border:0; background:transparent;">
        <div>Student</div><div>Submitted</div><div>Auto</div><div>Manual</div><div>Flags</div>
      </div>` + data.attempts.map(a => {
      const flagged = (a.tab_blur_count || 0) >= 3;
      const sub = a.submitted_at
        ? new Date(a.submitted_at * 1000).toLocaleString()
        : '<span style="color:var(--muted);">in progress</span>';
      return `
        <div class="exam-attempt-row ${flagged ? 'flagged' : ''}">
          <div><code>${escapeHtml(a.user_id.slice(0, 12))}…</code></div>
          <div>${sub}</div>
          <div><strong>${a.auto_score != null ? a.auto_score : '—'}</strong></div>
          <div>${a.manual_score != null ? a.manual_score : '<em>ungraded</em>'}</div>
          <div>${a.tab_blur_count
              ? `<span class="exam-flag-pill">${a.tab_blur_count} tab-blur${a.tab_blur_count === 1 ? '' : 's'}</span>`
              : '<span style="color:#16A34A;">✓ clean</span>'}</div>
        </div>`;
    }).join('');
  } catch (ex) {
    wrap.innerHTML = `<div style="color:var(--err);">${escapeHtml(ex.message)}</div>`;
  }
}

// ---- E5 Fees + invoicing ----
let _feesRzpConfigured = null;

async function schRenderFees() {
  if (!schCurrentOrg) return;
  await schLoadClasses();
  // Hydrate the "applies to" dropdown options
  const sel = document.getElementById('sch-fee-applies');
  if (sel) {
    sel.innerHTML = '<option value="all">All students in the org</option>' +
      schClasses.map(c =>
        `<option value="class:${escapeHtml(c.id)}">${escapeHtml(c.name)}</option>`).join('');
  }
  // Check Razorpay config once per session
  if (_feesRzpConfigured === null) {
    try {
      const r = await fetch('/api/fees/config');
      if (r.ok) _feesRzpConfigured = (await r.json()).razorpay_configured;
    } catch { _feesRzpConfigured = false; }
  }
  await schLoadFeesSummary();
  await schLoadFeeStructures();
  await schLoadFeeInvoices();
}

async function schLoadFeesSummary() {
  if (!schCurrentOrg) return;
  try {
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}/fees/summary`,
                          { headers: schAuthHeaders() });
    if (!r.ok) {
      $('sch-fees-summary').innerHTML = '';
      return;
    }
    const s = await r.json();
    const fmtRupees = (paise) => '₹' + Math.round((paise || 0) / 100).toLocaleString('en-IN');
    $('sch-fees-summary').innerHTML = `
      <div class="tile collected">
        <div class="lbl">Collected</div>
        <div class="val">${fmtRupees(s.collected_paise)}</div>
        <div class="sub">${(s.counts || {}).paid || 0} paid invoices</div>
      </div>
      <div class="tile pending">
        <div class="lbl">Pending</div>
        <div class="val">${fmtRupees(s.pending_paise)}</div>
        <div class="sub">${(s.counts || {}).pending || 0} invoices</div>
      </div>
      <div class="tile overdue">
        <div class="lbl">Overdue</div>
        <div class="val">${fmtRupees(s.overdue_paise)}</div>
        <div class="sub">${(s.counts || {}).overdue || 0} invoices</div>
      </div>
      <div class="tile">
        <div class="lbl">Razorpay</div>
        <div class="val" style="font-size:14px; padding-top:6px;">${_feesRzpConfigured ? '✓ wired' : '⚠ mock mode'}</div>
        <div class="sub">${_feesRzpConfigured ? 'real charges' : 'set RAZORPAY_KEY_ID'}</div>
      </div>`;
  } catch (e) {}
}

async function schLoadFeeStructures() {
  if (!schCurrentOrg) return;
  const wrap = $('sch-fees-structures');
  wrap.innerHTML = '<div style="color:var(--muted); padding:12px;">Loading…</div>';
  try {
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}/fees/structures`,
                          { headers: schAuthHeaders() });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    if (!data.structures.length) {
      wrap.innerHTML = `<div style="color:var(--muted); text-align:center; padding:18px;">
        No fee structures yet. Click <strong>+ Define fee</strong> above.
      </div>`;
      return;
    }
    const classNameById = Object.fromEntries(schClasses.map(c => [c.id, c.name]));
    wrap.innerHTML = data.structures.map(s => {
      const target = s.applies_to === 'all'
        ? 'All students'
        : 'Class: ' + escapeHtml(classNameById[s.applies_to.split(':')[1]] || '—');
      return `
        <div class="sch-fee-struct-row" data-sid="${escapeHtml(s.id)}">
          <div>
            <div class="name">${escapeHtml(s.name)}</div>
            <div class="meta">${target} ${s.due_date ? '· due ' + escapeHtml(s.due_date) : ''}</div>
          </div>
          <div class="amount">₹${Math.round(s.amount_paise / 100).toLocaleString('en-IN')}</div>
          <div class="meta">${escapeHtml(s.currency || 'INR')}</div>
          <div></div>
          <button class="btn-ghost" data-sid="${escapeHtml(s.id)}" data-action="generate">
            Generate invoices
          </button>
        </div>`;
    }).join('');
    wrap.querySelectorAll('button[data-action="generate"]').forEach(b =>
      b.addEventListener('click', () => schGenerateInvoices(b.dataset.sid)));
  } catch (ex) {
    wrap.innerHTML = `<div style="color:var(--err);">${escapeHtml(ex.message)}</div>`;
  }
}

async function schGenerateInvoices(sid) {
  if (!schCurrentOrg) return;
  try {
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}/fees/structures/${sid}/generate`,
      { method:'POST', headers: schAuthHeaders() });
    if (!r.ok && r.status !== 201) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || ('HTTP ' + r.status));
    }
    const j = await r.json();
    alert(`Generated ${j.created} invoices. ${j.skipped_already_invoiced} students already had one.`);
    await schLoadFeesSummary();
    await schLoadFeeInvoices();
  } catch (ex) {
    alert('Generate failed: ' + ex.message);
  }
}

async function schLoadFeeInvoices() {
  if (!schCurrentOrg) return;
  const wrap = $('sch-fees-invoices');
  const filter = $('sch-fees-status-filter')?.value || '';
  wrap.innerHTML = '<div style="color:var(--muted); padding:12px;">Loading…</div>';
  try {
    const url = `/api/orgs/${schCurrentOrg.id}/fees/invoices` +
                (filter ? `?status=${filter}` : '');
    const r = await fetch(url, { headers: schAuthHeaders() });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    if (!data.invoices.length) {
      wrap.innerHTML = `<div style="color:var(--muted); text-align:center; padding:18px;">
        No invoices yet. Generate from a fee structure above.
      </div>`;
      return;
    }
    wrap.innerHTML = data.invoices.map(inv => `
      <div class="sch-invoice-row">
        <div><code>${escapeHtml(inv.user_id.slice(0, 14))}…</code></div>
        <div class="amount">₹${Math.round(inv.amount_paise / 100).toLocaleString('en-IN')}</div>
        <div><span class="status-pill ${inv.status}">${inv.status}</span></div>
        <div>${inv.due_date ? '<span style="color:var(--muted);">due ' + escapeHtml(inv.due_date) + '</span>' : ''}</div>
        <div>${inv.status === 'pending' ? `<button class="btn-ghost" data-iid="${escapeHtml(inv.id)}" data-action="pay">Pay</button>` : ''}</div>
      </div>`).join('');
    wrap.querySelectorAll('button[data-action="pay"]').forEach(b =>
      b.addEventListener('click', () => schPayInvoice(b.dataset.iid)));
  } catch (ex) {
    wrap.innerHTML = `<div style="color:var(--err);">${escapeHtml(ex.message)}</div>`;
  }
}

document.getElementById('sch-fees-status-filter')?.addEventListener('change', schLoadFeeInvoices);

async function schPayInvoice(iid) {
  if (!schCurrentOrg) return;
  try {
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}/fees/invoices/${iid}/pay`,
      { method:'POST', headers: schAuthHeaders() });
    if (!r.ok) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || ('HTTP ' + r.status));
    }
    const j = await r.json();
    if (j.already_paid) {
      alert('Already paid.'); return;
    }
    const order = j.razorpay_order;
    if (order.mock) {
      // Mock flow — auto-confirm because dev mode has no real Razorpay
      const fd = new FormData();
      fd.set('razorpay_payment_id', 'pay_mock_' + order.id.split('_').pop());
      fd.set('razorpay_order_id', order.id);
      fd.set('razorpay_signature', 'mock_sig');
      const c = await fetch(`/api/orgs/${schCurrentOrg.id}/fees/invoices/${iid}/confirm`,
        { method:'POST', headers: schAuthHeaders(), body: fd });
      if (!c.ok) throw new Error('confirm HTTP ' + c.status);
      alert('✓ Mock payment confirmed. (In production, Razorpay Checkout would open here.)');
      await schLoadFeesSummary();
      await schLoadFeeInvoices();
      return;
    }
    // Real Razorpay path — open Checkout (requires the SDK script
    // to be loaded; v0.16.1 will inject it lazily here).
    alert('Razorpay Checkout SDK integration lands in v0.16.1. '
        + 'For now, the server returned order ' + order.id);
  } catch (ex) {
    alert('Payment failed: ' + ex.message);
  }
}

// ---- Create fee modal ----
$('sch-add-fee')?.addEventListener('click', () => schOpenModal('sch-modal-fee-create'));
$('sch-fee-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!schCurrentOrg) return;
  const status = $('sch-fee-status'); status.textContent = '';
  try {
    const fd = new FormData(e.target);
    // Server takes paise; the form takes rupees for human-friendliness.
    const rupees = parseFloat(fd.get('amount_rupees') || '0');
    if (!(rupees > 0)) { throw new Error('amount must be > 0'); }
    fd.delete('amount_rupees');
    fd.set('amount_paise', Math.round(rupees * 100));
    const r = await fetch(`/api/orgs/${schCurrentOrg.id}/fees/structures`,
      { method:'POST', headers: schAuthHeaders(), body: fd });
    if (!r.ok && r.status !== 201) {
      const j = await r.json().catch(()=>({}));
      throw new Error(j.detail || ('HTTP ' + r.status));
    }
    schCloseModal('sch-modal-fee-create');
    e.target.reset();
    await schLoadFeeStructures();
    await schLoadFeesSummary();
  } catch (ex) {
    status.textContent = 'Error: ' + ex.message;
    status.className = 'status error';
  }
});

// schBoot() is called automatically by showModule('school') — the
// hook lives in the central nav function so direct navigation
// (programmatic, deep-link, etc.) gets the same behaviour as a
// sidebar click.
</script>
</body>
</html>
"""

@app.get("/", response_model=None)
def root(accept: str | None = Header(default=None)):
    """Serve HTML when a browser asks, JSON when an API client asks.

    `Accept: text/html` is what browsers send; curl / Postman / the
    test client send `*/*` or nothing — those get JSON so existing
    automation keeps working.

    v3.18: browsers get the new goal-led home UI (per review §26 +
    HTML mockup) which fetches /api/navigation/manifest +
    /api/home/me/dashboard on load. The old _INDEX_HTML is still
    available at /ui-legacy for anyone who depends on it."""
    if accept and "text/html" in accept and "application/json" not in accept:
        from . import home_ui as _home_ui
        return HTMLResponse(_home_ui.get_home_html())
    routes_out: list[str] = []
    for r in app.routes:
        methods = getattr(r, "methods", None)
        path = getattr(r, "path", None)
        if not methods or not path:
            continue
        verbs = sorted(m for m in methods if m != "HEAD")
        if not verbs:
            continue
        if path in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"):
            continue
        for v in verbs:
            routes_out.append(f"{v} {path}")
    routes_out.sort()
    return JSONResponse(
        {
            "name": "AI Pathshala",
            "version": "3.19.0",
            "endpoints": routes_out,
            "interactive_docs": "/docs",
            "openapi": "/openapi.json",
            "web_ui": "/ui",
        }
    )


@app.get("/ui", response_class=HTMLResponse)
def ui() -> HTMLResponse:
    """Direct link to the goal-led home UI — useful when an API
    client wants to reach the browser UI explicitly without playing
    accept-header games."""
    from . import home_ui as _home_ui
    return HTMLResponse(_home_ui.get_home_html())


@app.get("/home", response_class=HTMLResponse)
def home_page() -> HTMLResponse:
    """Alias — explicit /home route per the mockup."""
    from . import home_ui as _home_ui
    return HTMLResponse(_home_ui.get_home_html())


# P2 — India-first SEO. Per-language landing pages so Google can index
# /home/hi, /home/ta, etc. and serve them for vernacular searches.
# Each variant carries hreflang tags pointing at every other variant so
# Google clusters them correctly.
_SEO_LOCALES = {
    "hi": ("hi-IN", "हिन्दी"),
    "ta": ("ta-IN", "தமிழ்"),
    "te": ("te-IN", "తెలుగు"),
    "kn": ("kn-IN", "ಕನ್ನಡ"),
    "ml": ("ml-IN", "മലയാളം"),
    "mr": ("mr-IN", "मराठी"),
    "bn": ("bn-IN", "বাংলা"),
    "gu": ("gu-IN", "ગુજરાતી"),
    "pa": ("pa-IN", "ਪੰਜਾਬੀ"),
}


@app.get("/home/{lang}", response_class=HTMLResponse, include_in_schema=False)
def home_page_localized(lang: str) -> HTMLResponse:
    """Localized landing variant. The HTML is the same SPA — the JS
    `padhai_lang` localStorage key gets pre-seeded by an inline script
    so the language switcher and any localized strings render immediately.

    Hreflang tags are emitted in the <head> so search engines cluster all
    locale variants as alternates of each other (avoids duplicate-content
    penalties + serves the right locale to vernacular searchers).
    """
    if lang not in _SEO_LOCALES and lang != "en":
        raise HTTPException(404, "unsupported locale")
    from . import home_ui as _home_ui
    html = _home_ui.get_home_html()
    # Inject hreflang + locale pre-seed before </head>
    hreflangs = [
        '<link rel="alternate" hreflang="en-IN" href="https://aipadhai.app/home">',
    ]
    for code, (iso, _name) in _SEO_LOCALES.items():
        hreflangs.append(
            f'<link rel="alternate" hreflang="{iso}" href="https://aipadhai.app/home/{code}">'
        )
    hreflangs.append(
        '<link rel="alternate" hreflang="x-default" href="https://aipadhai.app/home">'
    )
    # x-default + html lang attribute swap so screen readers and Chrome
    # i18n tools both pick up the locale correctly.
    iso_lang = _SEO_LOCALES.get(lang, ("en-IN", "English"))[0]
    seed_script = (
        f'<script>try{{localStorage.setItem("padhai_lang","{lang}")}}'
        f'catch(_){{}}</script>'
    )
    html = html.replace(
        '<html lang="en">',
        f'<html lang="{iso_lang}">',
    ).replace(
        "</head>",
        "\n".join(hreflangs) + "\n" + seed_script + "\n</head>",
    )
    return HTMLResponse(html)



_TERMS_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Terms of Service — PadhaiApp</title>
<style>
  body{max-width:780px;margin:40px auto;padding:0 20px;
       font-family:Inter,Segoe UI,Arial,sans-serif;color:#111827;line-height:1.7}
  h1{font-size:28px;margin-bottom:4px}
  h2{font-size:18px;margin-top:32px}
  .meta{color:#667085;font-size:13px;margin-bottom:32px}
  a{color:#1565d8}
  footer{margin-top:48px;padding-top:16px;border-top:1px solid #e5e7eb;
         color:#667085;font-size:13px}
</style>
</head>
<body>
<h1>Terms of Service</h1>
<p class="meta">Effective date: 1 June 2026 &nbsp;|&nbsp; Last updated: 1 June 2026</p>

<h2>1. Acceptance</h2>
<p>By creating an account or using PadhaiApp ("Service"), you agree to these Terms. If you do not
agree, do not use the Service. Users under 18 must have a parent or guardian review and accept
these Terms on their behalf.</p>

<h2>2. Service Description</h2>
<p>PadhaiApp provides AI-generated video lessons, adaptive practice tests, flashcards, and tutoring
tools for Indian students. Features vary by subscription tier (Free M1 through Enterprise M4e).</p>

<h2>3. Account Registration</h2>
<p>You must provide accurate information. You are responsible for keeping your password confidential.
You must not share your account. You must be at least 13 years old, or provide verifiable parental
consent (as required by the Digital Personal Data Protection Act 2023).</p>

<h2>4. Acceptable Use</h2>
<p>You agree not to: (a) upload content you do not have rights to; (b) attempt to reverse-engineer
the AI models; (c) use the Service to generate misleading or harmful content; (d) circumvent
subscription limits; (e) scrape, crawl, or bulk-download content.</p>

<h2>5. Intellectual Property</h2>
<p>You retain ownership of content you upload. By uploading, you grant PadhaiApp a non-exclusive,
royalty-free licence to process it solely to provide the Service. AI-generated lesson videos are
owned by PadhaiApp; users receive a limited licence to use them for personal study.</p>

<h2>6. Subscription and Payments</h2>
<p>Paid plans are billed in advance. Prices are in Indian Rupees (INR) and include applicable taxes.
Refunds are available within 7 days of purchase for unused credits. We reserve the right to change
prices with 30 days' notice.</p>

<h2>7. Disclaimer of Warranties</h2>
<p>The Service is provided "as is". We do not warrant that AI-generated content is accurate,
complete, or suitable for any examination. Always verify important information with authoritative
sources.</p>

<h2>8. Limitation of Liability</h2>
<p>To the extent permitted by law, PadhaiApp's liability is limited to the amount you paid in the
12 months preceding the claim. We are not liable for indirect, incidental, or consequential
damages.</p>

<h2>9. Governing Law</h2>
<p>These Terms are governed by the laws of India. Disputes are subject to the exclusive jurisdiction
of the courts of Bengaluru, Karnataka.</p>

<h2>10. Changes to Terms</h2>
<p>We may update these Terms. We will notify you by email or in-app notice at least 14 days before
material changes take effect. Continued use after the effective date constitutes acceptance.</p>

<h2>11. Contact</h2>
<p>Legal queries: <a href="mailto:legal@aipadhaiapp.com">legal@aipadhaiapp.com</a></p>

<footer>© 2026 PadhaiApp &nbsp;|&nbsp;
<a href="/terms">Terms</a> &nbsp;|&nbsp;
<a href="/privacy">Privacy</a> &nbsp;|&nbsp;
<a href="/landing">Home</a></footer>
</body>
</html>"""

_PRIVACY_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Privacy Policy — PadhaiApp</title>
<style>
  body{max-width:780px;margin:40px auto;padding:0 20px;
       font-family:Inter,Segoe UI,Arial,sans-serif;color:#111827;line-height:1.7}
  h1{font-size:28px;margin-bottom:4px}
  h2{font-size:18px;margin-top:32px}
  .meta{color:#667085;font-size:13px;margin-bottom:32px}
  a{color:#1565d8}
  footer{margin-top:48px;padding-top:16px;border-top:1px solid #e5e7eb;
         color:#667085;font-size:13px}
</style>
</head>
<body>
<h1>Privacy Policy</h1>
<p class="meta">Effective date: 1 June 2026 &nbsp;|&nbsp; Last updated: 1 June 2026</p>

<h2>1. Data Controller</h2>
<p>PadhaiApp (operated by AI Pathshala Pvt. Ltd., Bengaluru, Karnataka, India) is the Data
Fiduciary under the Digital Personal Data Protection Act 2023 (DPDP Act). Contact:
<a href="mailto:privacy@aipadhaiapp.com">privacy@aipadhaiapp.com</a></p>

<h2>2. Data We Collect</h2>
<ul>
  <li><strong>Account data:</strong> name, email, date of birth, password hash.</li>
  <li><strong>Usage data:</strong> lessons generated, exam scores, study streaks, session logs.</li>
  <li><strong>Uploaded content:</strong> textbook pages, question papers, notes.</li>
  <li><strong>Device data:</strong> IP address, browser type, OS (for security and analytics).</li>
  <li><strong>Payment data:</strong> processed by Razorpay; we store only invoice IDs and status.</li>
</ul>

<h2>3. Legal Basis for Processing (DPDP Act 2023)</h2>
<p>We process your data under: (a) consent given at registration; (b) performance of the
subscription contract; (c) compliance with legal obligations; (d) legitimate interests
(fraud prevention, service improvement).</p>

<h2>4. Children's Data (DPDP Act 2023 §9)</h2>
<p>Under the Digital Personal Data Protection Act 2023, a "child" is any person under eighteen
years of age. We do not process personal data of users under 18 without verifiable parental or
guardian consent. Users who declare a date of birth indicating they are under 18 are placed in a
restricted state until a parent or guardian provides digital consent via a verifiable email link.
We do not serve behavioural advertising to children under 18.</p>

<h2>5. How We Use Your Data</h2>
<ul>
  <li>Provide and personalise AI lessons and practice materials.</li>
  <li>Process payments and manage subscriptions.</li>
  <li>Send transactional emails (password reset, consent, invoices).</li>
  <li>Improve model accuracy (anonymised, aggregated signals only).</li>
  <li>Detect and prevent fraud and abuse.</li>
</ul>

<h2>6. Data Sharing</h2>
<p>We share data with: (a) Anthropic PBC (AI inference); (b) Razorpay (payments);
(c) Cloudflare R2 (storage); (d) Render.com (hosting). All processors are bound by
data processing agreements. We do not sell your data.</p>

<h2>7. Your Rights (DPDP Act 2023)</h2>
<ul>
  <li><strong>Access:</strong> <code>GET /api/me/data/export</code> — download your data as JSON.</li>
  <li><strong>Correction:</strong> update your profile in Settings.</li>
  <li><strong>Erasure:</strong> <code>DELETE /api/me/account</code> — deletes your account and
      anonymises personal data within 30 days.</li>
  <li><strong>Grievance:</strong> email <a href="mailto:privacy@aipadhaiapp.com">privacy@aipadhaiapp.com</a>;
      we respond within 72 hours.</li>
</ul>

<h2>8. Data Retention</h2>
<p>Account data: retained while your account is active + 90 days after deletion.
Uploaded content: deleted within 30 days of account deletion.
Aggregated analytics: retained indefinitely (no personal identifiers).</p>

<h2>9. Security</h2>
<p>Passwords are hashed with bcrypt (cost factor 12). Data in transit is encrypted via TLS 1.3.
Data at rest is encrypted at the storage layer (Cloudflare R2 AES-256). We conduct annual
security audits.</p>

<h2>10. International Transfers</h2>
<p>AI inference is processed by Anthropic (USA). We rely on standard contractual clauses for
transfers outside India in accordance with DPDP Act rules.</p>

<h2>11. Cookies</h2>
<p>We use only strictly necessary session cookies (HttpOnly, SameSite=Lax). No advertising or
tracking cookies are set.</p>

<h2>12. Changes</h2>
<p>Material changes will be notified by email 14 days in advance.</p>

<h2>13. Grievance Officer</h2>
<p>Name: [To be appointed] &nbsp;|&nbsp;
Email: <a href="mailto:privacy@aipadhaiapp.com">privacy@aipadhaiapp.com</a><br>
Response within 72 hours as required by DPDP Act §13.</p>

<footer>© 2026 PadhaiApp &nbsp;|&nbsp;
<a href="/terms">Terms</a> &nbsp;|&nbsp;
<a href="/privacy">Privacy</a> &nbsp;|&nbsp;
<a href="/landing">Home</a></footer>
</body>
</html>"""


@app.get("/terms", include_in_schema=False)
def terms_page() -> HTMLResponse:
    return HTMLResponse(_TERMS_HTML)


@app.get("/privacy", include_in_schema=False)
def privacy_page() -> HTMLResponse:
    return HTMLResponse(_PRIVACY_HTML)


@app.get("/robots.txt", include_in_schema=False)
def robots_txt() -> Response:
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin\n"
        "Disallow: /api/\n"
        "Disallow: /jobs/\n"
        "Sitemap: https://aipadhaiapp.com/sitemap.xml\n"
    )
    return Response(content=content, media_type="text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml() -> Response:
    content = '''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://aipadhaiapp.com/landing</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>
  <url><loc>https://aipadhaiapp.com/features</loc><changefreq>monthly</changefreq><priority>0.8</priority></url>
  <url><loc>https://aipadhaiapp.com/pricing</loc><changefreq>monthly</changefreq><priority>0.8</priority></url>
  <url><loc>https://aipadhaiapp.com/terms</loc><changefreq>yearly</changefreq><priority>0.3</priority></url>
  <url><loc>https://aipadhaiapp.com/privacy</loc><changefreq>yearly</changefreq><priority>0.3</priority></url>
</urlset>'''
    return Response(content=content, media_type="application/xml")


@app.get("/landing", response_class=HTMLResponse)
def landing_page() -> HTMLResponse:
    """Public landing for unauthed visitors."""
    from . import home_ui as _home_ui
    return HTMLResponse(_home_ui.get_landing_html())


@app.get("/auth/login")
def login_page_redirect() -> RedirectResponse:
    """Redirect GET /auth/login → /landing so bookmarks and direct
    navigation work; the actual login form and POST handler live there."""
    return RedirectResponse("/landing", status_code=302)


@app.get("/login")
def login_shortcut_redirect() -> RedirectResponse:
    """Convenience alias — /login → /landing."""
    return RedirectResponse("/landing", status_code=302)


@app.get("/ui-legacy", response_class=HTMLResponse)
def ui_legacy() -> HTMLResponse:
    """The pre-v3.18 dashboard. Kept so existing bookmarks /
    embedded views don't break while the new home rolls out."""
    return HTMLResponse(_INDEX_HTML)


@app.get("/features", response_class=HTMLResponse)
def features() -> HTMLResponse:
    """Feature explorer — every button opens the real live module."""
    import pathlib
    html_path = pathlib.Path(__file__).parent.parent / "PADHAIAPP_FEATURE_EXPLORER.html"
    html = html_path.read_text(encoding="utf-8") if html_path.exists() else "<h1>Feature explorer not found</h1>"
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/health")
def _git_sha() -> str:
    """Return the short git SHA of HEAD, or 'unknown' if not in a repo."""
    try:
        import subprocess
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=2,
        ).decode().strip()
    except Exception:
        return "unknown"


@app.get("/healthz")
def health() -> JSONResponse:
    """Liveness + readiness probe. Returns 200 only when the DB pool
    is reachable; 503 otherwise. Load balancers use this to drain
    instances before cycling them. Both /health and /healthz are
    supported — /healthz is the Kubernetes convention.

    Includes build provenance so QA can verify the browser is running
    the correct code: git_sha, db_backend, queue_backend."""
    checks: dict = {
        "status": "ok",
        "git_sha": _git_sha(),
        "queue_backend": "postgres" if _pg_store is not None else "sqlite",
    }
    if _pg_store is not None:
        try:
            with _pg_store.pool.connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
            checks["db"] = "postgres"
            checks["db_status"] = "ok"
        except Exception as exc:
            checks["db"] = "postgres"
            checks["db_status"] = f"error: {exc}"
            checks["status"] = "degraded"
            return JSONResponse(checks, status_code=503)
    else:
        checks["db"] = "sqlite"
        checks["db_status"] = "ok (no DATABASE_URL)"
    return JSONResponse(checks)


@app.get("/api/ai-status")
def ai_status() -> JSONResponse:
    """Returns which AI features are configured on this server.
    Safe to call without auth — exposes no secrets, just boolean flags.

    `features` reports backend availability; `routes` lists the HTTP
    endpoints the SPA can use for each. The frontend uses `routes` to
    decide which navigation tiles to render (vs which to hide / mark
    'coming soon')."""
    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_video = bool(
        os.environ.get("HEYGEN_API_KEY") or
        os.environ.get("DID_API_KEY") or
        os.environ.get("TAVUS_API_KEY")
    )
    has_live_provider = bool(
        os.environ.get("LIVEKIT_API_KEY") or os.environ.get("DAILY_API_KEY")
    )
    has_razorpay = bool(os.environ.get("RAZORPAY_KEY_ID"))
    has_sarvam_or_bhashini = bool(
        os.environ.get("SARVAM_API_KEY") or os.environ.get("BHASHINI_API_KEY")
    )
    return JSONResponse({
        "anthropic_configured": has_anthropic,
        "video_configured": has_video,
        "live_video_configured": has_live_provider,
        "razorpay_configured": has_razorpay,
        "indic_tts_configured": has_sarvam_or_bhashini,
        "features": {
            "voice_tutor": has_anthropic,
            "voice_tutor_streaming": has_anthropic,
            "live_lecture": True,                   # stub fallback works without provider
            "essay_grader": True,
            "math_vision": has_anthropic,
            "mock_interview": True,
            "adaptive_practice": True,
            "practice_tests": True,
            "ai_synthesis": has_anthropic,
            "lesson_generation": has_anthropic,
            "upload_chat": True,                    # RAG works in dev path
            "upload_flashcards": True,
            "upload_quiz": True,
            "upload_summary": True,
            "onboarding_funnel": True,
            "student_dashboard": True,
            "parent_dashboard": True,
            "teacher_dashboard": True,
            "pricing_page": True,
            "subscription_upgrades": True,          # uses mock orders when Razorpay unset
        },
        "routes": {
            "voice_tutor": {
                "start": "/api/tutor/sessions",
                "message": "/api/tutor/sessions/{sid}/message",
                "stream": "/api/tutor/sessions/{sid}/stream",
            },
            "essay_grader": {
                "rubrics": "/api/essay/rubrics",
                "submit": "/api/essay/submit",
                "list": "/api/essay/submissions",
            },
            "math_vision": {
                "submit": "/api/math/submit",
                "extract": "/api/math/{sid}/extract",
                "validate": "/api/math/{sid}/validate",
            },
            "mock_interview": {
                "start": "/api/mock/start",
                "turn": "/api/mock/{iid}/turn",
                "end": "/api/mock/{iid}/end",
                "tracks": "/api/mock/tracks",
            },
            "adaptive_practice": {
                "view_pack": "/api/adaptive/pack/{base_pack_code}",
                "rebalance": "/api/adaptive/pack/{base_pack_code}/rebalance",
                "my_packs": "/api/adaptive/packs",
            },
            "practice_tests": {
                "generate": "/api/practice/generate",
                "start": "/api/practice/{tid}/start",
                "submit": "/api/practice/{tid}/submit",
            },
            "live_lecture": {
                "upcoming": "/api/live/upcoming",
                "schedule": "/api/live/schedule",
                "join": "/api/live/{lc_id}/join",
            },
            "upload_ai": {
                "chat": "/api/uploads/{uid}/chat",
                "flashcards": "/api/uploads/{uid}/flashcards",
                "quiz": "/api/uploads/{uid}/quiz",
                "summary": "/api/uploads/{uid}/summary",
            },
            "onboarding": {
                "options": "/api/onboarding/options",
                "status": "/api/onboarding/status",
                "step": "/api/onboarding/step",
                "complete": "/api/onboarding/complete",
            },
            "dashboards": {
                "student": "/api/me/dashboard",
                "parent": "/api/parents/dashboard",
                "teacher": "/api/teacher/dashboard?org_id=...",
            },
            "pricing": {
                "plans": "/api/pricing/plans",
                "checkout": "/api/pricing/checkout",
                "verify": "/api/pricing/verify",
                "page": "/pricing",
            },
        },
        # Features that work but are degraded without the API key
        "degraded_without_ai": (
            [] if has_anthropic else
            ["essay_grader", "mock_interview", "practice_tests",
             "upload_chat", "upload_quiz", "upload_summary"]
        ),
    })


# ---- auth -----------------------------------------------------------------


def _persist_signup_dpdp(
    *, user_id: str, dob: str, parent_email: str | None, is_minor: bool,
) -> None:
    """Persist DOB/consent state to the active auth store.

    Postgres path: writes to the `users` table in the configured DB.
    SQLite path: writes to the SAME db file that SQLiteUserRepository
    used at construction (where the `users` row actually lives). The
    earlier version of this function targeted `_dpdp._db_path()`, but
    that file doesn't have a `users` table — signup crashed with
    `sqlite3.OperationalError: no such table: users` on every dev /
    CI signup. Resolving the db path via the live user repo keeps
    DOB/consent state co-located with the row it describes.
    """
    if _pg_store is not None:
        with _pg_store.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET dob = %s, parent_email = %s, "
                "account_locked = %s WHERE id = %s",
                (dob, parent_email if is_minor else None, is_minor, user_id),
            )
        return

    repo = _get_user_repo()
    sqlite_path = getattr(repo, "_db_path", None) if repo else None
    if not sqlite_path:
        # No SQLite repo wired (anonymous-only deployment). Nothing to
        # persist — the signup itself wouldn't have reached this code.
        return
    import sqlite3
    with sqlite3.connect(sqlite_path) as conn:
        conn.execute(
            "UPDATE users SET dob = ?, parent_email = ?, "
            "account_locked = ? WHERE id = ?",
            (dob, parent_email if is_minor else None,
             1 if is_minor else 0, user_id),
        )


def _verify_parent_consent(token: str, *, parent_ip: str) -> _dpdp.ConsentRecord:
    """Redeem a parent-consent token against the active auth store.

    Flow: `dpdp.verify_consent_token()` validates + deletes the token
    (returns ConsentRecord). We then unlock the user in whichever store
    is active — Postgres pool when DATABASE_URL is set, otherwise the
    SQLite user repo. The two stores live in different DBs, so the
    dpdp module deliberately doesn't try to UPDATE users itself."""
    rec = _dpdp.verify_consent_token(token, parent_ip=parent_ip)

    if _pg_store is not None:
        with _pg_store.pool.connection() as pg_conn, pg_conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET parent_consent_at = %s, "
                "parent_consent_ip = %s, account_locked = FALSE "
                "WHERE id = %s",
                (rec.consented_at, parent_ip, rec.user_id),
            )
        return rec

    repo = _get_user_repo()
    if repo is not None and hasattr(repo, "unlock_for_consent"):
        repo.unlock_for_consent(
            rec.user_id,
            parent_ip=parent_ip,
            consented_at=rec.consented_at,
        )
    return rec


@app.post("/auth/signup")
def signup(
    request: Request,
    email: str = Form(...),
    # NB: min_length removed from Form() — let `_validate_password_complexity`
    # raise 400 (not Pydantic's 422) so the API contract stays consistent.
    # The regex already enforces ≥8 chars + letter + digit.
    password: str = Form(...),
    # DPDP Act 2023 §9: DOB collected at signup. When the user is under 18
    # we require parent_email and lock the account until consent
    # comes back via /auth/parent-consent.
    dob: str | None = Form(None, description="YYYY-MM-DD"),
    parent_email: str | None = Form(None),
    # Legal: user must accept Terms of Service before account creation.
    terms_accepted: bool = Form(False),
) -> JSONResponse:
    if _get_user_repo() is None:
        raise HTTPException(503, "auth not configured — restart the server")
    if not terms_accepted:
        raise HTTPException(400, "you must accept the Terms of Service to create an account")
    if "@" not in email:
        raise HTTPException(400, "invalid email")
    _validate_password_complexity(password)
    if _get_user_repo().find_by_email(email) is not None:
        raise HTTPException(409, "email already registered")

    # DPDP gate: if DOB given AND user is under 18, parent_email is
    # required and the account is locked until consent verification.
    is_minor = bool(dob and _dpdp.is_minor(dob))
    if is_minor:
        if not parent_email or "@" not in parent_email:
            raise HTTPException(
                400,
                "users under 18 require a parent_email (DPDP Act 2023 §9)",
            )

    user = _get_user_repo().create(
        email=email,
        password_hash=hash_password(password),
        tier="M1",
        level="L3",
    )

    if dob:
        _persist_signup_dpdp(
            user_id=user.id, dob=dob,
            parent_email=parent_email, is_minor=is_minor,
        )

    response_body: dict = {
        "user_id": user.id,
        "email": user.email,
        "subscription_tier": user.subscription_tier,
        "subscription_level": user.subscription_level,
        "token": None if is_minor else issue_token(user.id),
        "account_locked": is_minor,
    }

    if is_minor:
        # Mint a consent token + queue the parent email (dev: outbox).
        # The verify URL is hostname-relative so it works both in
        # localhost dev and on Render production.
        token = _dpdp.issue_consent_token(
            user_id=user.id, parent_email=parent_email,
        )
        verify_url = str(request.url_for("parent_consent_verify")) + f"?t={token}"
        _dpdp.queue_parent_email(
            user_id=user.id, parent_email=parent_email,
            verify_url=verify_url,
        )
        response_body["consent_required"] = True
        response_body["parent_email"] = parent_email
        # Log the consent URL server-side only — never expose in the response
        # body so the minor cannot self-approve by extracting the token.
        _log.info("[signup] parental consent URL for user %s: %s", user.id, verify_url)
    response = JSONResponse(response_body)
    if response_body.get("token"):
        return _set_auth_cookie(response, response_body["token"], request)
    return response


@app.get("/auth/parent-consent", name="parent_consent_verify",
         response_class=HTMLResponse)
def parent_consent_verify(request: Request, t: str):
    """Parent clicks the link in the verification email.

    On success: the child's account is unlocked + the consent record
    written (timestamp + IP per DPDP §9). On failure: a friendly
    "this link expired" page that hints the parent to ask the school
    admin to re-send.
    """
    try:
        # Prefer X-Forwarded-For (Render terminates TLS upstream)
        ip = (request.headers.get("x-forwarded-for") or
              (request.client.host if request.client else "unknown")).split(",")[0].strip()
        rec = _verify_parent_consent(t, parent_ip=ip)
    except ValueError as e:
        return HTMLResponse(_consent_result_page(
            ok=False, message=str(e),
        ), status_code=400)
    return HTMLResponse(_consent_result_page(
        ok=True,
        message=f"Account unlocked for {_mask_email(rec.parent_email)}.",
    ))


def _mask_email(e: str) -> str:
    # Show "mo***@x.com" so the parent sees we got their email right
    # without leaking it on a shareable URL response.
    local, _, dom = e.partition("@")
    if not dom:
        return e
    keep = max(2, len(local) // 3)
    return f"{local[:keep]}{'*' * max(1, len(local) - keep)}@{dom}"


def _consent_result_page(*, ok: bool, message: str) -> str:
    bg = "#d1fae5" if ok else "#fee2e2"
    fg = "#065f46" if ok else "#991b1b"
    icon = "✓" if ok else "✕"
    title = "Consent recorded" if ok else "Consent link invalid"
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><title>{title} — AI Pathshala</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font:16px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
         color:#1F2937; background:#F5F7FA; margin:0; padding:40px 24px;
         display:flex; align-items:center; justify-content:center; min-height:90vh; }}
  .card {{ background:#fff; max-width:480px; width:100%; padding:32px;
          border-radius:16px; border:1px solid #e5e7eb; text-align:center; }}
  .icon {{ width:64px; height:64px; border-radius:99px; background:{bg};
          color:{fg}; font-size:32px; display:inline-flex; align-items:center;
          justify-content:center; margin-bottom:18px; font-weight:700; }}
  h1 {{ font-size:22px; color:#102A43; margin:0 0 10px; }}
  p {{ color:#4b5563; margin:0 0 16px; }}
  .meta {{ font-size:13px; color:#6b7280; margin-top:20px; }}
  a {{ color:#5E60CE; }}
</style>
</head><body>
<div class="card">
  <div class="icon">{icon}</div>
  <h1>{title}</h1>
  <p>{_escape_html(message)}</p>
  <p class="meta">
    {"You can close this tab. Your child can now sign in." if ok else
     "Ask your school admin to re-send the verification email."}
  </p>
  <p class="meta">
    Per the Digital Personal Data Protection Act 2023 §9, a verifiable
    parental consent is required before we process personal data of
    users under 13.
  </p>
</div>
</body></html>"""


def _escape_html(s: str) -> str:
    import html
    return html.escape(s)


_AUTH_COOKIE_NAME = "pathshala_token"


def _set_auth_cookie(
    response: JSONResponse | HTMLResponse,
    token: str,
    request: Request,
) -> JSONResponse | HTMLResponse:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    response.set_cookie(
        key=_AUTH_COOKIE_NAME,
        value=token,
        max_age=7 * 24 * 60 * 60,
        httponly=True,
        secure=scheme == "https",
        samesite="lax",
        path="/",
    )
    return response


def _delete_auth_cookie(response: JSONResponse) -> JSONResponse:
    response.delete_cookie(_AUTH_COOKIE_NAME, path="/", samesite="lax")
    return response


_PASSWORD_RE = _re.compile(
    r"^(?=.*[A-Za-z])(?=.*\d)\S{8,}$"
)

def _validate_password_complexity(password: str) -> None:
    """Require ≥8 non-whitespace chars with at least one letter and one digit."""
    if not _PASSWORD_RE.fullmatch(password):
        raise HTTPException(
            400,
            "password must be at least 8 characters and contain at least "
            "one letter and one digit",
        )


@app.post("/auth/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
) -> JSONResponse:
    _rate_key = _rl.client_ip_from_request(request)
    if not _rl.login.try_consume(_rate_key):
        raise HTTPException(429, "Too many login attempts — please wait before trying again.")
    if _get_user_repo() is None:
        raise HTTPException(503, "auth not configured")
    actor = _audit.actor_from_request(request)
    found = _get_user_repo().find_by_email(email)
    if not found:
        _audit.record(
            action="auth.login.fail",
            target_type="email", target_id=email,
            note="user not found", **actor,
        )
        raise HTTPException(401, "invalid credentials")
    user, password_hash = found
    # Check account_locked BEFORE verify_password to avoid a status-code
    # oracle: if we checked after, a correct password on a locked account
    # returns 403 while a wrong password returns 401, leaking the password.
    if user.account_locked:
        _audit.record(
            action="auth.login.blocked",
            actor_user_id=user.id,
            target_type="user", target_id=user.id,
            note="account locked", **actor,
        )
        raise HTTPException(403, "account suspended — contact support")
    if not password_hash or not verify_password(password, password_hash):
        _audit.record(
            action="auth.login.fail",
            actor_user_id=user.id,
            target_type="email", target_id=email,
            note="bad password", **actor,
        )
        raise HTTPException(401, "invalid credentials")
    _audit.record(
        action="auth.login.success",
        actor_user_id=user.id,
        target_type="user", target_id=user.id,
        **actor,
    )
    token = issue_token(user.id)
    return _set_auth_cookie(JSONResponse({
        "user_id": user.id,
        "email": user.email,
        "subscription_tier": user.subscription_tier,
        "subscription_level": user.subscription_level,
        "token": token,
    }), token, request)


@app.post("/auth/logout")
def logout() -> JSONResponse:
    return _delete_auth_cookie(JSONResponse({"ok": True}))


# ---------- E7: SSO (Google + Microsoft OIDC) ----------

def _sso_redirect_uri(request: Request, provider: str) -> str:
    """Production-safe redirect URI builder. Uses X-Forwarded-Proto +
    X-Forwarded-Host when behind Render's TLS terminator."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    return f"{scheme}://{host}/auth/sso/{provider}/callback"


@app.get("/auth/sso/providers")
def sso_providers() -> JSONResponse:
    """Which SSO providers are configured on this deploy. Sign-in UI
    uses this to decide which buttons to render — no point showing a
    'Continue with Google' button if GOOGLE_CLIENT_ID isn't set."""
    return JSONResponse({"providers": _sso.configured_providers()})


@app.get("/auth/sso/{provider}/start")
def sso_start(provider: str, request: Request, next: str = "/"):
    """Kick off the OIDC flow — redirects the browser to the provider."""
    if provider not in _sso.PROVIDERS:
        raise HTTPException(404, f"unknown provider {provider!r}")
    if not _sso.is_configured(provider):
        raise HTTPException(
            503,
            f"{provider} SSO not configured (set {provider.upper()}_CLIENT_ID + "
            f"{provider.upper()}_CLIENT_SECRET on this deploy)",
        )
    try:
        url = _sso.build_authorize_url(
            provider,
            redirect_uri=_sso_redirect_uri(request, provider),
            redirect_after=next or "/",
        )
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    return RedirectResponse(url, status_code=302)


@app.get("/auth/sso/{provider}/callback", response_class=HTMLResponse)
def sso_callback(
    provider: str, request: Request,
    code: str | None = None, state: str | None = None,
    error: str | None = None, error_description: str | None = None,
):
    """OAuth callback. The provider redirects the user here with either
    a `code` (success) or an `error` (user cancelled / IdP rejected).

    On success: exchange code → claims → resolve-or-create user → mint
    our session JWT → redirect into the app with the token in a URL
    fragment (so it doesn't end up in server logs).
    """
    if error:
        return HTMLResponse(_sso_error_page(
            f"{provider} sign-in cancelled or rejected",
            error_description or error,
        ), status_code=400)
    if not code or not state:
        raise HTTPException(400, "missing code or state")
    if provider not in _sso.PROVIDERS:
        raise HTTPException(404, f"unknown provider {provider!r}")

    try:
        claims, redirect_after = _sso.exchange_code(
            provider, code=code, state=state,
            redirect_uri=_sso_redirect_uri(request, provider),
        )
    except (ValueError, RuntimeError) as e:
        return HTMLResponse(_sso_error_page(
            "Sign-in failed", str(e),
        ), status_code=400)

    resolution = _sso.resolve_or_create_user(claims)
    token = issue_token(resolution.user_id)

    # We bounce through a tiny HTML page that puts the token into
    # localStorage and then redirects — this keeps the token out of
    # access logs (URL fragments are not sent to the server, but they
    # ARE in the user's address bar; localStorage is cleaner still).
    body = (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Signing in…</title></head><body>"
        f"<script>"
        f"localStorage.setItem('pathshala_token', {token!r});"
        f"localStorage.setItem('pathshala_email', {resolution.email!r});"
        f"window.location.replace({redirect_after!r});"
        f"</script>"
        f"<p>Signing you in… "
        f"<a href={redirect_after!r}>continue</a></p>"
        f"</body></html>"
    )
    return _set_auth_cookie(HTMLResponse(body), token, request)


def _sso_error_page(title: str, detail: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset='utf-8'><title>Sign-in error — AI Pathshala</title>
<style>body{{font:16px/1.5 system-ui; padding:40px; max-width:560px; margin:auto;}}
.card{{background:#fff; border:1px solid #fee2e2; border-radius:12px; padding:24px;}}
h1{{color:#991b1b; font-size:20px; margin:0 0 8px;}}
p{{color:#4b5563; margin:6px 0;}} a{{color:#5E60CE;}}</style>
</head><body><div class='card'>
<h1>⚠ {_escape_html(title)}</h1>
<p>{_escape_html(detail)}</p>
<p><a href='/'>← Back to AI Pathshala</a></p>
</div></body></html>"""


@app.get("/auth/me")
def me(user: AuthUser | None = Depends(current_user)) -> JSONResponse:
    if user is None:
        return JSONResponse({"authenticated": False})
    return JSONResponse({
        "authenticated": True,
        "user_id": user.id,
        "email": user.email,
        "subscription_tier": user.subscription_tier,
        "subscription_level": user.subscription_level,
        "talking_head_provider": resolve_provider_for_tier(user),
    })


@app.get("/tiers")
def tiers() -> JSONResponse:
    return JSONResponse(
        {
            "languages": sorted(SUPPORTED_LANGUAGES),
            "levels": sorted(LEVEL_GUIDANCE),
            "boards": sorted(BOARD_GUIDANCE),
            "themes": sorted(THEME_REGISTRY),
            "talking_head": get_talking_head_provider().name,
        }
    )


@app.post("/lessons", status_code=202)
def create_lesson(
    request: Request,
    image: UploadFile = File(..., description="textbook page image (JPG/PNG)"),
    language: str = Form("en"),
    level: str = Form("middle"),
    theme: str | None = Form(None),
    teacher: bool = Form(True),
    include_quiz: bool = Form(True),
    render_mode: str = Form("animated"),
    board: str | None = Form(None, description="curriculum board, e.g. CBSE, ICSE, NEET, JEE"),
    exam: str | None = Form(None, description="competitive exam context, e.g. NEET, JEE, UPSC"),
    user: AuthUser | None = Depends(current_user),
):
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(400, f"language must be one of {sorted(SUPPORTED_LANGUAGES)}")
    if level not in LEVEL_GUIDANCE:
        raise HTTPException(400, f"level must be one of {sorted(LEVEL_GUIDANCE)}")
    _rate_key = user.id if user else _rl.client_ip_from_request(request)
    if not _rl.ai_generation.try_consume(_rate_key):
        raise HTTPException(429, "Too many lesson generations — please wait a moment before trying again.")

    # Persist the upload. For PDFs / PPTX / DOCX, fan out to one page
    # image per page; the rest of the pipeline only knows about images.
    suffix = Path(image.filename or "page.jpg").suffix.lower() or ".jpg"
    _SIZE_LIMIT = 25 * 1024 * 1024
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        upload_path = Path(f.name)
        total = 0
        for chunk in iter(lambda: image.file.read(65536), b""):
            total += len(chunk)
            if total > _SIZE_LIMIT:
                f.close()
                upload_path.unlink(missing_ok=True)
                raise HTTPException(413, "file too large (limit 25 MB)")
            f.write(chunk)
    try:
        page_images = ingest_source(upload_path)
    except ValueError as e:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(400, str(e))
    image_path = page_images[0]
    extra_pages = page_images[1:]
    # ingest() calls `source.resolve()`, so image_path is a normalized
    # absolute Path while upload_path is whatever NamedTemporaryFile
    # produced — on Windows the two often differ in casing/separators
    # even when pointing to the same file. Use samefile() so we don't
    # accidentally unlink the only copy and crash the subsequent
    # read_bytes() call.
    try:
        same_source = upload_path.samefile(image_path)
    except (FileNotFoundError, OSError):
        same_source = upload_path == image_path
    if not same_source:
        upload_path.unlink(missing_ok=True)

    # Synchronous cache short-circuit. Two layers checked in order:
    #   1) Cloud object storage — beats the local cache once R2 has the file
    #   2) Local filesystem cache — for dev / single-instance deploys
    # Either way we don't stream MP4 bytes through this web tier; we
    # redirect the client straight to the storage URL (or local FileResponse
    # when object storage is the LocalDiskStorage shim).
    chosen_theme = theme_for_level(level, theme)
    # Server-side tier enforcement: anonymous + M1 users get cartoon
    # regardless of what the form requests; paying tiers get the
    # provider their subscription entitles them to.
    if teacher:
        entitled = resolve_provider_for_tier(user)
        os.environ["PADHAI_TALKING_HEAD_PROVIDER"] = entitled
        provider = get_talking_head_provider()
    else:
        provider = None
    provider_name = provider.name if provider else "none"
    image_bytes = image_path.read_bytes()
    storage_key = _video_storage_key(
        image_bytes, language, level,
        chosen_theme.name, provider_name, render_mode,
    )
    if object_storage.exists(storage_key):
        image_path.unlink(missing_ok=True)
        url = object_storage.url(storage_key)
        if url.startswith("file://"):
            return FileResponse(url[7:], media_type="video/mp4", filename="lesson.mp4")
        return RedirectResponse(url, status_code=302)

    cache_probe = _OUTPUT_DIR / "probe.mp4"
    if cache.get_video(
        image_bytes, language, level,
        chosen_theme.name, provider_name, render_mode,
        cache_probe,
    ):
        # promote local-cache hit to object storage so other instances get it
        url = object_storage.put(storage_key, cache_probe)
        image_path.unlink(missing_ok=True)
        if url.startswith("file://"):
            return FileResponse(cache_probe, media_type="video/mp4", filename="lesson.mp4")
        return RedirectResponse(url, status_code=302)

    # Exam key takes precedence over board when both are supplied (e.g. a
    # CBSE student also doing NEET prep should get NEET-specific framing).
    from .pedagogy import BOARD_GUIDANCE as _BOARD_GUIDANCE
    board_hint: str | None = None
    if exam and exam.upper() in _BOARD_GUIDANCE:
        board_hint = exam.upper()
    elif board and board.upper() in _BOARD_GUIDANCE:
        board_hint = board.upper()

    job_payload = {
        "image_path": str(image_path),
        "language": language,
        "level": level,
        "theme": theme,
        "teacher": teacher,
        "include_quiz": include_quiz,
        "render_mode": render_mode,
        # Stamping the resolved provider on the payload routes the job
        # to the right worker — Wav2Lip → GPU instance, everything else
        # → web service's local thread pool.
        "talking_head_provider": provider_name,
    }
    if board_hint:
        job_payload["board_hint"] = board_hint
    if user is not None:
        job_payload["user_id"] = user.id
        job_payload["subscription_tier"] = user.subscription_tier
    job = runner.enqueue(job_payload)

    # Multi-page upload: fan out one job per remaining page.
    # Enqueue extra pages. On any failure, unlink ALL remaining temp files
    # (both the failing page and any pages not yet enqueued) to avoid leaks.
    # Already-enqueued extra jobs are allowed to proceed — the first-page job
    # is also already running, so a partial multi-page render is better than
    # dropping everything and leaving no output for the user.
    extra_jobs = []
    remaining = list(extra_pages)
    for extra in extra_pages:
        remaining.remove(extra)
        try:
            extra_payload = dict(job_payload)
            extra_payload["image_path"] = str(extra)
            extra_jobs.append(runner.enqueue(extra_payload))
        except Exception:
            Path(extra).unlink(missing_ok=True)
            for leftover in remaining:
                Path(leftover).unlink(missing_ok=True)
            raise

    response = {
        "job_id": job.id,
        "status": job.status,
        "status_url": f"/jobs/{job.id}",
        "video_url": f"/jobs/{job.id}/video",
    }
    if extra_jobs:
        response["additional_pages"] = [
            {"job_id": j.id, "status_url": f"/jobs/{j.id}", "video_url": f"/jobs/{j.id}/video"}
            for j in extra_jobs
        ]
        response["total_pages"] = 1 + len(extra_jobs)
    return JSONResponse(status_code=202, content=response)


# ---- chat-on-content (Spark.E equivalent) ---------------------------------

import anthropic as _anthropic   # noqa: E402  — placed here to keep top imports tidy

_chat_client: _anthropic.Anthropic | None = None


def _claude() -> _anthropic.Anthropic:
    global _chat_client
    if _chat_client is None:
        _chat_client = _anthropic.Anthropic()
    return _chat_client


CHAT_SYSTEM_PROMPT = """You are PadhAI's Spark — a friendly tutor that answers \
follow-up questions about a lesson the student has just watched.

You will be given the lesson plan as JSON (title, scenes with bullets, quiz). \
Answer based ONLY on the lesson content; if the student asks something the \
lesson doesn't cover, say so honestly and suggest they re-scan a related page \
rather than guessing. Keep replies short (2-4 sentences) and match the \
student's language.

SOURCE CITATIONS (REQUIRED): Whenever a fact in your answer comes from a \
specific scene, append the scene number in square brackets right after that \
fact — for example "Plants take in CO2 through stomata [Scene 2]". Use the \
exact form [Scene N] where N is the 1-indexed scene number. Cite multiple \
scenes when needed: "...this happens in chloroplasts [Scene 1, Scene 3]". \
The UI parses these citations to show clickable jump-to-scene buttons, so \
the format must be exact."""


# Match a single [Scene N] OR a comma-joined list of scenes inside one
# bracket: [Scene 1], [Scene 2, Scene 3], [Scene 1, 2, 4].
_CITATION_BLOCK_RE = re.compile(r"\[Scene[^\]]*\]")
_CITATION_NUM_RE = re.compile(r"\d+")


def _parse_citations(text: str, total_scenes: int) -> list[dict]:
    """Extract every [Scene N] citation from the model's answer and
    return a deduplicated list of {scene_number} objects. Numbers
    outside the valid range are dropped silently — the citation system
    is best-effort, not load-bearing."""
    seen: set[int] = set()
    out: list[dict] = []
    for block in _CITATION_BLOCK_RE.findall(text):
        for num_match in _CITATION_NUM_RE.findall(block):
            n = int(num_match)
            if 1 <= n <= total_scenes and n not in seen:
                seen.add(n)
                out.append({"scene_number": n})
    return out


def _compute_user_stats(user_id: str | None, days: int) -> dict:
    """Aggregate jobs over the window. user_id=None means "anonymous"
    and returns the last 5 public jobs (fresh-deploy UX).

    Extracted in v0.14 E8 so /api/parents/children/{cid}/stats can
    reuse the same logic, scoped to the child's user_id.
    """
    import collections

    days = max(1, min(90, days))
    cutoff = time.time() - (days * 86400)

    if user_id is not None:
        all_jobs = store.recent_jobs(limit=200, filter_user_id=user_id)
    else:
        all_jobs = store.recent_jobs(limit=20)

    succeeded = [j for j in all_jobs if j.status == "succeeded"]
    recent = [j for j in succeeded if j.created_at >= cutoff]

    languages = collections.Counter()
    levels = collections.Counter()
    activity_by_day: dict[str, dict] = {}
    cache_hits = 0
    total_estimated_minutes = 0  # generous estimate: 6 min/video viewed

    for j in succeeded:
        languages[j.payload.get("language", "en")] += 1
        levels[j.payload.get("level", "middle")] += 1
        if (j.result or {}).get("cache_hit"):
            cache_hits += 1

    for j in recent:
        from datetime import datetime, timezone
        d = datetime.fromtimestamp(j.created_at, tz=timezone.utc).strftime("%Y-%m-%d")
        bucket = activity_by_day.setdefault(d, {"date": d, "lessons": 0, "minutes": 0})
        bucket["lessons"] += 1
        bucket["minutes"] += 6
        total_estimated_minutes += 6

    # Fill every day in the window so the chart shows zeros (parents
    # want to see the gap days, not skip them)
    from datetime import datetime, timezone, timedelta
    series: list[dict] = []
    today = datetime.now(tz=timezone.utc).date()
    for i in range(days):
        d = today - timedelta(days=days - 1 - i)
        key = d.strftime("%Y-%m-%d")
        series.append(activity_by_day.get(key, {"date": key, "lessons": 0, "minutes": 0}))

    # Streak — count consecutive days with at least one lesson ending today
    streak = 0
    for s in reversed(series):
        if s["lessons"] > 0:
            streak += 1
        else:
            if streak > 0:
                break
    # If today has no activity but yesterday did, streak counts yesterday
    if streak == 0 and len(series) > 1 and series[-2]["lessons"] > 0:
        for s in reversed(series[:-1]):
            if s["lessons"] > 0:
                streak += 1
            else:
                break

    # Most recent lessons (capped for the UI)
    recent_lessons = []
    for j in succeeded[:10]:
        r = j.result or {}
        recent_lessons.append({
            "id": j.id,
            "lesson_id": r.get("lesson_id"),
            "language": j.payload.get("language"),
            "level": j.payload.get("level"),
            "created_at": j.created_at,
            "video_url": f"/jobs/{j.id}/video" if r else None,
        })

    return {
        "authenticated": user_id is not None,
        "window_days": days,
        "summary": {
            "lessons_total": len(succeeded),
            "lessons_in_window": len(recent),
            "estimated_minutes": total_estimated_minutes,
            "cache_hits": cache_hits,
            "streak_days": streak,
            "languages_count": len(languages),
        },
        "activity": series,
        "top_languages": [
            {"code": k, "count": v}
            for k, v in languages.most_common(5)
        ],
        "top_levels": [
            {"level": k, "count": v}
            for k, v in levels.most_common(5)
        ],
        "recent_lessons": recent_lessons,
    }


@app.get("/me/stats")
def my_stats(
    days: int = 7,
    user: AuthUser | None = Depends(current_user),
):
    """Aggregated activity for the dashboard. Thin wrapper around
    _compute_user_stats so /api/parents/children/{cid}/stats shares
    the same logic."""
    return _compute_user_stats(user.id if user else None, days)


@app.post("/learning-path")
def make_learning_path(
    student_class: int = Form(...),
    subjects: str = Form(...),         # comma-separated, e.g. "Maths,Science"
    weeks: int = Form(4),
    daily_minutes: int = Form(30),
    focus_topics: str = Form(""),       # comma-separated, optional
    regenerate: bool = Form(False),
    user: AuthUser | None = Depends(current_user),
):
    """Generate a multi-week personalised study plan.

    Reuses the user's library (existing lessons) + the curriculum index
    seeded in padhai/curriculum.py. Uses Claude Opus 4.7 with adaptive
    thinking — this is a real planning task (Haiku gets task-mix wrong
    in evals). ~₹4-6/call, cached by deterministic input key so the
    same student request returns instantly."""
    from .pedagogy import generate_learning_path
    from .curriculum import CURRICULUM

    subject_list = [s.strip() for s in subjects.split(",") if s.strip()]
    focus_list = [s.strip() for s in focus_topics.split(",") if s.strip()]
    if not subject_list:
        raise HTTPException(400, "subjects must be non-empty (e.g. 'Maths,Science')")
    if student_class < 1 or student_class > 12:
        raise HTTPException(400, "student_class must be 1..12")

    # Pull user's library to seed the planner; anonymous → empty
    if user is not None:
        recent = store.recent_jobs(limit=30, filter_user_id=user.id)
    else:
        recent = store.recent_jobs(limit=5)
    library = []
    for j in recent:
        if j.status != "succeeded":
            continue
        r = j.result or {}
        if r.get("lesson_id"):
            library.append({
                "lesson_id": r["lesson_id"],
                "language": j.payload.get("language"),
                "level": j.payload.get("level"),
            })

    key = cache.learning_path_key(
        student_class, subject_list, weeks, daily_minutes,
        focus_list, len(library),
    )
    if not regenerate:
        cached = cache.get_learning_path(key)
        if cached is not None:
            return {"plan": cached, "cached": True, "key": key}

    plan = generate_learning_path(
        student_class=student_class,
        subjects=subject_list,
        weeks=weeks,
        daily_minutes=daily_minutes,
        focus_topics=focus_list,
        library_lessons=library,
        catalogue=CURRICULUM,
    )
    cache.put_learning_path(key, plan)
    return {"plan": plan, "cached": False, "key": key}


@app.post("/lessons/{lesson_id}/curriculum")
def curriculum_for_lesson(
    lesson_id: str,
    regenerate: bool = False,
    user: AuthUser | None = Depends(current_user),
):
    """Match a generated lesson against the NCERT/state-board catalogue.

    Returns up to 3 ranked curriculum entries (board, class, subject,
    chapter) with confidence + reason. Lever for the Learning Path
    module (Phase 2) and for surfacing 'related practice from CBSE
    Class 8 Chapter 6' under a lesson.

    Per-user resource — requires auth even when PADHAI_REQUIRE_AUTH=0
    (anonymous users have no lessons of their own to match against).

    Cached idempotently. ~₹0.20/call (Haiku 4.5)."""
    if user is None:
        raise HTTPException(401, "authentication required")
    from .pedagogy import match_curriculum
    from .curriculum import CURRICULUM

    if not regenerate:
        cached = cache.get_curriculum_matches(lesson_id)
        if cached is not None:
            return {
                "lesson_id": lesson_id,
                "matches": cached,
                "cached": True,
                "count": len(cached),
            }

    cached_lesson = cache.get_lesson_by_key(lesson_id)
    if cached_lesson is None:
        raise HTTPException(404, "lesson not found; POST /lessons first")

    matches = match_curriculum(cached_lesson, CURRICULUM)
    cache.put_curriculum_matches(lesson_id, matches)
    return {
        "lesson_id": lesson_id,
        "matches": matches,
        "cached": False,
        "count": len(matches),
    }


@app.get("/curriculum/index")
def curriculum_index(
    board: str | None = None,
    cls: int | None = None,
    subject: str | None = None,
):
    """Return the curriculum catalogue (no copyrighted content — just
    metadata: chapter titles + topic tags). Used by the Curriculum Map
    module to render the browseable index.

    Static seed list (curriculum.py) is the base; DB rows override or
    extend it when a curriculum_topics table is available."""
    from .curriculum import CURRICULUM
    # Start with the static seed
    rows: list[dict] = [dict(r) for r in CURRICULUM]
    # Merge DB overrides / additions when Postgres is available
    db_url = get_db_url()
    if db_url:
        try:
            import psycopg, json as _json
            with psycopg.connect(db_url) as conn:
                db_rows = conn.execute(
                    "SELECT id, board, class, subject, chapter_no, chapter_title, "
                    "level, summary, topics FROM curriculum_topics "
                    "ORDER BY board, class, subject, chapter_no"
                ).fetchall()
            if db_rows:
                # Build index of static rows for deduplication
                static_idx = {
                    (r["board"], r["class"], r["subject"], r["chapter_no"])
                    for r in rows
                }
                for dbr in db_rows:
                    key = (dbr[1], dbr[2], dbr[3], dbr[4])
                    entry = {
                        "id": dbr[0], "board": dbr[1], "class": dbr[2],
                        "subject": dbr[3], "chapter_no": dbr[4],
                        "chapter_title": dbr[5], "level": dbr[6],
                        "summary": dbr[7],
                        "topics": dbr[8] if isinstance(dbr[8], list)
                                  else _json.loads(dbr[8] or "[]"),
                        "source": "db",
                    }
                    if key in static_idx:
                        # Override matching static entry
                        rows = [entry if (
                            r["board"] == dbr[1] and r["class"] == dbr[2] and
                            r["subject"] == dbr[3] and r["chapter_no"] == dbr[4]
                        ) else r for r in rows]
                    else:
                        rows.append(entry)
        except Exception:
            pass  # DB unavailable — static list is still fully useful
    # Apply filters after merge
    if board:
        rows = [r for r in rows if r["board"] == board]
    if cls is not None:
        rows = [r for r in rows if r["class"] == cls]
    if subject:
        rows = [r for r in rows if r["subject"] == subject]
    all_boards = sorted({r["board"] for r in rows}) if not board else [board]
    all_classes = sorted({r["class"] for r in rows}) if cls is None else [cls]
    all_subjects = sorted({r["subject"] for r in rows}) if not subject else [subject]
    return {
        "boards": all_boards, "classes": all_classes, "subjects": all_subjects,
        "entries": rows, "count": len(rows),
    }


@app.post("/lessons/{lesson_id}/flashcards")
def make_flashcards(
    lesson_id: str,
    count: int = 8,
    regenerate: bool = False,
    user: AuthUser | None = Depends(current_user),
):
    """Generate (or fetch cached) flashcards for a lesson the student
    watched. Idempotent — second call returns the cached set unless
    `regenerate=true`. Free for M1/M2 (uses Haiku 4.5, ~₹0.30/call).

    Per-user resource — requires auth even when PADHAI_REQUIRE_AUTH=0."""
    if user is None:
        raise HTTPException(401, "authentication required")
    from .pedagogy import generate_flashcards

    if not regenerate:
        cached = cache.get_flashcards(lesson_id)
        if cached is not None:
            return {
                "lesson_id": lesson_id,
                "cards": cached,
                "cached": True,
                "count": len(cached),
            }

    cached_lesson = cache.get_lesson_by_key(lesson_id)
    if cached_lesson is None:
        raise HTTPException(404, "lesson not found; POST /lessons first")

    cards = generate_flashcards(cached_lesson, count=count)
    cache.put_flashcards(lesson_id, cards)
    return {
        "lesson_id": lesson_id,
        "cards": cards,
        "cached": False,
        "count": len(cards),
    }


@app.post("/chat/{lesson_id}")
def chat_about_lesson(
    request: Request,
    lesson_id: str,
    question: str = Form(..., min_length=2),
    user: AuthUser | None = Depends(current_user),
) -> JSONResponse:
    """Ask a question about a lesson the student watched.

    `lesson_id` comes from the job result returned by GET /jobs/{id}
    once the render completes (look for `result.lesson_id`).
    The endpoint loads the cached Lesson JSON and answers with Claude
    grounded in that material — no general-knowledge hallucination."""
    _rate_key = user.id if user else _rl.client_ip_from_request(request)
    if not _rl.ai_generation.try_consume(_rate_key):
        raise HTTPException(429, "Too many requests — please wait before asking again.")
    import dataclasses
    import json
    from .pedagogy import MODEL

    # S4 anti-cheat: if the user is in the middle of an exam, lock
    # the doubt-chat. The exam attempt has to finish (submit or timer
    # expiry) before chat reopens. We don't 403 silently — the
    # response tells the client WHY so the UI can show a helpful
    # "Doubt chat is locked while you're taking [exam]" message.
    if user is not None:
        active_exam_id = _orgs.has_active_exam(user.id)
        if active_exam_id:
            raise HTTPException(
                status_code=423,  # Locked
                detail={
                    "error": "exam_mode_active",
                    "exam_id": active_exam_id,
                    "message": "Doubt chat is locked while you have an "
                               "active exam attempt. Submit the exam to "
                               "use chat again.",
                },
            )

    cached = cache.get_lesson_by_key(lesson_id)
    if cached is None:
        raise HTTPException(404, "lesson not found; POST /lessons first")

    lesson_json = json.dumps(dataclasses.asdict(cached), ensure_ascii=False)
    response = _claude().messages.create(
        model=MODEL,
        max_tokens=1000,
        system=CHAT_SYSTEM_PROMPT + "\n\nLESSON:\n" + lesson_json,
        messages=[{"role": "user", "content": question}],
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
    )
    answer = next((b.text for b in response.content if b.type == "text"), "")
    citations = _parse_citations(answer, total_scenes=len(cached.scenes))
    # v0.14 C7: page citations come from each cited scene's
    # source_pages (set by the lesson generator). Dedupe across cited
    # scenes so a parent sees "this answer came from pages 4, 7" once,
    # not 4-then-4 because two scenes reference page 4.
    cited_pages: list[int] = []
    seen_pages: set[int] = set()
    for c in citations:
        scene = cached.scenes[c["scene_number"] - 1]
        for p in (scene.source_pages or []):
            if p not in seen_pages:
                seen_pages.add(p)
                cited_pages.append(p)
    return JSONResponse({
        "lesson_id": lesson_id,
        "question": question,
        "answer": answer,
        # Resolved scene metadata so the UI can render
        # "Scene 2: Let me explain" jump buttons without re-fetching.
        "source_citations": [
            {
                "scene_number": c["scene_number"],
                "scene_title": cached.scenes[c["scene_number"] - 1].title,
                "source_pages": cached.scenes[c["scene_number"] - 1].source_pages or [],
            }
            for c in citations
        ],
        # Flat list of page numbers across all cited scenes — handy
        # for "this answer came from pages 4, 7" hint at the bottom
        # of the chat bubble.
        "source_pages": cited_pages,
    })


@app.post("/lessons/{lesson_id}/quiz")
def standalone_quiz(
    lesson_id: str,
    user: AuthUser | None = Depends(current_user),
):
    """Return the quiz JSON for a cached lesson without rendering a video.
    Used by the Quiz Maker module — the player UI scores the student and
    shows correct/wrong feedback. Free: no Claude call, just a cache lookup."""
    cached_lesson = cache.get_lesson_by_key(lesson_id)
    if cached_lesson is None:
        raise HTTPException(404, "lesson not found; POST /lessons first")
    return {
        "lesson_id": lesson_id,
        "title": cached_lesson.title,
        "language_code": cached_lesson.language_code,
        "language_name": cached_lesson.language_name,
        "level": cached_lesson.level,
        "questions": cached_lesson.quiz,
    }


@app.post("/lessons/{lesson_id}/recap")
def make_recap(
    lesson_id: str,
    regenerate: bool = False,
    user: AuthUser | None = Depends(current_user),
):
    """Generate (or fetch cached) podcast-style audio recap.

    First call: Haiku text generation + Piper/gTTS synthesis → cached MP3
    (~₹0.20). Every subsequent listener hits the cache: instant + free.
    Returns the recap text and a URL the browser can play directly."""
    from .pedagogy import generate_recap
    from .tts import get_provider as get_tts_provider

    cached_lesson = cache.get_lesson_by_key(lesson_id)
    if cached_lesson is None:
        raise HTTPException(404, "lesson not found; POST /lessons first")

    provider = get_tts_provider()
    audio_path = cache.recap_audio_path(lesson_id, provider.name)
    text = cache.get_recap_text(lesson_id) if not regenerate else None

    if text is None or not audio_path.exists() or regenerate:
        text = generate_recap(cached_lesson)
        cache.put_recap_text(lesson_id, text)
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            provider.synthesise(text, cached_lesson.language_code, audio_path)
        except Exception as e:  # TTS failed — return text-only so UI can still render
            return {
                "lesson_id": lesson_id,
                "text": text,
                "audio_url": None,
                "audio_error": f"tts failed: {e}",
                "cached": False,
            }
        cached = False
    else:
        cached = True

    return {
        "lesson_id": lesson_id,
        "text": text,
        "audio_url": f"/lessons/{lesson_id}/recap.mp3",
        "cached": cached,
    }


@app.get("/lessons/{lesson_id}/recap.mp3")
def get_recap_audio(lesson_id: str):
    """Stream the cached recap MP3. POST /lessons/{id}/recap must be
    called once first to populate the cache."""
    from .tts import get_provider as get_tts_provider

    provider = get_tts_provider()
    audio_path = cache.recap_audio_path(lesson_id, provider.name)
    if not audio_path.exists():
        raise HTTPException(404, "recap not generated yet; POST /lessons/{id}/recap first")
    return FileResponse(audio_path, media_type="audio/mpeg", filename=f"recap-{lesson_id}.mp3")


@app.get("/lessons/{lesson_id}/notes")
def get_notes(
    lesson_id: str,
    user: AuthUser | None = Depends(current_user),
):
    """Fetch the user's notes attached to a lesson. Empty body when none.

    Response carries both `notes` (legacy) and `content` (Cypress-spec
    convention) keys so callers can use either.

    Per-user resource — requires auth even when PADHAI_REQUIRE_AUTH=0."""
    if user is None:
        raise HTTPException(401, "authentication required")
    user_key = user.id
    text = cache.get_notes(lesson_id, user_key=user_key) or ""
    return {"lesson_id": lesson_id, "notes": text, "content": text}


@app.post("/lessons/{lesson_id}/notes")
def put_notes(
    lesson_id: str,
    notes: str | None = Form(None, max_length=50_000),
    content: str | None = Form(None, max_length=50_000),
    user: AuthUser | None = Depends(current_user),
):
    """Save the user's notes (overwrite). The browser autosaves on idle so
    this gets called silently every few seconds while the user types.

    Accepts either `notes` (legacy) or `content` (Cypress-spec convention)
    form field — whichever is provided is persisted.

    Per-user resource — requires auth even when PADHAI_REQUIRE_AUTH=0."""
    if user is None:
        raise HTTPException(401, "authentication required")
    text = notes if notes is not None else (content or "")
    cache.put_notes(lesson_id, text, user_key=user.id)
    return {"lesson_id": lesson_id, "saved": True, "length": len(text)}


@app.post("/lessons/{lesson_id}/flashcards/rate")
def rate_flashcard(
    lesson_id: str,
    card_id: int = Form(...),
    rating: int = Form(..., ge=0, le=5),
    user: AuthUser | None = Depends(current_user),
):
    """SM-2 review rating endpoint. Forwards to the canonical
    spaced_repetition.review_card pathway. Requires auth."""
    if user is None:
        raise HTTPException(401, "authentication required")
    try:
        from . import spaced_repetition as _srs
        result = _srs.review_card(
            card_id=str(card_id), user_id=user.id, grade=rating,
        )
        return {
            "lesson_id": lesson_id,
            "card_id": card_id,
            "rating": rating,
            "next_due_at": getattr(result, "due_at", None),
            "ease": getattr(result, "ease", None),
        }
    except Exception as exc:
        # Unknown card → still tell the client the rating was recorded
        return {"lesson_id": lesson_id, "card_id": card_id,
                "rating": rating, "note": str(exc)[:100]}


@app.post("/explain")
def explain_topic(
    topic: str = Form(..., min_length=2, max_length=200),
    language: str = Form("en"),
    level: str = Form("middle"),
    regenerate: bool = Form(False),
    user: AuthUser | None = Depends(current_user),
):
    """Type-a-topic explainer. No file upload needed.

    Returns a 7-field structured explanation (one-liner, paragraphs,
    key points, worked example, common mistakes, analogy). Cached by
    (topic, language, level) so the same 'photosynthesis' from 1000
    students hits the cache 999 times — total cost ~₹0.30 for all."""
    from .pedagogy import generate_explainer

    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(400, f"language must be one of: {sorted(SUPPORTED_LANGUAGES)}")
    if level not in LEVEL_GUIDANCE:
        raise HTTPException(400, f"level must be one of: {sorted(LEVEL_GUIDANCE)}")

    # Moderation gate (PRD §9.4) — block scams, hate, CSAM, etc. before
    # we spend Opus tokens on the generation. Fail-open on classifier
    # outage; results are logged for admin review either way.
    mod = _moderation.classify(
        topic, content_kind="topic",
        user_id=(user.id if user else None),
    )
    if not mod.allowed:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "content_blocked",
                "category": mod.category,
                "reasoning": mod.reasoning or "This topic isn't supported here.",
                "log_id": mod.log_id,
            },
        )

    if not regenerate:
        cached = cache.get_explainer(topic, language, level)
        if cached is not None:
            return {**cached, "cached": True, "language": language, "level": level}

    payload = generate_explainer(topic, language_code=language, level=level)
    cache.put_explainer(topic, language, level, payload)
    return {**payload, "cached": False, "language": language, "level": level}


@app.post("/explain/video")
def explain_video(
    topic: str = Form(..., min_length=2, max_length=200),
    language: str = Form("en"),
    level: str = Form("middle"),
    teacher: bool = Form(True),
    user: AuthUser | None = Depends(current_user),
):
    """Generate a cartoon-video explainer for a topic.

    Reuses the existing render pipeline: Explainer JSON → 5-scene Lesson
    → narrated cartoon teacher + scene boards → MP4. Returns a job_id
    the client polls just like Create Lesson. Video cache shared with
    /lessons so the same (topic, lang, level) only renders once."""
    from .pedagogy import generate_explainer

    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(400, f"language must be one of: {sorted(SUPPORTED_LANGUAGES)}")
    if level not in LEVEL_GUIDANCE:
        raise HTTPException(400, f"level must be one of: {sorted(LEVEL_GUIDANCE)}")

    explainer = cache.get_explainer(topic, language, level)
    if explainer is None:
        explainer = generate_explainer(topic, language_code=language, level=level)
        cache.put_explainer(topic, language, level, explainer)

    if teacher:
        entitled = resolve_provider_for_tier(user)
        os.environ["PADHAI_TALKING_HEAD_PROVIDER"] = entitled
        provider = get_talking_head_provider()
        provider_name = provider.name
    else:
        provider_name = "none"

    payload = {
        "kind": "explainer",
        "topic": topic,
        "explainer": explainer,
        "language": language,
        "level": level,
        "teacher": teacher,
        "render_mode": "animated",
        "talking_head_provider": provider_name,
    }
    if user is not None:
        payload["user_id"] = user.id
        payload["subscription_tier"] = user.subscription_tier
    job = runner.enqueue(payload)
    return JSONResponse(status_code=202, content={
        "job_id": job.id,
        "status": job.status,
        "status_url": f"/jobs/{job.id}",
        "video_url": f"/jobs/{job.id}/video",
        "topic": topic,
    })


# ============================================================================
# v2 API — PRD §13 contract
# ----------------------------------------------------------------------------
# The v2 surface implements the PRD's full personalization contract:
# upload → analyze → video-request → status → result → regenerate → chat.
# It is BACKWARD-COMPATIBLE with the v1 `/lessons` and `/explain` paths;
# those keep working unchanged. Apps can migrate at their own pace.
#
# Key design: every v2 request runs through PersonalizationProfile
# (padhai/personalization.py) so the same uploaded material produces
# meaningfully different videos for different (user_type × age × mode
# × tone × duration × format) combinations.
# ============================================================================


_PROGRESS_STEPS = [
    "queued",
    "analyzing_document",
    "understanding_topic",
    "creating_script",
    "creating_storyboard",
    "generating_voice",
    "rendering_video",
    "preparing_quiz",
    "uploading",
    "complete",
]


def _progress_for_job(job: "Job") -> dict:
    """Surface the worker-emitted progress step + percent. Falls back
    to synthesising a position from job.status when the worker hasn't
    emitted yet (e.g. job is queued, hasn't been claimed)."""
    # Prefer the worker-emitted values if present.
    step = job.progress_step
    percent = job.progress_percent
    if not step:
        fallback = {
            "queued":    ("queued", 0),
            "running":   ("rendering_video", 60),
            "succeeded": ("complete", 100),
            "failed":    ("failed", 0),
        }
        step, percent = fallback.get(job.status, ("queued", 0))
    if job.status == "succeeded":
        step, percent = "complete", 100
    elif job.status == "failed":
        step = "failed"
    return {
        "status": job.status,
        "current_step": step,
        "progress": percent,
        "all_steps": _PROGRESS_STEPS,
    }


@app.post("/api/v2/video-requests", status_code=202)
def v2_create_video_request(
    # — source —
    upload_id: str | None = Form(None,
        description="upload_id from POST /api/v2/uploads — or omit and pass topic"),
    topic: str | None = Form(None,
        description="text topic if no upload"),
    image: UploadFile | None = File(None,
        description="alternative to upload_id — direct file upload"),
    # — personalization (PRD §6) —
    video_mode: str = Form("teaching"),
    user_type: str = Form("student"),
    age: int = Form(13),
    grade: str = Form("Class 8"),
    language: str = Form("en"),
    tone: str | None = Form(None),
    duration_seconds: int | None = Form(None),
    output_format: str = Form("16:9"),
    render_tier: str = Form("m1"),
    include_subtitles: bool = Form(True),
    user: AuthUser | None = Depends(current_user),
):
    """PRD §13.3 — Create a personalized video request.

    One of `topic`, `image`, or `upload_id` must be provided. The
    response matches the PRD spec: `{video_request_id, status,
    estimated_time_seconds}`.

    Behind the scenes this routes to the existing `/lessons` or
    `/explain/video` worker depending on whether the user provided a
    file or a topic — but all the personalization passes through
    PersonalizationProfile so the script and render adapt correctly."""
    # Moderation gate before any generation work — text-only path for
    # now. Image moderation (vision-based Haiku call) will land in
    # v0.10.1; for now the existing copyright/safety prompts in
    # generate_lesson handle the image case downstream.
    if topic:
        mod = _moderation.classify(
            topic, content_kind="topic",
            user_id=(user.id if user else None),
        )
        if not mod.allowed:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "content_blocked",
                    "category": mod.category,
                    "reasoning": mod.reasoning,
                    "log_id": mod.log_id,
                },
            )

    try:
        profile = build_profile(
            video_mode=video_mode,
            user_type=user_type,
            age=age,
            grade=grade,
            language_code=language,
            tone=tone,  # type: ignore[arg-type]
            duration_seconds=duration_seconds,
            output_format=output_format,  # type: ignore[arg-type]
            render_tier=render_tier,  # type: ignore[arg-type]
            topic_hint=topic or "",
            # PRD §17.4-5: clamp duration + render_tier to plan limits.
            # Anonymous users default to M1 (Free).
            user_subscription_tier=(user.subscription_tier if user else "M1"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Route to the right worker. Topic-only goes through the explainer
    # pipeline (no source image needed); file/upload goes through the
    # standard lessons pipeline. Both inherit the profile.
    if topic and not (image or upload_id):
        # explainer-video path
        from .pedagogy import generate_explainer
        explainer = cache.get_explainer(topic, profile.language_code, "middle")
        if explainer is None:
            explainer = generate_explainer(
                topic, language_code=profile.language_code, level="middle",
            )
            cache.put_explainer(topic, profile.language_code, "middle", explainer)
        provider_name = resolve_provider_for_tier(user)
        os.environ["PADHAI_TALKING_HEAD_PROVIDER"] = provider_name
        get_talking_head_provider()  # warm any side-effects
        payload = {
            "kind": "explainer",
            "topic": topic,
            "explainer": explainer,
            "language": profile.language_code,
            "level": "middle",
            "teacher": True,
            "render_mode": profile.render_mode,
            "talking_head_provider": provider_name,
            # carry the profile for the v2.1 worker that will read it
            "profile_json": _profile_to_dict(profile),
        }
        if user is not None:
            payload["user_id"] = user.id
            payload["subscription_tier"] = user.subscription_tier
        job = runner.enqueue(payload)
        eta = profile.duration_seconds // 2  # very rough
        return JSONResponse(status_code=202, content={
            "video_request_id": job.id,
            "status": "queued",
            "estimated_time_seconds": eta,
            "profile": _profile_to_dict(profile),
        })

    if image is not None:
        suffix = Path(image.filename or "page.jpg").suffix.lower() or ".jpg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(image.file.read())
            upload_path = Path(f.name)
        try:
            page_images = ingest_source(upload_path)
        except ValueError as e:
            upload_path.unlink(missing_ok=True)
            raise HTTPException(400, str(e))
        image_path = page_images[0]
        if upload_path != image_path:
            upload_path.unlink(missing_ok=True)

        chosen_theme = theme_for_level(_age_to_level(profile.age))
        provider_name = resolve_provider_for_tier(user)
        os.environ["PADHAI_TALKING_HEAD_PROVIDER"] = provider_name
        get_talking_head_provider()

        payload = {
            "image_path": str(image_path),
            "language": profile.language_code,
            "level": _age_to_level(profile.age),
            "theme": chosen_theme.name,
            "teacher": True,
            "include_quiz": profile.needs_quiz,
            "render_mode": profile.render_mode,
            "talking_head_provider": provider_name,
            "profile_json": _profile_to_dict(profile),
        }
        if user is not None:
            payload["user_id"] = user.id
            payload["subscription_tier"] = user.subscription_tier
        job = runner.enqueue(payload)
        return JSONResponse(status_code=202, content={
            "video_request_id": job.id,
            "status": "queued",
            "estimated_time_seconds": profile.duration_seconds,
            "profile": _profile_to_dict(profile),
        })

    if upload_id:
        # PRD §13.3 path — caller already uploaded the file and got an
        # upload_id back (possibly even ran /analyze for a preview).
        # No fresh ingest needed; we reuse the file on disk.
        rec = _uploads.get(upload_id)
        if rec is None:
            raise HTTPException(404, "upload_id not found")
        if user and rec.user_id and rec.user_id != user.id:
            raise HTTPException(403, "upload belongs to another user")
        page_path = Path(rec.file_path)
        if not page_path.exists():
            raise HTTPException(410, "uploaded file no longer on disk (retention)")
        _uploads.touch(upload_id)

        chosen_theme = theme_for_level(_age_to_level(profile.age))
        provider_name = resolve_provider_for_tier(user)
        os.environ["PADHAI_TALKING_HEAD_PROVIDER"] = provider_name
        get_talking_head_provider()
        payload = {
            "image_path": str(page_path),
            "upload_id": upload_id,   # so retention.touch() can fire on regen
            "language": profile.language_code,
            "level": _age_to_level(profile.age),
            "theme": chosen_theme.name,
            "teacher": True,
            "include_quiz": profile.needs_quiz,
            "render_mode": profile.render_mode,
            "talking_head_provider": provider_name,
            "profile_json": _profile_to_dict(profile),
        }
        if user is not None:
            payload["user_id"] = user.id
            payload["subscription_tier"] = user.subscription_tier
        job = runner.enqueue(payload)
        return JSONResponse(status_code=202, content={
            "video_request_id": job.id,
            "status": "queued",
            "estimated_time_seconds": profile.duration_seconds,
            "profile": _profile_to_dict(profile),
        })

    raise HTTPException(400,
        "must provide one of: topic, image, or upload_id")


# ---------- C2: /api/uploads — PRD §13.1 / 13.2 ----------

# Where uploaded files live on the local disk. Same convention as
# /lessons used to use; v0.13+ will swap to R2 directly.
_UPLOAD_DIR = Path(os.environ.get(
    "PADHAI_UPLOAD_DIR",
    str(Path.home() / ".padhai" / "uploads"),
))


@app.post("/api/uploads", status_code=201)
def create_upload(
    request: Request,
    # `file` is Optional so the auth gate fires BEFORE body validation
    # (FastAPI's `File(...)` validation otherwise returns 422 to anonymous
    # callers, leaking the contract). When user IS authenticated and file
    # is missing, we re-raise as 422 manually.
    file: UploadFile | None = File(None),
    is_whiteboard: bool = Form(False,
        description="True for handwritten content (whiteboard / notebook / "
                    "blackboard photo) — uses the OCR-tolerant analyzer prompt"),
    user: AuthUser | None = Depends(current_user),
):
    """Step 1 of the PRD §13 contract. Persist the upload + ingest into
    page images; do NOT run analysis yet (that's /analyze).

    `is_whiteboard=true` flags handwritten content so /analyze later
    uses the OCR-tolerant prompt (C5).

    The response carries `upload_id` + `page_count` so the UI can show
    "Uploaded 12 pages of textbook.pdf" before the user decides what to
    generate from it.

    Per-user resource — requires auth even when PADHAI_REQUIRE_AUTH=0
    (uploads live in user's library). Anonymous callers get 401.
    """
    if user is None:
        raise HTTPException(401, "authentication required")
    if file is None:
        raise HTTPException(422, "file is required")
    _rate_key = _rl.client_ip_from_request(request)
    if not _rl.file_upload.try_consume(_rate_key):
        raise HTTPException(429, "too many uploads — slow down")
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "page.jpg").suffix.lower() or ".jpg"
    if suffix not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf",
                      ".pptx", ".docx", ".heic", ".heif"):
        raise HTTPException(400, "unsupported file type")
    raw_path = _UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    body = file.file.read()
    if len(body) > 25 * 1024 * 1024:
        raise HTTPException(413, "file too large (limit 25 MB)")
    raw_path.write_bytes(body)

    try:
        page_images = ingest_source(raw_path)
    except ValueError as e:
        raw_path.unlink(missing_ok=True)
        raise HTTPException(400, str(e))

    # For multi-page sources (PDF/PPTX/DOCX) we keep ONLY the first
    # page image as the upload's `file_path` — that's what the
    # downstream analyzer + lesson generator look at today. v0.13
    # multi-page lessons will track all page images in a
    # `document_pages` table per PRD §12.
    page_path = page_images[0]
    # If ingest converted the original (PDF → PNG), the raw upload is
    # no longer needed; the page image is the source of truth.
    # See create_lesson() above for the same Path-equality issue —
    # ingest() resolves the path, so direct `!=` falsely flags the
    # single-image case and unlinks the file we're about to register.
    try:
        same_source = raw_path.samefile(page_path)
    except (FileNotFoundError, OSError):
        same_source = raw_path == page_path
    if not same_source:
        raw_path.unlink(missing_ok=True)

    # C5: whiteboard kind biases /analyze toward OCR-tolerant prompt.
    if is_whiteboard:
        kind = "whiteboard"
    elif suffix == ".pdf":
        kind = "pdf"
    elif suffix in (".pptx", ".docx"):
        kind = "document"
    else:
        kind = "image"
    rec = _uploads.register(
        file_path=str(page_path),
        content_kind=kind,
        original_filename=file.filename,
        size_bytes=len(body),
        page_count=len(page_images),
        user_id=(user.id if user else None),
    )
    return {
        "upload_id": rec.id,
        "page_count": rec.page_count,
        "content_kind": rec.content_kind,
        "original_filename": rec.original_filename,
        "next_step": "POST /api/uploads/{id}/analyze",
    }


@app.post("/api/uploads/{upload_id}/analyze")
def analyze_upload(
    upload_id: str,
    user: AuthUser | None = Depends(current_user),
):
    """Step 2 of the PRD §13 contract. Run Claude vision on the
    upload's first page → detected_topic / detected_subject /
    detected_grade / detected_language / suggested_modes.

    Result is persisted on the `uploads` row so repeat calls return
    instantly (cached). The Studio UI calls this between Source and
    Customize so the form can preselect language + grade + suggested
    modes.
    """
    rec = _uploads.get(upload_id)
    if rec is None:
        raise HTTPException(404, "upload not found")
    if user and rec.user_id and rec.user_id != user.id:
        raise HTTPException(403, "upload belongs to another user")

    if rec.analysis_json:
        _uploads.touch(upload_id)
        return {
            "upload_id": rec.id,
            "cached": True,
            **rec.analysis_json,
        }

    page_path = Path(rec.file_path)
    if not page_path.exists():
        raise HTTPException(410, "uploaded file no longer on disk (retention)")

    try:
        analysis = _uploads.analyze_via_claude(
            page_path, content_kind=rec.content_kind,
        )
    except Exception as e:
        raise HTTPException(502, f"content analysis failed: {e}")
    _uploads.set_analysis(upload_id, analysis)
    return {"upload_id": rec.id, "cached": False, **analysis}


@app.get("/api/uploads/{upload_id}")
def get_upload(
    upload_id: str,
    user: AuthUser | None = Depends(current_user),
):
    """Look up a previously-created upload — useful for the Studio
    when the user reloads the page mid-flow."""
    rec = _uploads.get(upload_id)
    if rec is None:
        raise HTTPException(404, "upload not found")
    if user and rec.user_id and rec.user_id != user.id:
        raise HTTPException(403, "upload belongs to another user")
    return {
        "upload_id": rec.id,
        "page_count": rec.page_count,
        "content_kind": rec.content_kind,
        "original_filename": rec.original_filename,
        "status": rec.status,
        "analysis": rec.analysis_json,
        "created_at": rec.created_at,
    }


@app.get("/api/v2/video-requests/{request_id}/status")
def v2_request_status(request_id: str):
    """PRD §13.4 — Track generation with per-step progress."""
    job = store.get(request_id)
    if not job:
        raise HTTPException(404, "request not found")
    out = _progress_for_job(job)
    out["video_request_id"] = request_id
    if job.error:
        out["error"] = job.error
    return out


@app.get("/api/v2/video-requests/{request_id}/result")
def v2_request_result(request_id: str):
    """PRD §13.5 — Fetch the final artifacts + actions."""
    job = store.get(request_id)
    if not job:
        raise HTTPException(404, "request not found")
    if job.status != "succeeded":
        raise HTTPException(
            409, f"request not ready (status={job.status})",
        )
    r = job.result or {}
    profile_dict = job.payload.get("profile_json")
    return {
        "video_request_id": request_id,
        "video_url": r.get("video_url") or f"/jobs/{request_id}/video",
        "thumbnail_url": r.get("thumbnail_url"),
        # PRD §11 output_assets contract — every artifact addressable
        # separately so apps can pull only what they need (e.g. audio
        # for walk-to-school mode, srt for the classroom projector).
        "subtitle_url":     r.get("subtitle_url")     or f"/jobs/{request_id}/subtitles.srt",
        "subtitle_vtt_url": r.get("subtitle_vtt_url") or f"/jobs/{request_id}/subtitles.vtt",
        "audio_url":    r.get("audio_url")    or f"/jobs/{request_id}/audio.mp3",
        "lesson_id": r.get("lesson_id"),
        "chat_endpoint": (
            f"/chat/{r['lesson_id']}" if r.get("lesson_id") else None
        ),
        "profile": profile_dict,
        # PRD §13.5 — actions surface
        "actions": [
            "ask_doubt",
            "make_easier",
            "make_advanced",
            "change_language",
            "shorten",
            "exam_focused",
            "create_short",
            "download",
            "share",
        ],
    }


@app.post("/api/v2/video-requests/{request_id}/regenerate", status_code=202)
def v2_regenerate(
    request_id: str,
    change: str = Form(..., description="make_easier|make_advanced|change_language|shorten|exam_focused|create_short"),
    language: str | None = Form(None),
    duration_seconds: int | None = Form(None),
    user: AuthUser | None = Depends(current_user),
):
    """PRD §13.6 — Linked regeneration with structured change intent.

    Reuses the upstream Lesson JSON cache when possible — so a
    `change_language` regen only pays for translation+TTS+render, not
    a fresh Claude vision call.
    """
    parent_job = store.get(request_id)
    if not parent_job:
        raise HTTPException(404, "request not found")

    parent_profile_dict = parent_job.payload.get("profile_json")
    if not parent_profile_dict:
        raise HTTPException(409,
            "parent request was created before v0.6.0 — no profile to extend; "
            "create a new POST /api/v2/video-requests instead")

    parent_profile = _profile_from_dict(parent_profile_dict)
    try:
        new_profile = apply_regenerate(
            parent_profile, change,  # type: ignore[arg-type]
            new_language=language,
            new_duration_seconds=duration_seconds,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Clone the parent payload, swap in the new profile + derived fields
    payload = {**parent_job.payload}
    payload["profile_json"] = _profile_to_dict(new_profile)
    payload["language"] = new_profile.language_code
    payload["render_mode"] = new_profile.render_mode
    if "include_quiz" in payload:
        payload["include_quiz"] = new_profile.needs_quiz
    if "level" in payload:
        payload["level"] = _age_to_level(new_profile.age)
    payload["parent_request_id"] = request_id
    payload["regenerate_action"] = change

    job = runner.enqueue(payload)
    return JSONResponse(status_code=202, content={
        "video_request_id": job.id,
        "parent_request_id": request_id,
        "status": "queued",
        "change": change,
        "estimated_time_seconds": new_profile.duration_seconds,
        "profile": _profile_to_dict(new_profile),
    })


@app.get("/api/v2/video-modes")
def v2_list_video_modes():
    """PRD §4 — enumerate the 9 supported video modes for the UI."""
    return {
        "video_modes": [
            {
                "id": mode_id,
                "name": tmpl.name,
                "description": tmpl.description,
                "default_duration_seconds": tmpl.default_duration_seconds,
                "scene_count": len(tmpl.scene_beats),
                "needs_quiz": tmpl.needs_quiz,
                "needs_cta": tmpl.needs_cta,
            }
            for mode_id, tmpl in VIDEO_MODE_TEMPLATES.items()
        ],
        "user_types": list(USER_TYPE_TONE),
        "output_formats": list(OUTPUT_DIMENSIONS),
        "languages": sorted(SUPPORTED_LANGUAGES),
    }


# ============================================================================
# v0.9.0 — Organizations / School & Coaching portal (PRD §3.6)
# ----------------------------------------------------------------------------
# Institutional plan: a school owner creates an org, uploads a roster CSV,
# groups students into classes, and assigns videos to those classes. NOT
# a full School ERP (attendance / exams / fees / timetable are deferred
# per PRD §19) — this is the lightweight content-management slice.
# ============================================================================

from . import orgs as _orgs


# v2.0.3 — these three helpers moved to padhai/api_deps.py so router
# modules can import them cleanly. Aliases kept here for backward
# compatibility with the ~200 call sites in this file.
from . import api_deps as _api_deps
_require_user = _api_deps.require_user
_org_or_404 = _api_deps.org_or_404
_require_org_role = _api_deps.require_org_role


def _org_to_dict(org: _orgs.Org) -> dict:
    return {
        "id": org.id, "slug": org.slug, "name": org.name, "kind": org.kind,
        "board": org.board, "city": org.city,
        "contact_email": org.contact_email,
        "plan_tier": org.plan_tier, "owner_user_id": org.owner_user_id,
        "logo_url": org.logo_url, "created_at": org.created_at,
    }


@app.get("/api/orgs/me")
def list_my_orgs(user: AuthUser | None = Depends(current_user)):
    """Orgs the current user belongs to (any role). Returns an empty
    list for anonymous or unaffiliated users — the UI uses that to
    show the 'Create your school' first-time form."""
    if user is None:
        return {"orgs": []}
    out = [_org_to_dict(o) for o in _orgs.find_orgs_for_user(user.id)]
    return {"orgs": out}


@app.post("/api/orgs", status_code=201)
def create_my_org(
    name: str = Form(..., min_length=2, max_length=120),
    kind: str = Form("school"),
    board: str | None = Form(None),
    city: str | None = Form(None),
    contact_email: str | None = Form(None),
    user: AuthUser | None = Depends(current_user),
):
    """Create an organisation. The caller becomes the owner + first
    admin member automatically."""
    user = _require_user(user)
    try:
        org = _orgs.create_org(
            name=name, kind=kind, owner_user_id=user.id,
            board=board, city=city, contact_email=contact_email,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _org_to_dict(org)


@app.get("/api/orgs/{org_id}")
def get_org_detail(
    org_id: str,
    user: AuthUser | None = Depends(current_user),
):
    user = _require_user(user)
    org = _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher", "student"})
    return {
        "org": _org_to_dict(org),
        "stats": _orgs.org_stats(org_id),
        "my_role": _orgs.user_role_in_org(org_id=org_id, user_id=user.id),
    }


@app.get("/api/orgs/{org_id}/members")
def list_org_members(
    org_id: str,
    role: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    user: AuthUser | None = Depends(current_user),
):
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher"})
    members = _orgs.list_members(org_id, role=role, limit=limit)
    return {
        "members": [
            {
                "id": m.id, "user_id": m.user_id,
                "invited_email": m.invited_email,
                "role": m.role, "class_id": m.class_id,
                "display_name": m.display_name, "joined_at": m.joined_at,
            }
            for m in members
        ],
    }


@app.post("/api/orgs/{org_id}/members", status_code=201)
def add_org_member(
    org_id: str,
    request: Request,
    email: str = Form(..., min_length=4),
    role: str = Form("student"),
    class_id: str | None = Form(None),
    display_name: str | None = Form(None),
    user: AuthUser | None = Depends(current_user),
):
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin"})
    try:
        m = _orgs.add_member(
            org_id=org_id, role=role, invited_email=email,
            class_id=class_id, display_name=display_name,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    _audit.record(
        action="org.member.invite",
        org_id=org_id, actor_user_id=user.id,
        target_type="org_member", target_id=m.id,
        after={"role": m.role, "email": m.invited_email,
               "class_id": m.class_id, "display_name": m.display_name},
        **_audit.actor_from_request(request),
    )
    return {
        "id": m.id, "invited_email": m.invited_email, "role": m.role,
        "class_id": m.class_id, "display_name": m.display_name,
    }


@app.post("/api/orgs/{org_id}/roster", status_code=201)
def upload_org_roster(
    org_id: str,
    request: Request,
    csv: UploadFile = File(...),
    user: AuthUser | None = Depends(current_user),
):
    """Bulk CSV roster import. Required column: email. Optional: name,
    role, class. New classes referenced by name are auto-created.
    Returns counts so the UI can show "imported N, skipped M"."""
    user = _require_user(user)
    _rate_key = _rl.client_ip_from_request(request)
    if not _rl.file_upload.try_consume(_rate_key):
        raise HTTPException(429, "too many uploads — slow down")
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin"})
    suffix = Path(csv.filename or "roster.csv").suffix.lower()
    if suffix not in (".csv", ".tsv", ".txt"):
        raise HTTPException(400, "roster must be a CSV/TSV file")
    body = csv.file.read()
    if len(body) > 2 * 1024 * 1024:
        raise HTTPException(413, "CSV too large (limit 2 MB)")
    return _orgs.import_roster_csv(org_id=org_id, csv_bytes=body)


@app.get("/api/orgs/{org_id}/classes")
def list_org_classes_route(
    org_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    user: AuthUser | None = Depends(current_user),
):
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher", "student"})
    classes = _orgs.list_classes(org_id, limit=limit)
    return {
        "classes": [
            {
                "id": c.id, "name": c.name,
                "grade_level": c.grade_level, "section": c.section,
                "created_at": c.created_at,
            }
            for c in classes
        ],
    }


@app.post("/api/orgs/{org_id}/classes", status_code=201)
def create_org_class_route(
    org_id: str,
    name: str = Form(..., min_length=1),
    grade_level: str | None = Form(None),
    section: str | None = Form(None),
    user: AuthUser | None = Depends(current_user),
):
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher"})
    try:
        c = _orgs.add_class(
            org_id=org_id, name=name,
            grade_level=grade_level, section=section,
        )
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"id": c.id, "name": c.name, "grade_level": c.grade_level,
            "section": c.section}


@app.get("/api/orgs/{org_id}/assignments")
def list_org_assignments_route(
    org_id: str,
    class_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    user: AuthUser | None = Depends(current_user),
):
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher", "student"})
    items = _orgs.list_assignments(org_id, class_id=class_id, limit=limit)
    return {
        "assignments": [
            {
                "id": a.id, "class_id": a.class_id, "title": a.title,
                "topic": a.topic, "language": a.language, "level": a.level,
                "due_date": a.due_date, "notes": a.notes,
                "created_at": a.created_at,
            }
            for a in items
        ],
    }


@app.post("/api/orgs/{org_id}/assignments", status_code=201)
def create_org_assignment_route(
    org_id: str,
    class_id: str = Form(...),
    title: str = Form(..., min_length=2, max_length=120),
    topic: str = Form(..., min_length=2, max_length=200),
    language: str = Form("en"),
    level: str = Form("middle"),
    due_date: str | None = Form(None),
    notes: str | None = Form(None),
    user: AuthUser | None = Depends(current_user),
):
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher"})
    a = _orgs.create_assignment(
        org_id=org_id, class_id=class_id, title=title, topic=topic,
        language=language, level=level, due_date=due_date, notes=notes,
        created_by=user.id,
    )
    return {
        "id": a.id, "title": a.title, "topic": a.topic,
        "language": a.language, "level": a.level, "due_date": a.due_date,
        "class_id": a.class_id,
    }


# ---------- per-student analytics (E1) ----------

@app.post("/api/orgs/{org_id}/assignments/{aid}/completion")
def post_completion(
    org_id: str, aid: str,
    watch_pct: int | None = Form(None, ge=0, le=100),
    quiz_score: int | None = Form(None, ge=0, le=100),
    quiz_attempt: bool = Form(False),
    user: AuthUser | None = Depends(current_user),
):
    """Student progress beacon — called by the player every ~30s
    (timeupdate) and once at quiz finish. Idempotent on (aid, user).

    Students can only write their own completions; admins/teachers
    cannot fake progress for a student (use the grading API for that)."""
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher", "student"})
    c = _orgs.record_completion(
        assignment_id=aid, user_id=user.id,
        watch_pct=watch_pct, quiz_score=quiz_score,
        quiz_attempt=quiz_attempt,
    )
    return {
        "assignment_id": c.assignment_id, "watch_pct": c.watch_pct,
        "quiz_score": c.quiz_score, "quiz_attempts": c.quiz_attempts,
        "watched_at": c.watched_at, "updated_at": c.updated_at,
    }


@app.get("/api/orgs/{org_id}/assignments/{aid}/stats")
def get_assignment_stats(
    org_id: str, aid: str,
    user: AuthUser | None = Depends(current_user),
):
    """Per-assignment class rollup. Admin or teacher only — students
    don't see other students' scores."""
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher"})
    # Look up class_id from the assignment
    assignments = _orgs.list_assignments(org_id)
    a = next((x for x in assignments if x.id == aid), None)
    if a is None:
        raise HTTPException(404, "assignment not found")
    return _orgs.assignment_class_stats(aid, a.class_id)


@app.get("/api/orgs/{org_id}/students/{uid}/history")
def get_student_history(
    org_id: str, uid: str,
    user: AuthUser | None = Depends(current_user),
):
    """All assignments + completion state for one student.

    Access rules:
      - admin/teacher: can view any student in the org
      - student: can view ONLY their own history
    """
    user = _require_user(user)
    _org_or_404(org_id)
    my_role = _orgs.user_role_in_org(org_id=org_id, user_id=user.id)
    if my_role is None:
        raise HTTPException(403, "not a member of this org")
    if my_role == "student" and user.id != uid:
        raise HTTPException(403, "students may only view their own history")
    return {"assignments": _orgs.student_assignment_history(
        org_id=org_id, user_id=uid)}


# ---------- E2: Notifications (PRD §3.6) ----------

from . import notifications as _notifs


def _resolve_user_org_context(user: AuthUser) -> tuple[list[str], str, str | None]:
    """Return (org_ids, primary_role, primary_class_id) for the user.
    A user can belong to multiple orgs but we use the first one for
    role/class context (most users only have one)."""
    orgs = _orgs.find_orgs_for_user(user.id)
    if not orgs:
        return ([], "none", None)
    org_ids = [o.id for o in orgs]
    primary_role = _orgs.user_role_in_org(
        org_id=orgs[0].id, user_id=user.id,
    ) or "student"
    # Look up class membership in the primary org
    members = _orgs.list_members(orgs[0].id, role=primary_role)
    my_member = next((m for m in members if m.user_id == user.id), None)
    primary_class_id = my_member.class_id if my_member else None
    return (org_ids, primary_role, primary_class_id)


@app.get("/api/notifications/me")
def my_notifications(
    unread_only: bool = False,
    limit: int = 50,
    user: AuthUser | None = Depends(current_user),
):
    """The current user's notification feed across all their orgs."""
    user = _require_user(user)
    org_ids, role, class_id = _resolve_user_org_context(user)
    feed = _notifs.feed_for_user(
        user_id=user.id, user_role=role,
        user_class_id=class_id, org_ids=org_ids,
        unread_only=unread_only, limit=limit,
    )
    unread = _notifs.unread_count(
        user_id=user.id, user_role=role,
        user_class_id=class_id, org_ids=org_ids,
    )
    return {"notifications": feed, "unread_count": unread}


@app.post("/api/notifications/{nid}/read")
def mark_notification_read(
    nid: str,
    user: AuthUser | None = Depends(current_user),
):
    user = _require_user(user)
    _notifs.mark_read(notification_id=nid, user_id=user.id)
    return {"ok": True}


@app.post("/api/notifications/read-all")
def mark_all_read(user: AuthUser | None = Depends(current_user)):
    user = _require_user(user)
    org_ids, _, _ = _resolve_user_org_context(user)
    n = _notifs.mark_all_read(user_id=user.id, org_ids=org_ids)
    return {"marked": n}


@app.post("/api/orgs/{org_id}/notifications", status_code=201)
def create_org_notification(
    org_id: str,
    audience: str = Form(..., description="all | class:<id> | role:teacher | user:<id>"),
    kind: str = Form("announcement"),
    title: str = Form(..., min_length=2, max_length=120),
    body: str | None = Form(None),
    link_url: str | None = Form(None),
    send_at_iso: str | None = Form(
        None, description="ISO 8601; default = send now"),
    channels: str = Form("in_app"),
    user: AuthUser | None = Depends(current_user),
):
    """Compose + queue a notification. Admin or teacher only;
    teachers are scoped to audience that mentions their own class (the
    full enforcement lands in v0.12 with proper class-ownership tracking;
    for v0.11 we allow any class/role audience from teachers)."""
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher"})

    send_at: float | None = None
    if send_at_iso:
        try:
            from datetime import datetime
            send_at = datetime.fromisoformat(send_at_iso).timestamp()
        except ValueError:
            raise HTTPException(400, f"send_at_iso must be ISO 8601, got {send_at_iso!r}")
    try:
        n = _notifs.create(
            org_id=org_id, audience=audience, kind=kind,
            title=title, body=body, link_url=link_url,
            sent_by=user.id, send_at=send_at, channels=channels,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    # I3 — fan out push for everyone whose tokens we hold + who is
    # opted in to this notification's category. Best-effort: if push
    # isn't configured (FCM/APNs keys missing) the per-send log row
    # still gets written with failed_reason='no_provider' so admin
    # telemetry shows what would have been delivered.
    push_summary = {"delivered": 0, "failed": 0, "recipients": 0}
    try:
        recipients = _resolve_audience(org_id, n.audience)
        push_summary["recipients"] = len(recipients)
        for r in _push.fan_out_for_notification(n, recipients):
            push_summary["delivered"] += r.delivered
            push_summary["failed"] += r.failed
    except Exception as e:  # noqa: BLE001
        # Never let push failures block notification creation.
        _log.warning("[push] fan_out failed for notification %s: %s", n.id, e)
    return {
        "id": n.id, "title": n.title, "audience": n.audience,
        "kind": n.kind, "send_at": n.send_at, "push": push_summary,
    }


def _resolve_audience(org_id: str, audience: str) -> list[str]:
    """Map a notification audience string to concrete user_ids for
    push fan-out. Audience formats:
      'all'           → every org member who has a user_id
      'class:<cid>'   → members whose class_id matches
      'role:<role>'   → members with that role
      'user:<uid>'    → exactly that one user
    """
    if audience.startswith("user:"):
        return [audience.split(":", 1)[1]] if audience[5:] else []
    role_filter: str | None = None
    class_filter: str | None = None
    if audience == "all":
        pass
    elif audience.startswith("class:"):
        class_filter = audience.split(":", 1)[1]
    elif audience.startswith("role:"):
        role_filter = audience.split(":", 1)[1]
    else:
        return []
    members = _orgs.list_members(org_id, role=role_filter)
    return [
        m.user_id for m in members
        if m.user_id
        and (class_filter is None or m.class_id == class_filter)
    ]


@app.get("/api/orgs/{org_id}/notifications")
def list_org_notifications(
    org_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    user: AuthUser | None = Depends(current_user),
):
    """Admin view — all notifications in this org (sent + scheduled)."""
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher"})
    items = _notifs.list_for_admin(org_id=org_id, limit=limit)
    return {
        "notifications": [
            {
                "id": n.id, "audience": n.audience, "kind": n.kind,
                "title": n.title, "body": n.body, "link_url": n.link_url,
                "sent_by": n.sent_by, "send_at": n.send_at,
                "channels": n.channels, "created_at": n.created_at,
            }
            for n in items
        ],
    }


# ---------- E3: Attendance API ----------

@app.get("/api/orgs/{org_id}/classes/{cid}/attendance")
def get_class_attendance(
    org_id: str, cid: str,
    date: str,
    user: AuthUser | None = Depends(current_user),
):
    """Daily class roll for one date. Students appear in the response
    even if not yet marked (status: null) so the teacher UI can render
    the full roster.

    Access: admin, teacher (any), or the student themselves seeing
    only their own row."""
    user = _require_user(user)
    _org_or_404(org_id)
    my_role = _orgs.user_role_in_org(org_id=org_id, user_id=user.id)
    if my_role is None:
        raise HTTPException(403, "not a member of this org")
    roll = _orgs.class_attendance_for_date(cid, date)
    if my_role == "student":
        # Students see only their own row
        roll = [r for r in roll if r["user_id"] == user.id]
    return {"date": date, "class_id": cid, "students": roll}


@app.post("/api/orgs/{org_id}/classes/{cid}/attendance", status_code=201)
def post_class_attendance(
    org_id: str, cid: str,
    records_json: str = Form(..., description='JSON array of {user_id, date, status, notes?}'),
    user: AuthUser | None = Depends(current_user),
):
    """Bulk-mark attendance. Teachers + admins only."""
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher"})
    try:
        records = json.loads(records_json)
    except (ValueError, TypeError):
        raise HTTPException(400, "records_json must be valid JSON")
    if not isinstance(records, list):
        raise HTTPException(400, "records_json must be a JSON array")
    return _orgs.mark_attendance(
        org_id=org_id, class_id=cid,
        marked_by=user.id, records=records,
    )


@app.get("/api/orgs/{org_id}/students/{uid}/attendance")
def get_student_attendance(
    org_id: str, uid: str,
    from_date: str | None = None, to_date: str | None = None,
    user: AuthUser | None = Depends(current_user),
):
    """Per-student attendance record. Admin/teacher see anyone;
    students see only their own (parents inherit via E8 — not yet)."""
    user = _require_user(user)
    _org_or_404(org_id)
    my_role = _orgs.user_role_in_org(org_id=org_id, user_id=user.id)
    if my_role is None:
        raise HTTPException(403, "not a member of this org")
    if my_role == "student" and user.id != uid:
        raise HTTPException(403, "students may only view their own attendance")
    return {
        "user_id": uid,
        "records": _orgs.student_attendance_history(
            org_id=org_id, user_id=uid,
            from_date=from_date, to_date=to_date,
        ),
    }


@app.get("/api/orgs/{org_id}/classes/{cid}/attendance/summary")
def get_class_attendance_summary(
    org_id: str, cid: str,
    from_date: str | None = None, to_date: str | None = None,
    user: AuthUser | None = Depends(current_user),
):
    """Per-student rollup over a date range. Admin/teacher only."""
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher"})
    return _orgs.class_attendance_summary(
        class_id=cid, from_date=from_date, to_date=to_date,
    )


# ---------- E6: Timetable API ----------

@app.get("/api/orgs/{org_id}/classes/{cid}/timetable")
def get_class_timetable(
    org_id: str, cid: str,
    user: AuthUser | None = Depends(current_user),
):
    """Weekly grid for a class. Anyone in the org can read."""
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher", "student"})
    return {"slots": _orgs.class_timetable(cid)}


@app.post("/api/orgs/{org_id}/classes/{cid}/timetable", status_code=201)
def replace_class_timetable_route(
    org_id: str, cid: str,
    slots_json: str = Form(..., description='JSON array of {day_of_week, start_time, end_time, subject, teacher_user_id?, room?}'),
    user: AuthUser | None = Depends(current_user),
):
    """Atomic bulk-replace of a class's timetable. Admin or teacher.

    Format: JSON array. day_of_week 1-7 (1=Monday). start_time + end_time
    'HH:MM'. subject required. teacher_user_id + room optional.

    Returns {added: N, errors: [(row, reason)]} — partial success is
    fine; bad rows are reported individually."""
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher"})
    try:
        slots = json.loads(slots_json)
    except (ValueError, TypeError):
        raise HTTPException(400, "slots_json must be valid JSON")
    if not isinstance(slots, list):
        raise HTTPException(400, "slots_json must be a JSON array")
    return _orgs.replace_class_timetable(
        org_id=org_id, class_id=cid, slots=slots,
    )


@app.get("/api/orgs/{org_id}/today")
def get_today_for_user(
    org_id: str,
    user: AuthUser | None = Depends(current_user),
):
    """What's on for the current user today. Students see their class
    schedule; teachers see slots they're teaching. Both lists are
    merged + sorted by start_time."""
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher", "student"})
    return {"slots": _orgs.today_for_user(org_id=org_id, user_id=user.id)}


# ---------- E8: Parent ↔ child linking (DPDP §9) ----------

from . import parents as _parents


def _link_to_dict(link) -> dict:
    return {
        "id": link.id,
        "parent_user_id": link.parent_user_id,
        "child_user_id": link.child_user_id,
        "relation": link.relation,
        "initiated_by": link.initiated_by,
        "status": link.status,
        "consent_signed_at": link.consent_signed_at,
        "created_at": link.created_at,
    }


@app.post("/api/parents/link", status_code=201)
def create_parent_link(
    request: Request,
    other_email: str = Form(..., description="email of the other party (child if you're a parent; parent if you're a child)"),
    role: str = Form(..., description="'parent' or 'child' — your role in this link"),
    relation: str | None = Form(None),
    user: AuthUser | None = Depends(current_user),
):
    """Create a pending parent ↔ child link. The other party must
    verify via /auth/parent-link/verify?t=<token> before the link is
    activated.

    `role` describes the *caller's* role — set to 'parent' when a
    parent is inviting their child by email; set to 'child' when a
    college student is inviting their parent.
    """
    user = _require_user(user)
    if role not in ("parent", "child"):
        raise HTTPException(400, "role must be 'parent' or 'child'")
    if _get_user_repo() is None:
        raise HTTPException(503, "auth not configured — restart the server")
    other = _get_user_repo().find_by_email(other_email)
    if not other:
        raise HTTPException(404, f"no AI Pathshala account for {other_email}")
    other_user, _ = other

    if role == "parent":
        parent_uid, child_uid = user.id, other_user.id
        initiated_by = "parent"
    else:
        parent_uid, child_uid = other_user.id, user.id
        initiated_by = "child"

    try:
        link, token = _parents.invite(
            parent_user_id=parent_uid,
            child_user_id=child_uid,
            initiated_by=initiated_by,
            relation=relation,
        )
    except ValueError as e:
        raise HTTPException(409, str(e))

    # Queue an in-app notification for the OTHER party (the one who
    # must verify). Stays in the bell drawer until they click through.
    try:
        _notifs.create(
            org_id="parent-link",  # special-case "namespace" — feed
                                   # treats this audience normally
            audience=f"user:{other_user.id}",
            kind="system",
            title=(
                "Verify parent link" if initiated_by == "parent"
                else "Confirm child link"
            ),
            body=(
                f"{user.email} wants to link as your {role}. "
                "Click to verify."
            ),
            link_url=str(request.url_for("parent_link_verify")) + f"?t={token}",
            sent_by=user.id,
        )
    except Exception:
        # Notification failure shouldn't block the link creation —
        # the verify URL is still in the response.
        pass

    verify_url = str(request.url_for("parent_link_verify")) + f"?t={token}"
    # Log server-side only; never expose the token in the response body.
    _log.info("[parent_link] verify URL for link %s: %s", link.id, verify_url)
    return {
        **_link_to_dict(link),
        "audience": "child" if initiated_by == "parent" else "parent",
    }


@app.get("/auth/parent-link/verify", name="parent_link_verify",
         response_class=HTMLResponse)
def parent_link_verify(request: Request, t: str,
                       user: AuthUser | None = Depends(current_user)):
    """Single-use verification page. The acting user must be signed in
    (we need their user_id to match the expected audience)."""
    if user is None:
        return HTMLResponse(_consent_result_page(
            ok=False,
            message="Please sign in first, then click the verification link again.",
        ), status_code=401)
    ip = (request.headers.get("x-forwarded-for") or
          (request.client.host if request.client else "unknown")).split(",")[0].strip()
    try:
        link = _parents.verify(token=t, acting_user_id=user.id, acting_ip=ip)
    except ValueError as e:
        return HTMLResponse(_consent_result_page(
            ok=False, message=str(e),
        ), status_code=400)
    return HTMLResponse(_consent_result_page(
        ok=True,
        message=(
            f"You are now linked as the "
            f"{'parent' if link.parent_user_id == user.id else 'child'}. "
            "You can close this tab."
        ),
    ))


@app.post("/api/parents/link/{link_id}/revoke")
def revoke_parent_link(
    link_id: str,
    user: AuthUser | None = Depends(current_user),
):
    """Either side can revoke at any time. The consent audit trail
    stays (status='revoked' + revoked_at + revoked_by)."""
    user = _require_user(user)
    try:
        link = _parents.revoke(link_id=link_id, acting_user_id=user.id)
    except ValueError as e:
        raise HTTPException(403, str(e))
    return _link_to_dict(link)


@app.get("/api/parents/children")
def list_my_children(
    user: AuthUser | None = Depends(current_user),
):
    """All children linked to the current user (any status except revoked)."""
    user = _require_user(user)
    links = _parents.children_of(user.id)
    # Augment with child's email when available — saves the parent
    # a separate lookup. The user_repo isn't always wired (SQLite
    # dev mode), so tolerate None.
    out = []
    for link in links:
        child_email = None
        if _get_user_repo() is not None:
            child = _get_user_repo().find_by_id(link.child_user_id)
            child_email = child.email if child else None
        out.append({**_link_to_dict(link), "child_email": child_email})
    return {"links": out}


@app.get("/api/parents/me/parents")
def list_my_parents(
    user: AuthUser | None = Depends(current_user),
):
    """For a child user: which parents are linked to me."""
    user = _require_user(user)
    links = _parents.parents_of(user.id)
    out = []
    for link in links:
        parent_email = None
        if _get_user_repo() is not None:
            p = _get_user_repo().find_by_id(link.parent_user_id)
            parent_email = p.email if p else None
        out.append({**_link_to_dict(link), "parent_email": parent_email})
    return {"links": out}


@app.get("/api/parents/children/{child_user_id}/stats")
def get_child_stats(
    child_user_id: str,
    days: int = 7,
    user: AuthUser | None = Depends(current_user),
):
    """Parent-only view of a child's progress. Returns the same shape
    as /me/stats but requires verified parent_link first."""
    user = _require_user(user)
    if not _parents.is_verified_parent_of(user.id, child_user_id):
        raise HTTPException(
            403,
            "you are not a verified parent of this user. "
            "Use POST /api/parents/link to invite + the child must verify.",
        )
    # Delegate to the same stats logic /me/stats uses, scoped to the
    # child's user_id.
    return _compute_user_stats(child_user_id, days)


# ---------- E4: Exams + auto-grading (v0.15) ----------

def _exam_to_dict(e, *, include_answers: bool = False) -> dict:
    """Serialize an exam for API responses. When `include_answers=False`
    we strip the `answer` field from each question — that's the
    student-facing view (no peeking at the correct answer)."""
    questions = []
    for q in e.questions:
        clean = dict(q)
        if not include_answers:
            clean.pop("answer", None)
        questions.append(clean)
    return {
        "id": e.id, "org_id": e.org_id, "class_id": e.class_id,
        "title": e.title, "subject": e.subject, "topic": e.topic,
        "scheduled_at": e.scheduled_at, "duration_min": e.duration_min,
        "max_marks": e.max_marks, "status": e.status,
        "questions": questions, "created_at": e.created_at,
    }


def _attempt_to_dict(a) -> dict:
    return {
        "id": a.id, "exam_id": a.exam_id, "user_id": a.user_id,
        "started_at": a.started_at, "submitted_at": a.submitted_at,
        "answers": a.answers, "auto_score": a.auto_score,
        "manual_score": a.manual_score, "total_score": a.total_score,
        "feedback": a.feedback,
        "tab_blur_count": a.tab_blur_count,
        "fullscreen_exit_count": a.fullscreen_exit_count,
        "flags": a.flags,
    }


@app.post("/api/orgs/{org_id}/exams", status_code=201)
def create_org_exam(
    org_id: str,
    class_id: str = Form(...),
    title: str = Form(..., min_length=2, max_length=160),
    topic: str = Form(..., min_length=2, max_length=200),
    questions_json: str = Form(...,
        description="JSON array of {q, options:{A,B,C,D}, answer, marks, kind}"),
    duration_min: int = Form(30, ge=1, le=480),
    subject: str | None = Form(None),
    scheduled_at: float | None = Form(None),
    user: AuthUser | None = Depends(current_user),
):
    """Create an exam. The teacher provides the question set (in v0.15.1
    the UI will offer "AI-generate from topic" via the existing quiz
    generator, but that needs Claude in production — see padhai/pedagogy.py).

    Returns the exam with answers INCLUDED (teacher view)."""
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher"})
    try:
        questions = json.loads(questions_json)
    except (ValueError, TypeError):
        raise HTTPException(400, "questions_json must be valid JSON")
    if not isinstance(questions, list):
        raise HTTPException(400, "questions_json must be a JSON array")
    try:
        exam = _orgs.create_exam(
            org_id=org_id, class_id=class_id, title=title, topic=topic,
            questions=questions, duration_min=duration_min,
            subject=subject, scheduled_at=scheduled_at,
            created_by=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _exam_to_dict(exam, include_answers=True)


@app.get("/api/orgs/{org_id}/exams")
def list_org_exams(
    org_id: str,
    class_id: str | None = None,
    user: AuthUser | None = Depends(current_user),
):
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher", "student"})
    my_role = _orgs.user_role_in_org(org_id=org_id, user_id=user.id)
    exams = _orgs.list_exams(org_id, class_id=class_id)
    # Students see exams WITHOUT correct answers; teachers see all.
    include_answers = my_role in ("admin", "teacher")
    return {
        "exams": [_exam_to_dict(e, include_answers=include_answers)
                  for e in exams],
    }


@app.post("/api/orgs/{org_id}/exams/{eid}/begin")
def begin_exam(
    org_id: str, eid: str,
    user: AuthUser | None = Depends(current_user),
):
    """Student starts the exam — locks in the start_time so the timer
    can't be reset by refreshing. Idempotent: re-begin returns the
    same started_at, not a new clock."""
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"student", "admin", "teacher"})
    exam = _orgs.get_exam(eid)
    if not exam or exam.org_id != org_id:
        raise HTTPException(404, "exam not found")
    attempt = _orgs.begin_attempt(exam_id=eid, user_id=user.id)
    return {
        "attempt": _attempt_to_dict(attempt),
        "exam": _exam_to_dict(exam, include_answers=False),
        "deadline_at": attempt.started_at + exam.duration_min * 60,
    }


@app.post("/api/orgs/{org_id}/exams/{eid}/submit")
def submit_exam(
    org_id: str, eid: str,
    answers_json: str = Form(..., description='JSON object: {question_index_str: answer}'),
    tab_blur_count: int = Form(0, ge=0),
    fullscreen_exit_count: int = Form(0, ge=0),
    flags_json: str = Form("[]",
        description="JSON array of {t, at} anti-cheat event records"),
    user: AuthUser | None = Depends(current_user),
):
    """Student submits. Auto-grades MCQs immediately; free-form
    questions wait for teacher grading. Anti-cheat counters captured."""
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"student", "admin", "teacher"})
    try:
        answers = json.loads(answers_json)
        flags = json.loads(flags_json or "[]")
    except (ValueError, TypeError):
        raise HTTPException(400, "answers_json / flags_json must be valid JSON")
    try:
        attempt = _orgs.submit_attempt(
            exam_id=eid, user_id=user.id, answers=answers,
            tab_blur_count=tab_blur_count,
            fullscreen_exit_count=fullscreen_exit_count,
            flags=flags,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    return _attempt_to_dict(attempt)


@app.get("/api/orgs/{org_id}/exams/{eid}/attempts")
def list_exam_attempts(
    org_id: str, eid: str,
    limit: int = Query(default=200, ge=1, le=500),
    user: AuthUser | None = Depends(current_user),
):
    """All attempts on this exam — teacher review surface."""
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher"})
    attempts = _orgs.list_attempts(eid, limit=limit)
    return {"attempts": [_attempt_to_dict(a) for a in attempts]}


@app.post("/api/orgs/{org_id}/exams/{eid}/attempts/{aid}/grade")
def grade_exam_attempt(
    org_id: str, eid: str, aid: str,
    request: Request,
    manual_score: int = Form(..., ge=0),
    feedback: str | None = Form(None),
    user: AuthUser | None = Depends(current_user),
):
    """Teacher posts manual marks (for free-form questions). Recomputes
    total_score = auto + manual."""
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher"})
    try:
        attempt = _orgs.grade_attempt(
            attempt_id=aid, manual_score=manual_score, feedback=feedback,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    # High-value audit — manual grade override is a known fraud vector.
    _audit.record(
        action="org.exam.grade.override",
        org_id=org_id, actor_user_id=user.id,
        target_type="exam_attempt", target_id=aid,
        after={"exam_id": eid, "manual_score": manual_score,
               "feedback": feedback, "total_score": attempt.total_score},
        **_audit.actor_from_request(request),
    )
    return _attempt_to_dict(attempt)


# ---------- S4: Anti-cheating exam mode ----------

@app.get("/api/exam-mode/active")
def get_active_exam_mode(
    user: AuthUser | None = Depends(current_user),
):
    """Returns the exam_id of any in-progress attempt this user has,
    else None. Client polls this on the doubt-chat / voice-tutor
    pages to know whether to lock those features."""
    if user is None:
        return {"active_exam_id": None}
    return {"active_exam_id": _orgs.has_active_exam(user.id)}


# ---------- E5: Fees + invoicing (v0.16) ----------

from . import razorpay_client as _rzp


def _fee_struct_to_dict(s) -> dict:
    return {
        "id": s.id, "org_id": s.org_id, "name": s.name,
        "amount_paise": s.amount_paise, "amount_rupees": s.amount_paise / 100,
        "currency": s.currency, "applies_to": s.applies_to,
        "due_date": s.due_date, "notes": s.notes,
        "created_at": s.created_at,
    }


def _invoice_to_dict(inv) -> dict:
    return {
        "id": inv.id, "org_id": inv.org_id, "user_id": inv.user_id,
        "structure_id": inv.structure_id,
        "amount_paise": inv.amount_paise,
        "amount_rupees": inv.amount_paise / 100,
        "currency": inv.currency,
        "status": inv.status, "due_date": inv.due_date,
        "paid_at": inv.paid_at,
        "razorpay_order_id": inv.razorpay_order_id,
        "razorpay_payment_id": inv.razorpay_payment_id,
        "receipt_url": inv.receipt_url,
        "created_at": inv.created_at,
    }


@app.post("/api/orgs/{org_id}/fees/structures", status_code=201)
def create_org_fee_structure(
    org_id: str,
    name: str = Form(..., min_length=2, max_length=120),
    amount_paise: int = Form(..., ge=100,
        description="Amount in paise — ₹100 = 10000 paise"),
    applies_to: str = Form(...,
        description="'all' or 'class:<class_id>'"),
    due_date: str | None = Form(None, description="YYYY-MM-DD"),
    notes: str | None = Form(None),
    user: AuthUser | None = Depends(current_user),
):
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin"})
    try:
        s = _orgs.create_fee_structure(
            org_id=org_id, name=name, amount_paise=amount_paise,
            applies_to=applies_to, due_date=due_date, notes=notes,
            created_by=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _fee_struct_to_dict(s)


@app.get("/api/orgs/{org_id}/fees/structures")
def list_org_fee_structures(
    org_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    user: AuthUser | None = Depends(current_user),
):
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher"})
    return {
        "structures": [_fee_struct_to_dict(s)
                       for s in _orgs.list_fee_structures(org_id, limit=limit)],
    }


@app.post("/api/orgs/{org_id}/fees/structures/{sid}/generate",
          status_code=201)
def generate_fee_invoices(
    org_id: str, sid: str,
    user: AuthUser | None = Depends(current_user),
):
    """Bulk-create pending invoices for every student the structure
    applies to. Idempotent — UNIQUE(structure_id, user_id) skips
    already-invoiced students."""
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin"})
    try:
        return _orgs.generate_invoices_for_structure(structure_id=sid)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/orgs/{org_id}/fees/invoices")
def list_org_fee_invoices(
    org_id: str,
    status: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    user: AuthUser | None = Depends(current_user),
):
    """Admin/teacher see all invoices in the org. Students see only
    their own (used by the "Pay my fees" surface)."""
    user = _require_user(user)
    _org_or_404(org_id)
    my_role = _orgs.user_role_in_org(org_id=org_id, user_id=user.id)
    if my_role is None:
        raise HTTPException(403, "not a member of this org")
    # Students get auto-filtered to their own user_id
    user_filter = user.id if my_role == "student" else None
    invoices = _orgs.list_invoices(org_id, status=status, user_id=user_filter, limit=limit)
    return {"invoices": [_invoice_to_dict(i) for i in invoices]}


@app.get("/api/orgs/{org_id}/fees/summary")
def get_fee_summary(
    org_id: str,
    user: AuthUser | None = Depends(current_user),
):
    """Admin dashboard top-line numbers."""
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin"})
    return _orgs.fee_summary(org_id)


@app.post("/api/orgs/{org_id}/fees/invoices/{iid}/pay")
def init_invoice_payment(
    org_id: str, iid: str,
    user: AuthUser | None = Depends(current_user),
):
    """Student starts payment — we create a Razorpay order (or a mock
    when RAZORPAY_KEY_ID is unset) and return the checkout details
    the client needs to launch Razorpay's hosted page or web SDK."""
    user = _require_user(user)
    _org_or_404(org_id)
    inv = _orgs.get_invoice(iid)
    if not inv or inv.org_id != org_id:
        raise HTTPException(404, "invoice not found")
    if inv.user_id != user.id:
        my_role = _orgs.user_role_in_org(org_id=org_id, user_id=user.id)
        if my_role != "admin":
            raise HTTPException(403, "students may only pay their own invoices")
    if inv.status == "paid":
        return {"already_paid": True, "invoice": _invoice_to_dict(inv)}
    if inv.status in ("cancelled", "refunded"):
        raise HTTPException(409, f"cannot pay {inv.status} invoice")

    order = _rzp.create_order(
        amount_paise=inv.amount_paise,
        currency=inv.currency,
        receipt=f"inv_{inv.id[:12]}",
        notes={"invoice_id": inv.id, "user_id": inv.user_id},
    )
    _orgs.attach_razorpay_order(invoice_id=inv.id, order_id=order["id"])
    return {
        "invoice": _invoice_to_dict(_orgs.get_invoice(iid)),
        "razorpay_order": order,
        "razorpay_key_id": (_rzp._env("RAZORPAY_KEY_ID")
                            if _rzp.is_configured() else None),
        "mock": order.get("mock", False),
    }


@app.post("/api/orgs/{org_id}/fees/invoices/{iid}/confirm")
def confirm_invoice_payment(
    org_id: str, iid: str,
    razorpay_payment_id: str = Form(...),
    razorpay_order_id: str = Form(...),
    razorpay_signature: str = Form(...),
    user: AuthUser | None = Depends(current_user),
):
    """Client-side Razorpay Checkout callback — after the user pays,
    Razorpay returns the three handshake values. We verify the
    signature server-side, then mark the invoice paid.

    Mock orders auto-verify (always true) so dev/sandbox can drive
    the full flow without real keys."""
    user = _require_user(user)
    _org_or_404(org_id)
    inv = _orgs.get_invoice(iid)
    if not inv or inv.org_id != org_id:
        raise HTTPException(404, "invoice not found")
    if not _rzp.verify_payment_signature(
        order_id=razorpay_order_id,
        payment_id=razorpay_payment_id,
        signature=razorpay_signature,
    ):
        raise HTTPException(400, "signature verification failed")
    paid = _orgs.mark_invoice_paid(
        invoice_id=iid, razorpay_payment_id=razorpay_payment_id,
    )
    return _invoice_to_dict(paid)


@app.post("/api/webhooks/razorpay")
async def razorpay_webhook(request: Request):
    """Razorpay calls this when a payment completes. Verifies the
    HMAC signature header, parses the event, marks the invoice paid.

    Idempotent (mark_invoice_paid is). Multiple deliveries of the
    same event are safe."""
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not _rzp.verify_webhook_signature(body=body, signature=signature):
        raise HTTPException(401, "invalid webhook signature")
    try:
        event = json.loads(body)
    except (ValueError, TypeError):
        raise HTTPException(400, "invalid JSON")
    event_type = event.get("event", "")
    payload = event.get("payload", {})

    # --- Subscription tier upgrade on plan activation / renewal ---
    # Map Razorpay plan IDs → subscription tiers via env vars:
    #   RAZORPAY_PLAN_M2=plan_xxx  RAZORPAY_PLAN_M3=plan_yyy  etc.
    _PLAN_TIER_MAP: dict[str, str] = {}
    for _t in ("M2", "M3", "M4a", "M4b", "M4c", "M4d", "M4e"):
        _pid = os.environ.get(f"RAZORPAY_PLAN_{_t}", "")
        if _pid:
            _PLAN_TIER_MAP[_pid] = _t

    if event_type in ("subscription.activated", "subscription.charged"):
        sub_entity = (payload.get("subscription") or {}).get("entity", {})
        plan_id = sub_entity.get("plan_id", "")
        # Razorpay stores the notes.user_id or the subscriber_id we
        # set when creating the subscription.
        sub_user_id = (
            (sub_entity.get("notes") or {}).get("user_id")
            or sub_entity.get("customer_id")
        )
        new_tier = _PLAN_TIER_MAP.get(plan_id)
        if new_tier and sub_user_id:
            db_url = get_db_url()
            if db_url:
                try:
                    import psycopg as _pg
                    with _pg.connect(db_url, autocommit=True) as conn:
                        # Validate sub_user_id refers to a real user before
                        # trusting the client-supplied notes.user_id value.
                        exists = conn.execute(
                            "SELECT 1 FROM users WHERE id = %s", (sub_user_id,)
                        ).fetchone()
                        if not exists:
                            _log.warning(
                                "[razorpay_webhook] notes.user_id %s not found in DB "
                                "(plan %s) — ignoring", sub_user_id, plan_id,
                            )
                            return {"ignored": f"user {sub_user_id} not found"}
                        conn.execute(
                            "UPDATE users SET subscription_tier = %s WHERE id = %s",
                            (new_tier, sub_user_id),
                        )
                    _audit.record(
                        action="subscription.upgraded",
                        actor_user_id=sub_user_id,
                        note=f"tier={new_tier} plan_id={plan_id} event={event_type}",
                    )
                    _log.info(
                        "[razorpay_webhook] upgraded user %s to tier %s "
                        "(plan %s, event %s)", sub_user_id, new_tier, plan_id, event_type,
                    )
                    return {"user_id": sub_user_id, "tier": new_tier, "status": "upgraded"}
                except Exception as exc:
                    _log.error(
                        "[razorpay_webhook] tier upgrade failed for user %s: %s",
                        sub_user_id, exc,
                    )
                    raise HTTPException(500, "tier upgrade failed")
        return {"ignored": f"{event_type}: no matching plan or user"}

    if event_type == "subscription.cancelled":
        sub_entity = (payload.get("subscription") or {}).get("entity", {})
        sub_user_id = (
            (sub_entity.get("notes") or {}).get("user_id")
            or sub_entity.get("customer_id")
        )
        if sub_user_id:
            db_url = get_db_url()
            if db_url:
                try:
                    import psycopg as _pg
                    with _pg.connect(db_url, autocommit=True) as conn:
                        conn.execute(
                            "UPDATE users SET subscription_tier = 'M1' WHERE id = %s",
                            (sub_user_id,),
                        )
                    _audit.record(
                        action="subscription.downgraded",
                        actor_user_id=sub_user_id,
                        note=f"tier=M1 event={event_type}",
                    )
                    _log.info(
                        "[razorpay_webhook] downgraded user %s to M1 on cancellation",
                        sub_user_id,
                    )
                    return {"user_id": sub_user_id, "tier": "M1", "status": "downgraded"}
                except Exception as exc:
                    _log.error(
                        "[razorpay_webhook] tier downgrade failed for user %s: %s",
                        sub_user_id, exc,
                    )
                    # Re-raise so Razorpay gets a 500 and retries delivery —
                    # a silent 200 would leave the cancelled user on paid tier.
                    raise HTTPException(500, "tier downgrade failed — will retry")
        return {"ignored": f"{event_type}: no user_id in notes"}

    if event_type not in ("payment.captured", "order.paid"):
        return {"ignored": event_type}

    payment = (payload.get("payment") or {}).get("entity", {})
    order_id = payment.get("order_id")
    payment_id = payment.get("id")
    if not order_id:
        return {"ignored": "no order_id in payload"}
    inv = _orgs.find_invoice_by_order(order_id)
    if not inv:
        # Could be a payment for something other than a fee invoice —
        # not an error.
        return {"ignored": "no matching invoice"}
    _orgs.mark_invoice_paid(
        invoice_id=inv.id, razorpay_payment_id=payment_id,
    )
    return {"invoice_id": inv.id, "status": "paid"}


@app.get("/api/fees/config")
def get_fees_config():
    """Public — tells the client whether Razorpay is wired (so the
    UI can decide whether to show the real Checkout SDK or a
    "Pay via mock order" affordance for sandbox testing)."""
    return {
        "razorpay_configured": _rzp.is_configured(),
        "razorpay_key_id": (_rzp._env("RAZORPAY_KEY_ID")
                            if _rzp.is_configured() else None),
    }


# ---------- A2: Avatar router observability ----------

from . import avatar_router as _avatar_router


@app.get("/api/avatar-providers")
def list_avatar_providers():
    """Which photoreal avatar providers are configured on this deploy.
    Public — the UI uses it to surface which premium-tier options are
    available without revealing whether keys are set (just provider
    names + circuit health)."""
    snap = _avatar_router.snapshot()
    return {
        "configured": snap["configured"],
        "fallback_chain": snap["chain"],
    }


@app.get("/api/avatar-stats")
def get_avatar_stats(user: AuthUser | None = Depends(current_user)):
    """Per-provider success/failure counts + latency. Behind auth so
    we don't leak which keys are working to anonymous visitors.

    Useful for the admin dashboard to see "Synthesia is failing 30%
    of requests today — switch primary to Tavus."""
    user = _require_user(user)
    return _avatar_router.snapshot()


@app.post("/api/avatar-stats/reset")
def reset_avatar_stats(user: AuthUser | None = Depends(current_user)):
    """Clear the in-memory counters. Useful after fixing a provider
    issue so the "consecutive_failures" counter doesn't keep the
    circuit open."""
    user = _require_user(user)
    _avatar_router.reset_stats()
    return {"ok": True}


# ---------- E9: White-label branding ----------

def _branding_to_dict(b) -> dict:
    return {
        "brand_name": b.brand_name,
        "brand_color": b.brand_color,
        "brand_accent": b.brand_accent,
        "brand_logo_url": b.brand_logo_url,
        "brand_subdomain": b.brand_subdomain,
    }


@app.get("/api/branding/resolve")
def resolve_branding(request: Request):
    """Public endpoint — the SPA calls this on page load to pick up
    org-specific branding when served on a custom subdomain.

    Returns the platform defaults when there's no subdomain match,
    so the client can always assume a valid response."""
    host = (request.headers.get("x-forwarded-host") or
            request.url.netloc or "")
    branding = _branding.resolve_by_subdomain(host)
    if branding is None:
        branding = _branding.platform_default()
    return _branding_to_dict(branding)


_BRANDING_LOGO_DIR = Path(os.environ.get(
    "PADHAI_LOGO_DIR",
    str(Path.home() / ".padhai" / "logos"),
))


@app.post("/api/orgs/{org_id}/branding/logo")
def upload_org_logo(
    org_id: str,
    request: Request,
    logo: UploadFile = File(...),
    user: AuthUser | None = Depends(current_user),
):
    """Upload a logo. Stored under PADHAI_LOGO_DIR (local) or R2 when
    S3_BUCKET is configured. URL returned + persisted on the org.

    Size cap: 2 MB. Type: PNG / JPG / SVG / WebP only."""
    user = _require_user(user)
    _rate_key = _rl.client_ip_from_request(request)
    if not _rl.file_upload.try_consume(_rate_key):
        raise HTTPException(429, "too many uploads — slow down")
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin"})

    suffix = Path(logo.filename or "logo.png").suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".svg", ".webp"):
        raise HTTPException(400, "logo must be PNG/JPG/SVG/WebP")
    body = logo.file.read()
    if len(body) > 2 * 1024 * 1024:
        raise HTTPException(413, "logo too large (limit 2 MB)")

    # Filename includes org_id so multiple uploads from the same org
    # don't collide across orgs. We do replace within an org —
    # the latest upload wins; old logos linger on disk for retention.
    _BRANDING_LOGO_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"{org_id}_{int(time.time())}{suffix}"
    out_path = _BRANDING_LOGO_DIR / safe_name
    out_path.write_bytes(body)

    # Build a URL — local serving via /static/logos; R2 path skipped
    # in v0.17 for scope (E9.1 will add it).
    logo_url = f"/branding/logo/{safe_name}"

    b = _branding.update_branding(org_id=org_id, brand_logo_url=logo_url)
    return _branding_to_dict(b)


@app.get("/branding/logo/{filename}")
def serve_branding_logo(filename: str):
    """Public serving of uploaded logos. Filename is derived
    deterministically from org_id+timestamp, so no path traversal
    even without an extra check — but we validate anyway."""
    # Reject path traversal explicitly
    if "/" in filename or ".." in filename or filename.startswith("."):
        raise HTTPException(400, "invalid filename")
    path = _BRANDING_LOGO_DIR / filename
    if not path.exists():
        raise HTTPException(404, "logo not found")
    media = "image/png"
    if filename.endswith((".jpg", ".jpeg")):
        media = "image/jpeg"
    elif filename.endswith(".svg"):
        media = "image/svg+xml"
    elif filename.endswith(".webp"):
        media = "image/webp"
    return FileResponse(path, media_type=media)


# ---------- H3: Audit log — query + export ----------

def _audit_row_to_dict(r) -> dict:
    return {
        "id": r.id,
        "created_at": r.created_at,
        "action": r.action,
        "actor_user_id": r.actor_user_id,
        "actor_ip": r.actor_ip,
        "actor_ua": r.actor_ua,
        "org_id": r.org_id,
        "target_type": r.target_type,
        "target_id": r.target_id,
        "before": _safe_json_loads(r.before_json),
        "after": _safe_json_loads(r.after_json),
        "request_id": r.request_id,
        "note": r.note,
    }


def _safe_json_loads(s: str | None):
    if s is None:
        return None
    try:
        import json as _json
        return _json.loads(s)
    except (ValueError, TypeError):
        return s  # surface raw if it isn't valid JSON


# ---------- I3: Push notifications — register / prefs / log ----------


@app.post("/api/push/{log_id}/opened")
def mark_push_opened(log_id: str):
    """Client beacon: user tapped a push and the app deep-linked in.
    Drives the push open-rate metric on the admin dashboard.
    Unauthenticated (the log_id is the auth — opaque + per-send)."""
    ok = _push.mark_opened(log_id=log_id)
    return {"ok": ok}


@app.get("/api/push/log")
def list_push_log(
    user_id: str | None = None,
    limit: int = 100,
    user: AuthUser | None = Depends(current_user),
):
    """Diagnostic: recent push sends. Users can see only their own
    (user_id=me); admins (any role admin in any org) can pass an
    explicit user_id to query for another user."""
    user = _require_user(user)
    if user_id and user_id != user.id:
        # cross-user query → caller must be an admin in some org
        is_admin_anywhere = any(
            _orgs.user_role_in_org(org_id=o.id, user_id=user.id) == "admin"
            for o in _orgs.find_orgs_for_user(user.id)
        )
        if not is_admin_anywhere:
            raise HTTPException(403, "admin role required to query other users")
        target = user_id
    else:
        target = user.id
    rows = _push.recent_log(user_id=target, limit=limit)
    return {
        "rows": [
            {
                "id": r.id, "category": r.category, "platform": r.platform,
                "title": r.title, "body": r.body,
                "sent_at": r.sent_at, "delivered_at": r.delivered_at,
                "opened_at": r.opened_at, "failed_reason": r.failed_reason,
                "notification_id": r.notification_id,
            }
            for r in rows
        ],
    }


@app.get("/api/push/stats")
def push_stats(hours: float = 24.0):
    """Aggregate metrics for the last N hours. Public (drives the
    /status page); no PII in the response."""
    if hours <= 0 or hours > 24 * 30:
        raise HTTPException(400, "hours must be in (0, 720]")
    return _push.stats_for_period(hours=hours)


# ---------- J5: Adaptive difficulty (mastery model) ----------


# ---------- K4: Preschool (K-2 Kids Mode v2) ----------


# ---------- H7: GeM procurement (public SKU catalog) ----------


# ---------- K2: Country / SAARC ----------


# ---------- K3: Coaching (UPSC / JEE / NEET) ----------


@app.post("/api/coaching/tracks", status_code=201)
def create_coaching_track(
    exam: str = Form(...),
    name: str = Form(...),
    description: str | None = Form(None),
    subjects: str | None = Form(None, description="comma-separated"),
    target_year: int | None = Form(None),
    user: AuthUser | None = Depends(current_user),
):
    user = _require_user(user)
    subj_list = [s.strip() for s in (subjects or "").split(",") if s.strip()]
    try:
        t = _coaching.create_track(
            exam=exam, name=name, description=description,
            subjects=subj_list or None, target_year=target_year,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": t.id, "exam": t.exam, "name": t.name,
            "subjects": t.subjects}


# ---------- J3: Curriculum alignment scorer ----------


# ---------- J4: Voice provider catalog ----------


# ---------- K1: Indic rendering profile ----------


# ---------- J6: Question bank ----------


# ---------- H5: Custom domains ----------


# ---------- H6: SOC 2 evidence dashboard ----------


# ---------- G4: Region awareness ----------


# ---------- I4: Streaks + XP + leaderboards ----------


@app.get("/api/orgs/{org_id}/classes/{class_id}/leaderboard")
def class_leaderboard(
    org_id: str, class_id: str,
    period: str = "alltime",
    limit: int = 50,
    user: AuthUser | None = Depends(current_user),
):
    user = _require_user(user)
    _org_or_404(org_id)
    _require_org_role(org_id, user.id, {"admin", "teacher", "student"})
    members = _orgs.list_members(org_id)
    scope = [
        m.user_id for m in members
        if m.user_id and m.class_id == class_id
    ]
    if not scope:
        return {"rows": []}
    try:
        rows = _streaks.leaderboard(
            scope_user_ids=scope, period=period, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"rows": rows, "period": period}


# ---------- J1 + J2: Math + diagram preview surfaces ----------


# ---------- H1: SAML 2.0 SSO ----------

def _saml_acs_url(request: Request, org_id: str) -> str:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    return f"{scheme}://{host}/auth/saml/{org_id}/acs"


@app.get("/auth/saml/{org_id}/metadata")
def saml_sp_metadata(org_id: str, request: Request):
    """SP metadata XML the org admin uploads to their IdP. Public
    endpoint — metadata is non-sensitive by design."""
    cfg = _saml.get_config(org_id)
    if cfg is None or not cfg.enabled:
        raise HTTPException(404, "SAML not configured for this org")
    xml = _saml.sp_metadata_xml(
        cfg, acs_url=_saml_acs_url(request, org_id),
    )
    return Response(content=xml, media_type="application/xml")


@app.post("/auth/saml/{org_id}/acs")
def saml_acs(
    org_id: str,
    request: Request,
    SAMLResponse: str = Form(...),
    RelayState: str | None = Form(None),
):
    """Assertion Consumer Service. IdP POSTs the SAML response here
    after the user authenticates on the IdP side. We validate the
    assertion, extract attributes, and (a) create the user JIT if
    they don't exist, (b) join them to the org, (c) issue our JWT."""
    if not _saml.is_library_available():
        raise HTTPException(
            503,
            "SAML is not enabled on this deploy (python3-saml missing)",
        )
    cfg = _saml.get_config(org_id)
    if cfg is None or not cfg.enabled:
        raise HTTPException(404, "SAML not configured for this org")
    try:
        info = _saml.parse_assertion(
            config=cfg,
            saml_response_b64=SAMLResponse,
            request_url=_saml_acs_url(request, org_id),
        )
    except (RuntimeError, ValueError) as e:
        _audit.record(
            action="auth.saml.fail",
            org_id=org_id,
            target_type="org", target_id=org_id,
            note=str(e)[:200],
            **_audit.actor_from_request(request),
        )
        raise HTTPException(401, f"SAML response rejected: {e}")
    email = info.get("email")
    if not email:
        raise HTTPException(400, "SAML assertion missing email")
    # User provisioning: when JIT, we'd lookup-or-create here. For
    # v1.3 we surface the validated identity and let the existing
    # auth flow create the user (Postgres-only auth — same gate as
    # /auth/login).
    _audit.record(
        action="auth.saml.success",
        org_id=org_id,
        target_type="email", target_id=email,
        after={"name_id": info.get("name_id")},
        **_audit.actor_from_request(request),
    )
    if _get_user_repo() is None:
        return JSONResponse(
            {"email": email, "name": info.get("name"), "org_id": org_id,
             "saml_jit_pending": True,
             "next": "/auth/signup?prefill_email=" + email},
        )
    found = _get_user_repo().find_by_email(email)
    if found:
        user, _ph = found
        return JSONResponse(
            {"user_id": user.id, "email": user.email,
             "token": issue_token(user.id), "saml": True},
        )
    # JIT user creation is a v1.3.x follow-up — UserRepository needs a
    # `create_user(email, password_hash=None)` method which doesn't
    # exist yet. For now, return a JIT-pending marker so the frontend
    # can route the user to a SAML-aware signup form that pre-fills
    # email + skips password (SAML-only accounts have no local pwd).
    return JSONResponse(
        {"email": email, "name": info.get("name"), "org_id": org_id,
         "saml_jit_pending": True,
         "next": "/auth/signup?prefill_email=" + email + "&saml=1"},
    )


# ---------- H2: SCIM 2.0 provisioning ----------

def _scim_authenticate(request: Request) -> str:
    """Returns the org_id when the bearer token is valid; else raises
    401 with the SCIM 2.0 error shape."""
    auth_h = request.headers.get("authorization", "")
    if not auth_h.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    raw = auth_h.split(" ", 1)[1].strip()
    org_id = _scim.authenticate(raw)
    if not org_id:
        raise HTTPException(401, "invalid or revoked token")
    return org_id


@app.get("/scim/v2/ServiceProviderConfig")
def scim_spc():
    """SCIM 2.0 ServiceProviderConfig — IdPs read this at integration
    time to discover what we support."""
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "documentationUri": "https://aipathshala.in/docs/scim",
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 200},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {"name": "OAuth Bearer Token", "type": "oauthbearertoken",
             "description": "Per-org bearer issued by the org admin."}
        ],
    }


@app.get("/scim/v2/Users")
def scim_list_users(
    request: Request,
    startIndex: int = 1,
    count: int = 100,
    filter: str | None = None,
):
    """SCIM ListResponse of org members. Filter param accepts the
    SCIM filter syntax for `userName eq "..."` (the only operator
    IdPs actually use in practice). Other filters ignored."""
    org_id = _scim_authenticate(request)
    # Resolve users + members for the org. Real impl would page off
    # users + org_members JOIN; for v1.3 we list org_members (which
    # is what IdPs care about — the SCIM-managed cohort).
    members = _orgs.list_members(org_id)
    resources: list[dict] = []
    for m in members:
        # Synth a minimal user record from the org_member row when
        # we don't have a separate users-table backing.
        u_email = m.invited_email or ""
        if filter and "userName eq" in filter:
            wanted = filter.split('"')[1] if '"' in filter else ""
            if wanted and wanted.lower() != u_email.lower():
                continue
        # Synthesised "user" object for SCIM rendering.
        class _U:
            pass
        u = _U()
        u.id = m.id
        u.email = u_email
        u.scim_external_id = None
        u.deactivated_at = None
        u.created_at = m.joined_at or time.time()
        resources.append(_scim.user_to_scim(u, member=m))
    paged = resources[max(0, startIndex - 1):max(0, startIndex - 1) + count]
    return _scim.list_response(
        resources=paged, total=len(resources),
        start=startIndex, per_page=count,
    )


@app.post("/scim/v2/Users", status_code=201)
def scim_create_user(request: Request, payload: dict):
    """SCIM provisioning create. IdP POSTs a User resource; we
    add them to the org. Returns the persisted resource."""
    org_id = _scim_authenticate(request)
    try:
        info = _scim.parse_scim_user(payload)
    except ValueError as e:
        return JSONResponse(
            _scim.error_response(status=400, detail=str(e),
                                 scim_type="invalidValue"),
            status_code=400,
        )
    role = info["roles"][0] if info["roles"] else "student"
    m = _orgs.add_member(
        org_id=org_id, role=role,
        invited_email=info["email"],
        display_name=info["display_name"],
    )
    _audit.record(
        action="scim.user.create",
        org_id=org_id,
        target_type="org_member", target_id=m.id,
        after={"email": info["email"], "role": role,
               "external_id": info["external_id"]},
        **_audit.actor_from_request(request),
    )
    # Synth user for response
    class _U:
        pass
    u = _U()
    u.id = m.id
    u.email = info["email"]
    u.scim_external_id = info["external_id"]
    u.deactivated_at = None
    u.created_at = m.joined_at or time.time()
    return JSONResponse(
        _scim.user_to_scim(u, member=m), status_code=201,
    )


@app.patch("/scim/v2/Users/{member_id}")
def scim_patch_user(member_id: str, request: Request, payload: dict):
    """SCIM PATCH — usually used for deactivation
    (Operations: [{op:'replace', path:'active', value:false}])."""
    org_id = _scim_authenticate(request)
    ops = (payload or {}).get("Operations") or []
    for op in ops:
        if op.get("op", "").lower() == "replace" and op.get("path") == "active":
            if not op.get("value", True):
                # Soft-delete via the dedicated helper (v2.0.1).
                _orgs.deactivate_member(
                    org_id=org_id, member_id=member_id,
                )
                _audit.record(
                    action="scim.user.deactivate",
                    org_id=org_id,
                    target_type="org_member", target_id=member_id,
                    **_audit.actor_from_request(request),
                )
                return {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                        "id": member_id, "active": False}
    return JSONResponse(
        _scim.error_response(status=400, detail="no supported ops"),
        status_code=400,
    )


# ---------- H4: Data residency ----------


# ---------- D3: PWA — manifest + service worker ----------

@app.get("/manifest.json")
def pwa_manifest(request: Request):
    """Web App Manifest — picked up by browsers' "Install app" prompt
    and by the service worker. Branding-aware: when served via a
    custom subdomain (E9), the org's brand_name + brand_color get
    injected so the home-screen icon reads "St. Paul's" not
    "AI Pathshala"."""
    host = (request.headers.get("x-forwarded-host") or
            request.url.netloc or "")
    b = _branding.resolve_by_subdomain(host) or _branding.platform_default()
    return JSONResponse({
        "name": b.brand_name,
        "short_name": b.brand_name[:12],
        "description": "Multilingual AI teacher for every student",
        "start_url": "/",
        "display": "standalone",
        "orientation": "portrait-primary",
        "background_color": "#F5F7FA",
        "theme_color": b.brand_color,
        "scope": "/",
        "lang": "en",
        "icons": [
            # Branding logo at multiple sizes; falls back to a data URI
            # of a coloured tile when no custom logo is set so the
            # manifest is always valid.
            {
                "src": b.brand_logo_url or _default_logo_data_uri(b.brand_color),
                "sizes": "any",
                "type": "image/png",
                "purpose": "any",
            },
        ],
    })


def _default_logo_data_uri(color: str) -> str:
    """1x1 PNG with the brand color baked in. Browsers accept data URIs
    in the manifest; this means the PWA is installable even before the
    org uploads a real logo."""
    # Tiny 1x1 PNG (transparent base, color via CSS theme). For real
    # use the org uploads a proper 512x512 logo.
    return "data:image/svg+xml;utf8," + (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 192 192'>"
        f"<rect width='192' height='192' rx='36' fill='{color}'/>"
        f"<text x='50%' y='58%' font-family='sans-serif' font-size='110' "
        f"font-weight='700' fill='white' text-anchor='middle'>P</text>"
        f"</svg>"
    )


_SERVICE_WORKER_JS = """\
// AI Pathshala service worker — v1.0 D3
//
// Two cache layers:
//   shell-v1 : the SPA HTML + the static assets it needs to boot
//              offline (fonts, fallback icons)
//   media-v1 : generated videos + audio that the user has
//              explicitly "saved offline"
//
// Network-first for API calls — we never serve stale lesson data.
// Cache-first for video/audio media (heavy bytes; offline is the
// whole point of caching them).

const SHELL_CACHE = 'padhai-shell-v1';
const MEDIA_CACHE = 'padhai-media-v1';

self.addEventListener('install', (event) => {
  // Pre-cache the SPA shell. We don't list specific assets — the
  // first navigation populates the cache via the fetch handler.
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  // Clean up older versioned caches when we bump the version.
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => !k.endsWith('-v1')).map(k => caches.delete(k))
    )),
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Only handle same-origin requests; external (R2, fonts.googleapis)
  // are passed through unmodified.
  if (url.origin !== self.location.origin) return;

  // API calls + auth: network-first; if offline, surface a clear
  // JSON 503 so the UI can show "you're offline; try again later"
  // rather than the browser's own offline page.
  if (url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/auth/') ||
      url.pathname.startsWith('/chat/')) {
    event.respondWith(networkFirstJson(req));
    return;
  }

  // Video / audio / subtitle artifacts: cache-first.
  if (url.pathname.startsWith('/jobs/') &&
      (url.pathname.endsWith('/video') ||
       url.pathname.endsWith('.mp3') ||
       url.pathname.endsWith('.srt') ||
       url.pathname.endsWith('.vtt'))) {
    event.respondWith(cacheFirst(req, MEDIA_CACHE));
    return;
  }

  // SPA shell + everything else: cache-first with network fallback.
  event.respondWith(cacheFirst(req, SHELL_CACHE));
});

async function cacheFirst(req, cacheName) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(req);
  if (hit) return hit;
  try {
    const resp = await fetch(req);
    if (resp.ok) cache.put(req, resp.clone());
    return resp;
  } catch (e) {
    // No network + no cache → 504
    return new Response('Offline', { status: 504, statusText: 'Offline' });
  }
}

async function networkFirstJson(req) {
  try {
    return await fetch(req);
  } catch (e) {
    return new Response(
      JSON.stringify({ error: 'offline', detail: "You're offline — try again when you have a connection." }),
      { status: 503, headers: { 'Content-Type': 'application/json' } },
    );
  }
}

// Messages from the page — used to explicitly cache a lesson's
// video for offline viewing ("Save offline" button).
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SAVE_OFFLINE') {
    const urls = event.data.urls || [];
    event.waitUntil(
      caches.open(MEDIA_CACHE).then(cache => Promise.all(
        urls.map(u => fetch(u).then(r => r.ok && cache.put(u, r.clone())))
      )),
    );
  }
});
"""


@app.get("/sw.js")
def service_worker():
    """Service worker JS. Served from the app root so its scope can
    cover everything under /."""
    from fastapi.responses import Response
    return Response(
        content=_SERVICE_WORKER_JS,
        media_type="application/javascript",
        headers={
            # Browser must re-check this file on every page load so
            # SW updates roll out quickly. The SW itself is small.
            "Cache-Control": "no-cache",
            "Service-Worker-Allowed": "/",
        },
    )


# ---- profile (de)serialization helpers ------------------------------------

def _profile_to_dict(p: PersonalizationProfile) -> dict:
    """Serialize a profile to a JSON-safe dict for job payload storage."""
    return {
        "video_mode": p.video_mode,
        "duration_seconds": p.duration_seconds,
        "output_format": p.output_format,
        "output_dimensions": list(p.output_dimensions),
        "render_tier": p.render_tier,
        "render_mode": p.render_mode,
        "needs_quiz": p.needs_quiz,
        "needs_cta": p.needs_cta,
        "user_type": p.user_type,
        "age": p.age,
        "grade": p.grade,
        "pedagogy_profile": p.pedagogy_profile,
        "language_code": p.language_code,
        "tone": p.tone,
        "narration_density": p.narration_density,
        "scene_beats": list(p.scene_beats),
        "prompt_addendum": p.prompt_addendum,
        "sensitive_domain": p.sensitive_domain,
        "disclaimer_text": p.disclaimer_text,
    }


def _profile_from_dict(d: dict) -> PersonalizationProfile:
    return PersonalizationProfile(
        video_mode=d["video_mode"],
        duration_seconds=d["duration_seconds"],
        output_format=d["output_format"],
        output_dimensions=tuple(d["output_dimensions"]),
        render_tier=d["render_tier"],
        render_mode=d["render_mode"],
        needs_quiz=d["needs_quiz"],
        needs_cta=d["needs_cta"],
        user_type=d["user_type"],
        age=d["age"],
        grade=d["grade"],
        pedagogy_profile=d["pedagogy_profile"],
        language_code=d["language_code"],
        tone=d["tone"],
        narration_density=d["narration_density"],
        scene_beats=tuple(d["scene_beats"]),
        prompt_addendum=d["prompt_addendum"],
        sensitive_domain=d.get("sensitive_domain"),
        disclaimer_text=d.get("disclaimer_text"),
    )


def _age_to_level(age: int) -> str:
    """Map age → existing `level` enum so we can route through the
    current `generate_lesson()` while v2.1 lands native profile support."""
    if age <= 6:   return "kg"
    if age <= 10:  return "primary"
    if age <= 13:  return "middle"
    if age <= 18:  return "secondary"
    return "neet_jee"


@app.post("/live/respond")
def live_respond(
    transcript: str = Form(..., min_length=1, max_length=2000),
    history_json: str = Form("[]"),
    user: AuthUser | None = Depends(current_user),
):
    """One turn of the Live Lecture loop.

    The browser does ASR (Web Speech API) and TTS (speechSynthesis) so
    we only handle the reasoning step here — cheap, fast, no audio
    bytes on the wire. Returns 2-4 sentence reply text; the client
    speaks it through speechSynthesis."""
    import json as _json
    from .pedagogy import live_tutor_reply

    try:
        history = _json.loads(history_json) if history_json else []
        if not isinstance(history, list):
            history = []
    except _json.JSONDecodeError:
        history = []
    # cap history to last 8 turns to keep prompts small
    history = history[-8:]
    try:
        reply = live_tutor_reply(transcript, history=history)
    except Exception as e:
        err = str(e)
        if "api_key" in err.lower() or "authentication" in err.lower() or "ANTHROPIC" in err:
            raise HTTPException(503, "AI service not configured — set ANTHROPIC_API_KEY")
        raise HTTPException(500, f"AI error: {err}")
    return {"transcript": transcript, "reply": reply}


@app.post("/voice/respond")
def voice_respond(
    request: Request,
    transcript: str = Form(..., min_length=1, max_length=2000),
    history_json: str = Form("[]"),
    lesson_id: str = Form(""),
    user: AuthUser | None = Depends(current_user),
):
    """One turn of the Voice Tutor loop.

    Optional `lesson_id` grounds the reply in the cached lesson (same
    as the text Doubt Chat). Without a lesson_id it behaves like the
    Live Lecture endpoint but with the VOICE_TUTOR_SYSTEM prompt.
    Browser handles ASR + TTS; we only handle the reasoning step."""
    import json as _json
    import dataclasses
    from .pedagogy import voice_tutor_reply

    _rate_key = user.id if user else _rl.client_ip_from_request(request)
    if not _rl.ai_generation.try_consume(_rate_key):
        raise HTTPException(429, "Too many requests — please wait before asking again.")

    try:
        history = _json.loads(history_json) if history_json else []
        if not isinstance(history, list):
            history = []
    except _json.JSONDecodeError:
        history = []
    history = history[-8:]

    lesson_json: str | None = None
    if lesson_id and lesson_id.strip():
        cached = cache.get_lesson_by_key(lesson_id.strip())
        if cached is not None:
            lesson_json = _json.dumps(dataclasses.asdict(cached), ensure_ascii=False)

    try:
        reply = voice_tutor_reply(transcript, history=history, lesson_json=lesson_json)
    except Exception as e:
        err = str(e)
        if "api_key" in err.lower() or "authentication" in err.lower() or "ANTHROPIC" in err:
            raise HTTPException(503, "AI service not configured — set ANTHROPIC_API_KEY")
        raise HTTPException(500, f"AI error: {err}")
    return {"transcript": transcript, "reply": reply, "lesson_grounded": lesson_json is not None}


@app.get("/jobs")
def list_jobs(
    limit: int = 20,
    user: AuthUser | None = Depends(current_user),
):
    """Newest-first list of jobs. Authenticated users see only their
    own jobs; anonymous browsing returns recent public jobs (capped to
    last 5) so a fresh user has *something* to look at in the Library.
    Limit clamped to 50."""
    limit = max(1, min(50, limit))
    if user is not None:
        jobs = store.recent_jobs(limit=limit, filter_user_id=user.id)
    else:
        # anonymous: show 5 most recent public jobs so the Library
        # doesn't look broken on a fresh deploy
        jobs = store.recent_jobs(limit=min(5, limit), filter_user_id=None)
    out = []
    for j in jobs:
        r = j.result or {}
        out.append({
            "id": j.id,
            "status": j.status,
            "created_at": j.created_at,
            "language": j.payload.get("language"),
            "level": j.payload.get("level"),
            "cache_hit": r.get("cache_hit"),
            "lesson_id": r.get("lesson_id") if j.status == "succeeded" else None,
            "video_url": f"/jobs/{j.id}/video" if j.status == "succeeded" else None,
        })
    return {"jobs": out, "count": len(out), "authenticated": user is not None}


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    result = job.result or {}
    return {
        "id": job.id,
        "status": job.status,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "cache_hit": result.get("cache_hit"),
        "error": job.error,
        "video_url": f"/jobs/{job.id}/video" if job.status == "succeeded" else None,
        "direct_url": result.get("video_url") if job.status == "succeeded" else None,
        # lesson_id (chat lookup key) — surfaced so the client can
        # POST /chat/{lesson_id} without re-uploading the source image.
        "lesson_id": result.get("lesson_id") if job.status == "succeeded" else None,
        # Mirror the result block for clients that want it nested.
        "result": result if job.status == "succeeded" else None,
    }


@app.get("/jobs/{job_id}/video")
def get_job_video(job_id: str, request: Request):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job.status == "failed":
        raise HTTPException(500, job.error or "job failed")
    if job.status != "succeeded":
        raise HTTPException(
            409, f"job not done yet (status={job.status}); poll /jobs/{job_id}",
        )
    # G3 — when CDN is configured (and this isn't a CDN-origin fetch),
    # 302 to a signed URL with 24h TTL. The CDN edge serves the cached
    # MP4 from Cloudflare's India PoPs; this origin only sees fetches
    # on cache miss + edge revalidation.
    cdn_url = _cdn.maybe_redirect(f"/jobs/{job_id}/video", request=request)
    if cdn_url:
        return RedirectResponse(cdn_url, status_code=302)
    result = job.result or {}
    url = result.get("video_url")
    if url:
        if url.startswith("file://"):
            return FileResponse(url[7:], media_type="video/mp4", filename="lesson.mp4")
        return RedirectResponse(url, status_code=302)
    # legacy path — older jobs may carry output_path instead of video_url
    output_path = Path(result.get("output_path", ""))
    if output_path.exists():
        return FileResponse(output_path, media_type="video/mp4", filename="lesson.mp4")
    raise HTTPException(500, "output missing")


def _locate_mp4(job: "Job") -> Path:
    """Return the path to the rendered MP4 on local disk, downloading
    it from object storage if needed. Used by the sidecar artifact
    endpoints (audio.mp3, subtitles.srt) which need direct file access."""
    if job.status != "succeeded":
        raise HTTPException(409, f"job not done yet (status={job.status})")
    local = _OUTPUT_DIR / f"{job.id}.mp4"
    if local.exists():
        return local
    result = job.result or {}
    url = result.get("video_url", "")
    if url.startswith("file://") and Path(url[7:]).exists():
        return Path(url[7:])
    raise HTTPException(404, "video file not available on disk")


@app.get("/jobs/{job_id}/audio.mp3")
def get_job_audio(job_id: str, request: Request):
    """Audio-only export of a generated lesson. PRD §8.6/§11 — the
    output_assets contract surfaces audio_url separately from the
    composite MP4 so listeners (walk-to-school audio mode) can save
    bandwidth. Extracted on demand from the cached MP4 via ffmpeg."""
    import subprocess
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    cdn_url = _cdn.maybe_redirect(f"/jobs/{job_id}/audio.mp3", request=request)
    if cdn_url:
        return RedirectResponse(cdn_url, status_code=302)
    mp4 = _locate_mp4(job)
    audio_path = _OUTPUT_DIR / f"{job_id}.mp3"
    if not audio_path.exists():
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(mp4),
                "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", "128k",
                str(audio_path),
            ],
            check=True, capture_output=True,
        )
    return FileResponse(audio_path, media_type="audio/mpeg", filename="lesson.mp3")


@app.get("/jobs/{job_id}/subtitles.srt")
def get_job_subtitles(job_id: str, request: Request):
    """SRT subtitle export. PRD §11 output_assets.subtitle_url.

    Built from the cached Lesson scenes — each scene's narration becomes
    one subtitle block, timed by reading per-scene audio durations from
    the cache (the same MP3s the renderer used). Fully reconstructable;
    no need to re-run TTS."""
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    cdn_url = _cdn.maybe_redirect(f"/jobs/{job_id}/subtitles.srt", request=request)
    if cdn_url:
        return RedirectResponse(cdn_url, status_code=302)
    result = job.result or {}
    lesson_id = result.get("lesson_id")
    if not lesson_id:
        raise HTTPException(404, "lesson_id not in job result")
    cached = cache.get_lesson_by_key(lesson_id)
    if cached is None:
        raise HTTPException(404, "lesson not in cache; cannot rebuild subtitles")

    srt_path = _OUTPUT_DIR / f"{job_id}.srt"
    if not srt_path.exists():
        srt_path.write_text(_build_srt(cached), encoding="utf-8")
    return FileResponse(srt_path, media_type="application/x-subrip",
                        filename="lesson.srt")


def _build_srt(lesson, vtt: bool = False) -> str:
    """Approximate-timing SRT (or WebVTT) from a Lesson. Each scene gets
    one cue sized by its narration's word count at ~140 wpm. Good enough
    for accessibility / WhatsApp share / classroom display / native
    <video><track> rendering.

    `vtt=True` switches to WebVTT format (browser-native): `.` instead
    of `,` for milliseconds + a `WEBVTT` header line.
    """
    sep = "." if vtt else ","
    def fmt(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t - (h * 3600 + m * 60)
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", sep)

    cursor = 0.0
    parts = []
    for i, scene in enumerate(lesson.scenes, start=1):
        words = max(3, len(scene.narration.split()))
        duration = max(2.5, (words / 140.0) * 60.0 + 0.5)
        start = cursor
        end = cursor + duration
        cursor = end + 0.2
        parts.append(
            f"{i}\n{fmt(start)} --> {fmt(end)}\n"
            f"{scene.narration.strip()}\n"
        )
    body = "\n".join(parts)
    if vtt:
        return "WEBVTT\n\n" + body
    return body


@app.get("/jobs/{job_id}/subtitles.vtt")
def get_job_subtitles_vtt(job_id: str, request: Request):
    """WebVTT version of /jobs/{id}/subtitles.srt — the format the
    HTML5 <video><track> element actually understands across browsers.
    Used by the Studio player to expose the PRD §15 subtitles toggle."""
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    cdn_url = _cdn.maybe_redirect(f"/jobs/{job_id}/subtitles.vtt", request=request)
    if cdn_url:
        return RedirectResponse(cdn_url, status_code=302)
    result = job.result or {}
    lesson_id = result.get("lesson_id")
    if not lesson_id:
        raise HTTPException(404, "lesson_id not in job result")
    cached = cache.get_lesson_by_key(lesson_id)
    if cached is None:
        raise HTTPException(404, "lesson not in cache; cannot rebuild subtitles")

    vtt_path = _OUTPUT_DIR / f"{job_id}.vtt"
    if not vtt_path.exists():
        vtt_path.write_text(_build_srt(cached, vtt=True), encoding="utf-8")
    return FileResponse(vtt_path, media_type="text/vtt",
                        filename="lesson.vtt")


# ============================================================================
# v3.20 — Student-facing UI screens + missing API endpoints
#
# Implements:
#   • Page routes for the core study loop (lesson generator, video player,
#     flashcards, quiz, chat, profile, teacher home, parent portal)
#   • GET+PUT /api/me/profile   — user preferences (language/level/mode)
#   • GET     /api/flashcards/due + /api/flashcards/decks
#   • POST    /api/flashcards/{card_id}/review   — SM-2 grade submission
#   • GET     /api/quiz/{lesson_id}              — GET alias for quiz data
#   • POST    /auth/forgot-password + /auth/reset-password
#   • POST    /auth/change-password
#   • GET     /api/doubts/queue
# ============================================================================


# ---- Page routes -----------------------------------------------------------
# NOTE: /lessons/new MUST be registered before /lessons/{job_id} so FastAPI
# matches the literal path first.

@app.get("/lessons/new", response_class=HTMLResponse)
def lesson_new_page() -> HTMLResponse:
    """Lesson generator screen — upload → options → generate → watch."""
    from . import ui_pages as _ui
    return HTMLResponse(_ui.get_lesson_new_html())


@app.get("/lessons/{job_id}", response_class=HTMLResponse)
def lesson_player_page(job_id: str) -> HTMLResponse:
    """Video player screen with tabs: quiz, chat, flashcards, notes, recap."""
    from . import ui_pages as _ui
    return HTMLResponse(_ui.get_lesson_player_html())


@app.get("/flashcards", response_class=HTMLResponse)
def flashcards_page() -> HTMLResponse:
    """SM-2 flashcard study screen — due queue, flip, rate."""
    from . import ui_pages as _ui
    return HTMLResponse(_ui.get_flashcards_html())


@app.get("/quiz", response_class=HTMLResponse)
def quiz_page() -> HTMLResponse:
    """Standalone quiz screen — reads ?lesson= from query string."""
    from . import ui_pages as _ui
    return HTMLResponse(_ui.get_quiz_html())


@app.get("/chat", response_class=HTMLResponse)
def chat_page() -> HTMLResponse:
    """AI Tutor chat screen — reads ?lesson= from query string."""
    from . import ui_pages as _ui
    return HTMLResponse(_ui.get_chat_html())


@app.get("/profile", response_class=HTMLResponse)
def profile_page() -> HTMLResponse:
    """User preferences and account settings."""
    from . import ui_pages as _ui
    return HTMLResponse(_ui.get_profile_html())


@app.get("/teacher", response_class=HTMLResponse)
def teacher_page() -> HTMLResponse:
    """Teacher home — classes, assignments, doubt queue."""
    from . import ui_pages as _ui
    return HTMLResponse(_ui.get_teacher_html())


@app.get("/parent", response_class=HTMLResponse)
def parent_page() -> HTMLResponse:
    """Parent portal — child progress, fee payment."""
    from . import ui_pages as _ui
    return HTMLResponse(_ui.get_parent_html())


@app.get("/onboarding", response_class=HTMLResponse)
def onboarding_page() -> HTMLResponse:
    """Multi-step student onboarding wizard. Drives the
    /api/onboarding/* endpoints declared in padhai/routers/onboarding.py."""
    return HTMLResponse(_ONBOARDING_HTML)


@app.get("/dashboard", response_class=HTMLResponse)
def student_dashboard_page() -> HTMLResponse:
    """Student dashboard — pulls /api/me/dashboard and renders blocks."""
    return HTMLResponse(_STUDENT_DASHBOARD_HTML)


_ONBOARDING_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Welcome to AI Pathshala</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
      color: #e2e8f0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px; }
    .card { background: #1e293b; border: 1px solid #334155; border-radius: 16px;
      padding: 32px; max-width: 640px; width: 100%; }
    .progress { display: flex; gap: 6px; margin-bottom: 24px; }
    .progress div { flex: 1; height: 6px; background: #334155; border-radius: 3px; }
    .progress div.active { background: #f59e0b; }
    .progress div.done { background: #10b981; }
    h1 { margin: 0 0 8px 0; font-size: 26px; }
    .step-num { color: #94a3b8; font-size: 13px; margin-bottom: 4px; }
    .options { display: grid; grid-template-columns: repeat(2, 1fr);
      gap: 10px; margin-top: 20px; }
    @media (max-width: 600px) { .options { grid-template-columns: 1fr; } }
    .opt { background: #0f172a; border: 1px solid #334155; border-radius: 10px;
      padding: 14px 16px; cursor: pointer; text-align: left; color: #e2e8f0;
      font-size: 14px; transition: all 0.15s; }
    .opt:hover { border-color: #f59e0b; background: #1a2235; }
    .opt.selected { border-color: #f59e0b; background: #2d2410; }
    .actions { display: flex; justify-content: space-between; margin-top: 24px; }
    .btn { padding: 10px 20px; border: 0; border-radius: 8px; cursor: pointer;
      font-weight: 600; }
    .btn.next { background: #f59e0b; color: #0f172a; }
    .btn.next:disabled { opacity: 0.4; cursor: not-allowed; }
    .btn.skip { background: transparent; color: #94a3b8; }
    .done-screen { text-align: center; padding: 32px 16px; }
    .done-screen h2 { font-size: 28px; margin: 16px 0 8px; }
    .done-screen p { color: #94a3b8; margin: 0 0 24px; }
    .signin-prompt { background: #0f172a; padding: 16px; border-radius: 10px;
      border: 1px solid #334155; text-align: center; }
    .signin-prompt a { color: #f59e0b; }
  </style>
</head>
<body>
  <div class="card" id="card">
    <div class="progress" id="progress"></div>
    <div id="content">Loading…</div>
  </div>

  <script>
    const TOTAL = 5;
    let CURRENT_STEP = null;
    let SELECTED_VALUE = null;
    let STATE = {};

    function authHeaders() {
      const token = localStorage.getItem('pathshala_token');
      return token ? { 'Authorization': 'Bearer ' + token } : {};
    }

    async function loadStatus() {
      const r = await fetch('/api/onboarding/status', { headers: authHeaders() });
      if (r.status === 401) {
        document.getElementById('content').innerHTML =
          '<div class="signin-prompt">Please <a href="/landing">sign in</a> '
          + 'to set up your study goals.</div>';
        return;
      }
      const j = await r.json();
      STATE = j.state || {};
      if (j.completed) {
        renderDone();
        return;
      }
      CURRENT_STEP = j.next_step;
      renderStep();
    }

    function renderProgress(currentStep) {
      const bar = document.getElementById('progress');
      bar.innerHTML = '';
      for (let i = 1; i <= TOTAL; i++) {
        const d = document.createElement('div');
        if (i < currentStep) d.className = 'done';
        else if (i === currentStep) d.className = 'active';
        bar.appendChild(d);
      }
    }

    function renderStep() {
      if (!CURRENT_STEP) { renderDone(); return; }
      renderProgress(CURRENT_STEP.step);
      const content = document.getElementById('content');
      content.innerHTML = '<div class="step-num">Step '
        + CURRENT_STEP.step + ' of ' + TOTAL + '</div>'
        + '<h1>' + CURRENT_STEP.label + '</h1>'
        + '<div class="options" id="opts"></div>'
        + '<div class="actions">'
        + '  <button class="btn skip" onclick="window.location.href=\\'/home\\'">Skip for now</button>'
        + '  <button class="btn next" id="nextBtn" disabled onclick="submitStep()">Next →</button>'
        + '</div>';
      const opts = document.getElementById('opts');
      CURRENT_STEP.options.forEach(o => {
        const b = document.createElement('button');
        b.className = 'opt';
        b.textContent = o.label;
        b.dataset.value = String(o.code);
        b.onclick = () => {
          opts.querySelectorAll('.opt').forEach(x => x.classList.remove('selected'));
          b.classList.add('selected');
          SELECTED_VALUE = String(o.code);
          document.getElementById('nextBtn').disabled = false;
        };
        opts.appendChild(b);
      });
      SELECTED_VALUE = null;
    }

    async function submitStep() {
      const fd = new FormData();
      fd.append('field', CURRENT_STEP.field);
      fd.append('value', SELECTED_VALUE);
      const r = await fetch('/api/onboarding/step', {
        method: 'POST', body: fd, headers: authHeaders(),
      });
      if (!r.ok) { alert('Failed to save: ' + r.statusText); return; }
      const j = await r.json();
      STATE = j.state;
      CURRENT_STEP = j.next_step;
      if (!CURRENT_STEP) {
        await completeOnboarding();
      } else {
        renderStep();
      }
    }

    async function completeOnboarding() {
      const r = await fetch('/api/onboarding/complete', {
        method: 'POST', headers: authHeaders(),
      });
      if (!r.ok) {
        alert('Could not finalise onboarding. You can update preferences in /profile.');
      }
      renderDone();
    }

    function renderDone() {
      document.getElementById('progress').innerHTML = '';
      document.getElementById('content').innerHTML = `
        <div class="done-screen">
          <div style="font-size:60px">🎉</div>
          <h2>You're all set!</h2>
          <p>Your study plan is being personalised. Let's get learning.</p>
          <a href="/home" class="btn next" style="display:inline-block;text-decoration:none">Go to my dashboard →</a>
        </div>
      `;
    }

    loadStatus();
  </script>
</body>
</html>
"""


_STUDENT_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Dashboard · AI Pathshala</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; background: #0f172a; color: #e2e8f0;
      font-family: system-ui, sans-serif; }
    header { padding: 20px 24px; border-bottom: 1px solid #334155;
      display: flex; justify-content: space-between; align-items: center; }
    h1 { margin: 0; font-size: 22px; }
    nav a { color: #f59e0b; margin-left: 16px; text-decoration: none; font-size: 14px; }
    main { padding: 24px; display: grid; grid-template-columns: repeat(3, 1fr);
      gap: 16px; max-width: 1200px; margin: 0 auto; }
    @media (max-width: 900px) { main { grid-template-columns: repeat(2, 1fr); } }
    @media (max-width: 600px) { main { grid-template-columns: 1fr; } }
    .panel { background: #1e293b; border: 1px solid #334155; border-radius: 12px;
      padding: 18px; }
    .panel h2 { margin: 0 0 12px 0; font-size: 15px; color: #94a3b8;
      text-transform: uppercase; letter-spacing: 0.5px; }
    .big { font-size: 36px; font-weight: 800; margin: 0; }
    .sub { color: #94a3b8; font-size: 13px; margin: 4px 0 0 0; }
    .list { list-style: none; padding: 0; margin: 0; }
    .list li { padding: 8px 0; border-bottom: 1px solid #334155;
      font-size: 14px; display: flex; justify-content: space-between; }
    .list li:last-child { border-bottom: 0; }
    .pill { background: #ef4444; color: #fff; padding: 2px 8px;
      border-radius: 999px; font-size: 11px; font-weight: 700; }
    .pill.ok { background: #10b981; }
    .pill.warn { background: #f59e0b; }
    .empty { color: #64748b; font-size: 13px; font-style: italic; }
    .signin { padding: 40px; text-align: center; color: #94a3b8; }
    .signin a { color: #f59e0b; }
  </style>
</head>
<body>
  <header>
    <h1>Your dashboard</h1>
    <nav>
      <a href="/home">Home</a>
      <a href="/onboarding">Goals</a>
      <a href="/pricing">Upgrade</a>
      <a href="/profile">Settings</a>
    </nav>
  </header>
  <main id="dash">Loading…</main>

  <script>
    async function load() {
      const token = localStorage.getItem('pathshala_token');
      if (!token) {
        document.getElementById('dash').innerHTML =
          '<div class="signin">Please <a href="/landing">sign in</a> to see your dashboard.</div>';
        return;
      }
      const r = await fetch('/api/me/dashboard', {
        headers: { 'Authorization': 'Bearer ' + token },
      });
      if (!r.ok) {
        document.getElementById('dash').innerHTML =
          '<div class="signin">Dashboard unavailable (' + r.status + ')</div>';
        return;
      }
      const d = await r.json();
      render(d);
    }

    function render(d) {
      const main = document.getElementById('dash');
      const p = d.profile || {};
      const onb = d.onboarding || {};
      const streak = d.streak || {};
      const mastery = d.mastery || {};
      const practice = d.practice_tests || {};
      const mock = d.mock_interviews || {};
      const essay = d.essays || {};
      const cards = d.flashcards || {};
      const live = d.live_classes || {};

      const tiles = [];
      if (!onb.completed) {
        tiles.push(`<div class="panel" style="grid-column:1/-1;border-color:#f59e0b">
          <h2>Setup needed</h2>
          <p class="sub">Complete onboarding so we can personalise your plan.</p>
          <a href="/onboarding" style="color:#f59e0b">Set goals →</a>
        </div>`);
      }
      tiles.push(`<div class="panel"><h2>Streak</h2>
        <p class="big">${streak.current_days || 0}<small style="font-size:13px;color:#94a3b8"> days</small></p>
        <p class="sub">Longest: ${streak.longest_days || 0} days</p></div>`);
      tiles.push(`<div class="panel"><h2>Due flashcards</h2>
        <p class="big">${cards.due_count || 0}</p>
        <p class="sub">${cards.deck_count || 0} decks total</p>
        <a href="/flashcards" style="color:#f59e0b;font-size:13px">Study now →</a></div>`);
      tiles.push(`<div class="panel"><h2>Weak topics</h2>
        <ul class="list">${
          (mastery.weak || []).slice(0,5).map(w =>
            `<li><span>${w.topic_key}</span><span class="pill">${(w.mastery*100).toFixed(0)}%</span></li>`
          ).join('') || '<li class="empty">No data yet — practice a few topics</li>'
        }</ul></div>`);
      tiles.push(`<div class="panel"><h2>Strong topics</h2>
        <ul class="list">${
          (mastery.strong || []).slice(0,5).map(s =>
            `<li><span>${s.topic_key}</span><span class="pill ok">${(s.mastery*100).toFixed(0)}%</span></li>`
          ).join('') || '<li class="empty">Keep practicing — strong topics will appear here</li>'
        }</ul></div>`);
      tiles.push(`<div class="panel"><h2>Recent practice tests</h2>
        <ul class="list">${
          (practice.recent || []).slice(0,4).map(t =>
            `<li><span>${t.exam} · ${t.subject}</span><span class="pill ${
              t.score ? (t.score.pct >= 0.6 ? 'ok' : 'warn') : ''
            }">${t.score ? Math.round(t.score.pct*100)+'%' : t.status}</span></li>`
          ).join('') || '<li class="empty">No tests yet</li>'
        }</ul></div>`);
      tiles.push(`<div class="panel"><h2>Mock interviews</h2>
        <ul class="list">${
          (mock.recent || []).slice(0,4).map(m =>
            `<li><span>${m.track}</span><span class="pill ${
              m.overall_score >= 7 ? 'ok' : m.overall_score ? 'warn' : ''
            }">${m.overall_score != null ? m.overall_score.toFixed(1) : m.status}</span></li>`
          ).join('') || '<li class="empty">No interviews yet</li>'
        }</ul></div>`);
      tiles.push(`<div class="panel"><h2>Essay scores</h2>
        <ul class="list">${
          (essay.recent || []).slice(0,4).map(e =>
            `<li><span>${e.rubric_id.slice(0,8)}…</span><span class="pill ${
              e.ai_score >= 60 ? 'ok' : e.ai_score ? 'warn' : ''
            }">${e.ai_score != null ? e.ai_score.toFixed(0) : '—'}</span></li>`
          ).join('') || '<li class="empty">No essays graded yet</li>'
        }</ul></div>`);
      tiles.push(`<div class="panel"><h2>Live classes</h2>
        <ul class="list">${
          (live.upcoming || []).slice(0,4).map(lc => {
            const when = new Date(lc.scheduled_at * 1000).toLocaleString();
            return `<li><span>${lc.title}</span><span class="pill warn">${when}</span></li>`;
          }).join('') || '<li class="empty">No upcoming classes</li>'
        }</ul></div>`);

      main.innerHTML = tiles.join('');
    }

    load();
  </script>
</body>
</html>
"""


# ---- User profile ---------------------------------------------------------
# Preference columns added idempotently on first use (ALTER TABLE IF NOT EXISTS).

_PROFILE_COLS_SQL = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_language TEXT NOT NULL DEFAULT 'en'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_level TEXT NOT NULL DEFAULT 'middle'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_mode TEXT NOT NULL DEFAULT 'teaching'",
]

_profile_migrated = False


def _ensure_profile_cols() -> None:
    global _profile_migrated
    if _profile_migrated:
        return
    try:
        db_url = get_db_url()
        if not db_url:
            return
        import psycopg
        with psycopg.connect(db_url, autocommit=True) as conn:
            for stmt in _PROFILE_COLS_SQL:
                conn.execute(stmt)
        _profile_migrated = True
    except Exception as exc:
        _log.warning("[profile_cols] non-fatal: %s", exc)


@app.get("/api/me/profile")
def get_my_profile(
    user: AuthUser | None = Depends(current_user),
) -> JSONResponse:
    """Return authenticated user's email, tier, and study preferences."""
    if user is None:
        raise HTTPException(401, "authentication required")
    _ensure_profile_cols()
    row = None
    try:
        db_url = get_db_url()
        if db_url:
            import psycopg
            with psycopg.connect(db_url) as conn:
                row = conn.execute(
                    "SELECT display_name, preferred_language, "
                    "       preferred_level, preferred_mode "
                    "FROM users WHERE id = %s",
                    (user.id,),
                ).fetchone()
    except Exception as exc:
        _log.warning("[get_profile] db error (non-fatal): %s", exc)
    return JSONResponse({
        "id": user.id,
        "email": user.email,
        "subscription_tier": user.subscription_tier,
        "subscription_level": user.subscription_level,
        "display_name": row[0] if row else None,
        "preferred_language": (row[1] if row and row[1] else "en"),
        "preferred_level": (row[2] if row and row[2] else "middle"),
        "preferred_mode": (row[3] if row and row[3] else "teaching"),
    })


@app.put("/api/me/profile")
async def update_my_profile(
    request: Request,
    user: AuthUser | None = Depends(current_user),
) -> JSONResponse:
    """Update user study preferences (language, level, mode, display_name)."""
    if user is None:
        raise HTTPException(401, "authentication required")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "request body must be JSON")
    _ensure_profile_cols()
    allowed = {"display_name", "preferred_language", "preferred_level", "preferred_mode"}
    updates: dict[str, str] = {
        k: str(v) for k, v in body.items()
        if k in allowed and v is not None
    }
    if not updates:
        raise HTTPException(400, "no valid fields provided")
    if "preferred_language" in updates and updates["preferred_language"] not in SUPPORTED_LANGUAGES:
        raise HTTPException(400, f"preferred_language must be one of {sorted(SUPPORTED_LANGUAGES)}")
    if "preferred_level" in updates and updates["preferred_level"] not in LEVEL_GUIDANCE:
        raise HTTPException(400, f"preferred_level must be one of {sorted(LEVEL_GUIDANCE)}")
    db_url = get_db_url()
    if not db_url:
        return JSONResponse({
            "ok": True,
            "updated": list(updates.keys()),
            "persisted": False,
            "note": "DATABASE_URL not set — preferences applied for this session only",
        })
    try:
        import psycopg
        set_clause = ", ".join(f"{col} = %s" for col in updates)
        vals = list(updates.values()) + [user.id]
        with psycopg.connect(db_url, autocommit=True) as conn:
            conn.execute(
                f"UPDATE users SET {set_clause} WHERE id = %s", vals,
            )
    except Exception as exc:
        raise HTTPException(500, f"update failed: {exc}")
    return JSONResponse({"ok": True, "updated": list(updates.keys()), "persisted": True})


# ---- DPDP Act 2023 — data portability + erasure ---------------------------

@app.get("/api/me/data/export")
def export_my_data(
    request: Request,
    user: AuthUser | None = Depends(current_user),
) -> JSONResponse:
    """DPDP Act 2023 §11 — right to access. Returns all personal data we
    hold for the authenticated user as a single JSON document."""
    if user is None:
        raise HTTPException(401, "authentication required")
    # Rate-limit: use the file_upload bucket (20 burst) as a proxy for
    # expensive read operations — prevents DB exhaustion via tight loops.
    _rate_key = _rl.client_ip_from_request(request)
    if not _rl.file_upload.try_consume(_rate_key, cost=5):
        raise HTTPException(429, "rate limit exceeded — please wait before exporting again")

    from datetime import datetime, timezone  # local import — module-level not present
    export: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
        "profile": {
            "id": user.id,
            "email": user.email,
            "subscription_tier": user.subscription_tier,
            "subscription_level": user.subscription_level,
        },
        "preferences": None,
        "jobs": [],
        "flashcard_decks": [],
        "audit_events": [],
    }

    # Preferences from DB (non-fatal: may not exist yet)
    db_url = get_db_url()
    if db_url:
        try:
            import psycopg as _pg
            with _pg.connect(db_url, autocommit=True) as conn:
                row = conn.execute(
                    "SELECT display_name, preferred_language, "
                    "       preferred_level, preferred_mode, created_at "
                    "FROM users WHERE id = %s",
                    (user.id,),
                ).fetchone()
                if row:
                    export["profile"]["display_name"] = row[0]
                    export["profile"]["created_at"] = (
                        row[4].isoformat() if hasattr(row[4], "isoformat") else str(row[4])
                    ) if row[4] else None
                    export["preferences"] = {
                        "language": row[1],
                        "level": row[2],
                        "mode": row[3],
                    }
                # Audit events: last 100 actions for this user
                audit_rows = conn.execute(
                    "SELECT action, note, created_at FROM audit_log "
                    "WHERE actor_user_id = %s ORDER BY created_at DESC LIMIT 100",
                    (user.id,),
                ).fetchall()
                export["audit_events"] = [
                    {
                        "action": r[0],
                        "note": r[1],
                        "timestamp": r[2].isoformat() if hasattr(r[2], "isoformat") else str(r[2]),
                    }
                    for r in audit_rows
                ]
        except Exception as exc:
            _log.warning("[data_export] profile/audit query failed (non-fatal): %s", exc)

    # Job history — use the module-level store, not _get_store() which doesn't exist
    try:
        jobs = store.recent_jobs(limit=200, filter_user_id=user.id)
        export["jobs"] = [
            {
                "id": j.get("id"),
                "topic": j.get("topic"),
                "language": j.get("language"),
                "status": j.get("status"),
                "created_at": j.get("created_at"),
            }
            for j in (jobs or [])
        ]
    except Exception as exc:
        _log.warning("[data_export] jobs query failed (non-fatal): %s", exc)

    # Flashcard decks
    try:
        from . import spaced_repetition as _sr
        decks = _sr.list_my_decks(user_id=user.id)
        export["flashcard_decks"] = [
            {"id": d.id, "name": d.name, "card_count": d.card_count}
            for d in (decks or [])
        ]
    except Exception as exc:
        _log.warning("[data_export] flashcard query failed (non-fatal): %s", exc)

    _audit.record(action="dpdp.data_export", actor_user_id=user.id)
    return JSONResponse(export)


@app.delete("/api/me/account")
def delete_my_account(
    user: AuthUser | None = Depends(current_user),
) -> JSONResponse:
    """DPDP Act 2023 §12 — right to erasure. Soft-deletes the account:
    the email is anonymised immediately and the account is locked. Full
    purge of all personal data happens within 30 days (scheduled by ops).

    This action is irreversible. The token used to call this endpoint
    becomes invalid after the call returns."""
    if user is None:
        raise HTTPException(401, "authentication required")

    db_url = get_db_url()
    if not db_url:
        raise HTTPException(503, "database not configured — cannot delete account")

    anon_email = f"deleted-{user.id}@deleted.invalid"
    deletion_requested_at = datetime.now(timezone.utc).isoformat()

    # Ensure profile columns exist before we try to NULL them out —
    # without this, a user who never visited /api/me/profile would hit
    # ProgrammingError: column 'preferred_language' does not exist.
    _ensure_profile_cols()

    try:
        import psycopg as _pg
        with _pg.connect(db_url, autocommit=True) as conn:
            # Anonymise email + lock account in one statement.
            conn.execute(
                "UPDATE users SET email = %s, account_locked = TRUE, "
                "display_name = NULL, preferred_language = NULL, "
                "preferred_level = NULL, preferred_mode = NULL "
                "WHERE id = %s",
                (anon_email, user.id),
            )
    except Exception as exc:
        _log.error("[delete_account] anonymisation failed for user %s: %s", user.id, exc)
        raise HTTPException(500, "account deletion failed — please contact support")

    _audit.record(
        action="dpdp.account_deletion_requested",
        actor_user_id=user.id,
        note=f"deletion_requested_at={deletion_requested_at}",
    )
    _log.info(
        "[delete_account] user %s requested erasure; email anonymised, "
        "full purge scheduled within 30 days", user.id,
    )
    return JSONResponse({
        "ok": True,
        "message": (
            "Account anonymised. All remaining personal data will be purged "
            "within 30 days in accordance with the DPDP Act 2023."
        ),
        "deletion_requested_at": deletion_requested_at,
    })


# ---- Subscription tier enforcement ----------------------------------------

_TIER_RANK: dict[str, int] = {
    "M1": 1, "M2": 2, "M3": 3,
    "M4a": 4, "M4b": 5, "M4c": 6, "M4d": 7, "M4e": 8,
}


def _require_tier(user: AuthUser | None, min_tier: str) -> AuthUser:
    """Raise 401/403 if the user is below `min_tier`. Use as a guard at
    the start of any endpoint that requires a paid subscription.

    Example:
        user = _require_tier(user, "M2")
    """
    if user is None:
        raise HTTPException(401, "authentication required")
    user_rank = _TIER_RANK.get(user.subscription_tier, 0)
    required_rank = _TIER_RANK.get(min_tier, 0)
    if user_rank < required_rank:
        raise HTTPException(
            403,
            f"this feature requires subscription tier {min_tier} or above "
            f"(your tier: {user.subscription_tier}). "
            "Upgrade at /pricing",
        )
    return user


# ---- Flashcard due queue + SM-2 review ------------------------------------

@app.get("/api/flashcards/due")
def flashcards_due_queue(
    deck_id: str | None = None,
    limit: int = 20,
    user: AuthUser | None = Depends(current_user),
) -> JSONResponse:
    """Return cards due for SM-2 review today, optionally filtered by deck."""
    if user is None:
        raise HTTPException(401, "authentication required")
    from . import spaced_repetition as _sr
    _sr.migrate()
    cards = _sr.due_queue(
        user_id=user.id,
        deck_id=deck_id or None,
        limit=min(max(1, limit), 100),
    )
    return JSONResponse({
        "cards": [
            {
                "id": c.id,
                "deck_id": c.deck_id,
                "front": c.front,
                "back": c.back,
                "hint": c.hint,
                "source_ref": c.source_ref,
            }
            for c in cards
        ],
        "count": len(cards),
    })


@app.post("/api/flashcards/{card_id}/review")
def review_flashcard(
    card_id: str,
    grade: int = Form(..., ge=0, le=5, description="SM-2 grade 0=Again 3=Hard 4=Good 5=Easy"),
    time_seconds: int | None = Form(None),
    user: AuthUser | None = Depends(current_user),
) -> JSONResponse:
    """Submit a SM-2 review grade for a card. Updates interval and next due date."""
    if user is None:
        raise HTTPException(401, "authentication required")
    from . import spaced_repetition as _sr
    try:
        outcome = _sr.review_card(
            card_id=card_id,
            user_id=user.id,
            grade=grade,
            time_seconds=time_seconds,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return JSONResponse({
        "ok": True,
        "card_id": card_id,
        "new_interval_days": round(outcome.new_interval, 2),
        "new_ease": round(outcome.new_ease, 3),
        "new_due_at": outcome.new_due_at,
        "repetitions": outcome.repetitions,
        "lapses": outcome.lapses,
    })


@app.get("/api/flashcards/decks")
def list_flashcard_decks(
    limit: int = Query(default=200, ge=1, le=500),
    user: AuthUser | None = Depends(current_user),
) -> JSONResponse:
    """List the authenticated user's flashcard decks."""
    if user is None:
        raise HTTPException(401, "authentication required")
    from . import spaced_repetition as _sr
    _sr.migrate()
    decks = _sr.list_my_decks(user.id, limit=limit)
    return JSONResponse({
        "decks": [
            {
                "id": d.id,
                "title": d.title,
                "description": d.description,
                "card_count": d.card_count,
                "language": d.language,
                "visibility": d.visibility,
            }
            for d in decks
        ],
        "count": len(decks),
    })


# ---- Quiz data (GET alias) ------------------------------------------------

@app.get("/api/quiz/{lesson_id}")
def get_quiz_data(
    lesson_id: str,
    user: AuthUser | None = Depends(current_user),
) -> JSONResponse:
    """Return the quiz questions for a cached lesson (GET version)."""
    cached_lesson = cache.get_lesson_by_key(lesson_id)
    if cached_lesson is None:
        raise HTTPException(404, "lesson not found; POST /lessons first")
    return JSONResponse({
        "lesson_id": lesson_id,
        "title": cached_lesson.title,
        "language_code": cached_lesson.language_code,
        "language_name": cached_lesson.language_name,
        "level": cached_lesson.level,
        "questions": cached_lesson.quiz or [],
    })


# ---- Password management -------------------------------------------------

_RESET_TOKEN_DDL = """
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token      TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    used_at    TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL
        DEFAULT (NOW() + INTERVAL '1 hour')
);
"""

_reset_table_created = False


def _ensure_reset_table() -> None:
    global _reset_table_created
    if _reset_table_created:
        return
    try:
        db_url = get_db_url()
        if db_url:
            import psycopg
            with psycopg.connect(db_url, autocommit=True) as conn:
                conn.execute(_RESET_TOKEN_DDL)
        _reset_table_created = True
    except Exception as exc:
        _log.warning("[reset_table] non-fatal: %s", exc)


def _send_reset_email(*, to_email: str, reset_url: str) -> None:
    """Send a password-reset email. Uses SMTP when SMTP_HOST is set;
    falls back to console log in dev so the flow is testable without
    a mail server."""
    smtp_host = os.environ.get("SMTP_HOST")
    base_url = os.environ.get("APP_BASE_URL", "http://localhost:8000")
    full_url = f"{base_url}{reset_url}"
    if not smtp_host:
        _log.info(
            "[DEV no-email] Password reset for %s. Reset link: %s "
            "(set SMTP_HOST in .env to send real emails)",
            to_email, full_url,
        )
        return
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["Subject"] = "Reset your AI Pathashala password"
    msg["From"] = os.environ.get("SMTP_FROM", "noreply@aipadhaiapp.com")
    msg["To"] = to_email
    msg.set_content(
        f"Click the link below to reset your AI Pathashala password:\n\n"
        f"{full_url}\n\n"
        f"This link expires in 1 hour. If you didn't request a reset, ignore this email."
    )
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as smtp:
            smtp.starttls()
            smtp_user = os.environ.get("SMTP_USER", "")
            smtp_pass = os.environ.get("SMTP_PASSWORD", "")
            if smtp_user:
                smtp.login(smtp_user, smtp_pass)
            smtp.send_message(msg)
    except Exception as exc:
        _log.error("[send_reset_email] failed to send to %s: %s", to_email, exc)


@app.post("/auth/forgot-password")
def forgot_password(email: str = Form(...)) -> JSONResponse:
    """Send a password-reset link. Always returns 200 to prevent email enumeration."""
    _ok = {"ok": True, "message": "If that email is registered, you'll receive a reset link."}
    if _get_user_repo() is None:
        return JSONResponse(_ok)
    result = _get_user_repo().find_by_email(email)
    if result is None:
        return JSONResponse(_ok)
    user, _ = result
    import secrets
    token = secrets.token_urlsafe(32)
    _ensure_reset_table()
    try:
        db_url = get_db_url()
        if db_url:
            import psycopg
            with psycopg.connect(db_url, autocommit=True) as conn:
                conn.execute(
                    "INSERT INTO password_reset_tokens (token, user_id) "
                    "VALUES (%s, %s)",
                    (token, user.id),
                )
    except Exception as exc:
        _log.error("[forgot_password] db error: %s", exc)
    reset_url = f"/auth/reset-password?token={token}"
    _send_reset_email(to_email=email, reset_url=reset_url)
    return JSONResponse(_ok)


@app.post("/auth/reset-password")
def reset_password(
    token: str = Form(...),
    new_password: str = Form(..., min_length=8),
) -> JSONResponse:
    """Consume a reset token and set a new password."""
    _validate_password_complexity(new_password)
    db_url = get_db_url()
    if not db_url:
        raise HTTPException(503, "auth not configured")
    _ensure_reset_table()
    try:
        import psycopg
        with psycopg.connect(db_url, autocommit=True) as conn:
            row = conn.execute(
                "SELECT user_id FROM password_reset_tokens "
                "WHERE token = %s AND used_at IS NULL AND expires_at > NOW()",
                (token,),
            ).fetchone()
            if not row:
                raise HTTPException(400, "invalid or expired reset link — please request a new one")
            conn.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (hash_password(new_password), row[0]),
            )
            conn.execute(
                "UPDATE password_reset_tokens SET used_at = NOW() WHERE token = %s",
                (token,),
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"reset failed: {exc}")
    return JSONResponse({"ok": True, "message": "Password updated — please sign in."})


@app.post("/auth/change-password")
def change_password(
    old_password: str = Form(...),
    new_password: str = Form(..., min_length=8),
    user: AuthUser | None = Depends(current_user),
) -> JSONResponse:
    """Change password for an authenticated user."""
    if user is None:
        raise HTTPException(401, "authentication required")
    _validate_password_complexity(new_password)
    if _get_user_repo() is None:
        raise HTTPException(503, "auth not configured — restart the server")
    result = _get_user_repo().find_by_email(user.email)
    if not result:
        raise HTTPException(404, "user not found")
    _, current_hash = result
    if not current_hash or not verify_password(old_password, current_hash):
        raise HTTPException(400, "current password is incorrect")
    db_url = get_db_url()
    try:
        import psycopg
        with psycopg.connect(db_url, autocommit=True) as conn:
            conn.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (hash_password(new_password), user.id),
            )
    except Exception as exc:
        raise HTTPException(500, f"update failed: {exc}")
    return JSONResponse({"ok": True, "message": "Password changed — please sign in again."})


# ---- Doubt queue for teacher portal --------------------------------------

@app.get("/api/doubts/queue")
def get_doubt_queue(
    user: AuthUser | None = Depends(current_user),
) -> JSONResponse:
    """Return pending doubts for the teacher/org to claim and respond to.
    Requires teacher or admin role in at least one org."""
    if user is None:
        raise HTTPException(401, "authentication required")
    # Role gate: only teachers and admins may see the doubt queue.
    try:
        user_orgs = _orgs.find_orgs_for_user(user.id)
        is_educator = any(
            _orgs.user_role_in_org(org_id=o.id, user_id=user.id) in ("teacher", "admin")
            for o in user_orgs
        )
    except Exception:
        is_educator = False
    if not is_educator:
        raise HTTPException(403, "teacher or admin role required to view the doubt queue")
    try:
        from . import doubt_clearing as _dc
        _dc.migrate()
        with _dc._conn() as conn:
            rows = conn.execute(
                "SELECT id, user_id, question_text, status, created_at "
                "FROM doubt_requests WHERE status = 'pending' "
                "ORDER BY created_at ASC LIMIT 50",
            ).fetchall()
        return JSONResponse({
            "doubts": [
                {
                    "id": r[0],
                    "user_id": r[1],
                    "question": r[2],
                    "status": r[3],
                    "created_at": r[4],
                }
                for r in rows
            ],
            "count": len(rows),
        })
    except Exception as exc:
        return JSONResponse({"doubts": [], "count": 0, "note": str(exc)})
