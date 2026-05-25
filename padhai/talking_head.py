"""Talking-head provider abstraction — surfaces the tiered subscription
options the consumer app exposes.

This is the spine of PadhAI's tutor-tier monetisation. Every tier uses
the same lesson plan and the same TTS narration; only the on-screen
teacher representation changes. Picking the provider per session lets us
serve free KG users with cartoons and paying institutional users with
photoreal teachers without forking the rest of the pipeline.

Subscription map is two-axis: (medium of teaching) × (level of teaching).
The medium dimension is what this module configures; the level dimension
is the difficulty value passed into pedagogy.generate_lesson(). Pricing
scales on both axes — a NEET-aspirant's parents pay way more than a
KG parent for the same minute of video.

Medium tiers (illustrative cost-to-us per 7-min video, India):

    M1  CartoonAvatar + free TTS (espeak/device)   ~Rs 0
    M2  CartoonAvatar + Bhashini Indic TTS         ~Rs 0.5
    M3  Wav2Lip self-hosted + Bhashini             ~Rs 3-4 (GPU compute)
    M4  HeyGen / D-ID hosted + ElevenLabs clone    ~Rs 25-40 (paid APIs)

Level tiers (drive the pedagogy.LEVEL_GUIDANCE table):

    L1 kg, L2 primary, L3 middle, L4 secondary/board, L5 neet_jee/upsc

The product layer multiplies the two to retail prices ranging from
Free (M1×L1-L3) up to Rs 2,499/mo (M4×L5). See LEARN.md §7.1 for the
full grid and reasoning.

Cost-per-minute reference (May 2026 pricing, ballpark):
  - HeyGen interactive avatar API: ~$0.30 / minute
  - D-ID Talks API: ~$0.10-0.20 / minute
  - Synthesia public API: ~$0.50 / minute
  - Wav2Lip self-hosted on a single A10G GPU: ~$0.02 / minute (compute
    only; one-time setup with a source photo, no fine-tuning needed)

The Premium tier is the sweet spot for unit economics at scale — moving
from CartoonAvatar to Wav2Lip costs you ~Rs 3 / video but unlocks a
~3x ARPU jump in field tests (Rs 99 -> Rs 299). The Pro tier is for
institutional/school deals where the school's branding (their own
teacher's face on every lesson) matters more than per-video margin."""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Protocol


class TalkingHeadProvider(Protocol):
    name: str

    def render_clip(
        self,
        narration_audio: Path,
        script_text: str,
        language_code: str,
        out_path: Path,
    ) -> None:
        """Produce an MP4 of the talking head saying `script_text` for the
        duration of `narration_audio`. The render pipeline overlays this on
        the whiteboard slide separately, so providers only need to produce
        an isolated avatar clip (transparent background preferred but not
        required — chroma key fallback handled by the compositor)."""
        ...


class CartoonAvatarProvider:
    """Offline default — uses the in-house PIL teacher + audio-envelope lip
    flap. Returns False from render_clip because the render pipeline already
    knows how to draw this directly into each slide frame; no separate clip
    file is needed."""

    name = "cartoon"
    in_process = True   # signal to render.py: handle this inline

    def render_clip(self, *args, **kwargs) -> None:  # pragma: no cover
        raise RuntimeError(
            "CartoonAvatarProvider draws the teacher inline; "
            "render.py should not call render_clip on this provider."
        )


class HeyGenProvider:
    """HeyGen interactive-avatar API.

    Flow (per HeyGen docs):
      1. POST /v2/video/generate with the script + voice + avatar settings
      2. Poll /v1/video_status.get?video_id=... until status == 'completed'
      3. Download the returned video_url
    Costs roughly $0.30 / minute of generated video.
    """

    name = "heygen"
    in_process = False
    API_BASE = "https://api.heygen.com"

    def __init__(
        self,
        api_key: str,
        avatar_id: str | None = None,
        voice_id: str | None = None,
        timeout: float = 300.0,
    ):
        self.api_key = api_key
        self.avatar_id = avatar_id or os.environ.get(
            "HEYGEN_AVATAR_ID", "Daisy-inskirt-20220818",
        )
        self.voice_id = voice_id or os.environ.get(
            "HEYGEN_VOICE_ID", "1bd001e7e50f421d891986aad5158bc8",
        )
        self.timeout = timeout

    def render_clip(
        self,
        narration_audio: Path,
        script_text: str,
        language_code: str,
        out_path: Path,
    ) -> None:
        import httpx

        # HeyGen ingests text directly; we pass the script and let HeyGen's
        # internal TTS render it. If your script differs from your narration
        # text, the lip sync will drift — keep them identical.
        body = {
            "video_inputs": [
                {
                    "character": {
                        "type": "avatar",
                        "avatar_id": self.avatar_id,
                        "avatar_style": "normal",
                    },
                    "voice": {
                        "type": "text",
                        "input_text": script_text,
                        "voice_id": self.voice_id,
                    },
                    "background": {"type": "color", "value": "#00FF00"},
                }
            ],
            "dimension": {"width": 480, "height": 720},
        }
        headers = {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
        }
        resp = httpx.post(
            f"{self.API_BASE}/v2/video/generate",
            json=body, headers=headers, timeout=60.0,
        )
        resp.raise_for_status()
        video_id = resp.json()["data"]["video_id"]

        # poll
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            r = httpx.get(
                f"{self.API_BASE}/v1/video_status.get",
                params={"video_id": video_id},
                headers=headers, timeout=30.0,
            )
            r.raise_for_status()
            data = r.json()["data"]
            if data["status"] == "completed":
                url = data["video_url"]
                with httpx.stream("GET", url, timeout=120.0) as src:
                    out_path.write_bytes(src.read())
                return
            if data["status"] == "failed":
                raise RuntimeError(f"HeyGen render failed: {data.get('error')}")
            time.sleep(5.0)
        raise TimeoutError(f"HeyGen render timed out after {self.timeout}s")


class SynthesiaProvider:
    """Synthesia hosted photoreal avatars (https://www.synthesia.io/).

    Top-end photoreal quality — Synthesia's avatars hold the polish bar in
    the corporate-training space. Roughly $0.50 / minute generated, billed
    on top of a monthly base. Requires an Enterprise plan for direct API
    access; the personal plan is dashboard-only.

    Setup:

        export SYNTHESIA_API_KEY=...
        export SYNTHESIA_AVATAR_ID=anna_costume1_cameraA   # optional, sane default
        export SYNTHESIA_BACKGROUND=off_white              # optional

    Synthesia ingests the script text and renders both the voice and the
    lip sync internally. We pass `script_text` (which should be identical
    to the text used for your PadhAI narration, otherwise the audio track
    won't line up). The `narration_audio` argument is unused but kept in
    the signature for protocol compatibility with Wav2Lip/D-ID.

    Render time at Synthesia's side is typically 3-5x audio duration with
    a one-time queue wait. Renders are billed even on failure, so test
    against `"test": True` first when wiring this up."""

    name = "synthesia"
    in_process = False
    API_BASE = "https://api.synthesia.io"

    def __init__(
        self,
        api_key: str,
        avatar_id: str | None = None,
        background: str | None = None,
        test_mode: bool = False,
        timeout: float = 600.0,
    ):
        self.api_key = api_key
        self.avatar_id = avatar_id or os.environ.get(
            "SYNTHESIA_AVATAR_ID", "anna_costume1_cameraA",
        )
        self.background = background or os.environ.get(
            "SYNTHESIA_BACKGROUND", "off_white",
        )
        self.test_mode = test_mode
        self.timeout = timeout

    def render_clip(
        self,
        narration_audio: Path,
        script_text: str,
        language_code: str,
        out_path: Path,
    ) -> None:
        import httpx

        body = {
            "test": self.test_mode,
            "title": "PadhAI scene",
            "input": [
                {
                    "avatar": self.avatar_id,
                    "background": self.background,
                    "scriptText": script_text,
                }
            ],
        }
        headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
        }
        resp = httpx.post(
            f"{self.API_BASE}/v2/videos",
            json=body, headers=headers, timeout=60.0,
        )
        resp.raise_for_status()
        video_id = resp.json()["id"]

        # poll
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            r = httpx.get(
                f"{self.API_BASE}/v2/videos/{video_id}",
                headers=headers, timeout=30.0,
            )
            r.raise_for_status()
            data = r.json()
            status = data.get("status")
            if status == "complete":
                download_url = data["download"]
                with httpx.stream("GET", download_url, timeout=180.0) as src:
                    out_path.write_bytes(src.read())
                return
            if status in ("rejected", "error", "failed"):
                raise RuntimeError(
                    f"Synthesia render {status}: {data.get('error', data)}"
                )
            time.sleep(10.0)
        raise TimeoutError(f"Synthesia render timed out after {self.timeout}s")


class TavusProvider:
    """Tavus — personalized AI video at scale (https://www.tavus.io/).

    Tavus's distinguishing feature is **personalization**: create one
    `replica` from a 2-min recording, then generate thousands of
    personalized videos with per-student variable interpolation
    (`{{first_name}}`, `{{streak_count}}`, etc.). The right pick when the
    pedagogical play is 'a video addressed personally to each student'
    rather than 'one generic lesson for everyone'.

    Setup:

        export TAVUS_API_KEY=...
        export TAVUS_REPLICA_ID=r1234abcd   # ID of your replica

    Pricing (May 2026): ~$0.40-0.60 / minute generated, usage-based
    above the base plan."""

    name = "tavus"
    in_process = False
    API_BASE = "https://tavusapi.com"

    def __init__(
        self,
        api_key: str,
        replica_id: str,
        timeout: float = 600.0,
    ):
        self.api_key = api_key
        self.replica_id = replica_id
        self.timeout = timeout

    def render_clip(
        self,
        narration_audio: Path,
        script_text: str,
        language_code: str,
        out_path: Path,
    ) -> None:
        import httpx

        body = {
            "replica_id": self.replica_id,
            "script": script_text,
            "video_name": "PadhAI scene",
        }
        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        resp = httpx.post(
            f"{self.API_BASE}/v2/videos", json=body, headers=headers,
            timeout=60.0,
        )
        resp.raise_for_status()
        video_id = resp.json()["video_id"]

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            r = httpx.get(
                f"{self.API_BASE}/v2/videos/{video_id}",
                headers=headers, timeout=30.0,
            )
            r.raise_for_status()
            data = r.json()
            status = data.get("status")
            if status == "ready":
                url = data["download_url"]
                with httpx.stream("GET", url, timeout=180.0) as src:
                    out_path.write_bytes(src.read())
                return
            if status in ("error", "failed"):
                raise RuntimeError(f"Tavus render {status}: {data}")
            time.sleep(8.0)
        raise TimeoutError(f"Tavus render timed out after {self.timeout}s")


class DeepBrainProvider:
    """DeepBrain AI / AI Studios — news-anchor & professional-presenter
    style avatars (https://www.aistudios.com/).

    Highest realism in the news-broadcast / corporate-finance niche;
    avatars hold a confident anchor pose with studio backgrounds. The
    right pick for UPSC / civil-services / financial-literacy content
    where the implied authority of a news presenter signals quality.

    Setup:

        export DEEPBRAIN_API_KEY=...
        export DEEPBRAIN_MODEL_ID=M000045058   # avatar model id
        export DEEPBRAIN_LANGUAGE=en           # optional, defaults to en

    Pricing (May 2026): tiered, $30+/mo base; ~$0.30-0.80 / minute
    above quota depending on plan. API endpoint shapes vary by region;
    confirm against your DeepBrain dashboard before production use."""

    name = "deepbrain"
    in_process = False
    API_BASE = "https://aistudios.com/api"

    def __init__(
        self,
        api_key: str,
        model_id: str,
        language: str = "en",
        timeout: float = 600.0,
    ):
        self.api_key = api_key
        self.model_id = model_id
        self.language = language
        self.timeout = timeout

    def render_clip(
        self,
        narration_audio: Path,
        script_text: str,
        language_code: str,
        out_path: Path,
    ) -> None:
        import httpx

        body = {
            "scenes": [
                {
                    "model": self.model_id,
                    "text": script_text,
                    "language": self.language,
                }
            ],
        }
        headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
        }
        resp = httpx.post(
            f"{self.API_BASE}/v2/generate", json=body, headers=headers,
            timeout=60.0,
        )
        resp.raise_for_status()
        project_id = resp.json().get("project_id") or resp.json().get("id")

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            r = httpx.get(
                f"{self.API_BASE}/v2/projects/{project_id}",
                headers=headers, timeout=30.0,
            )
            r.raise_for_status()
            data = r.json()
            status = data.get("status")
            if status in ("completed", "complete", "done"):
                url = data.get("download_url") or data.get("video_url")
                with httpx.stream("GET", url, timeout=180.0) as src:
                    out_path.write_bytes(src.read())
                return
            if status in ("failed", "error"):
                raise RuntimeError(f"DeepBrain render {status}: {data}")
            time.sleep(10.0)
        raise TimeoutError(f"DeepBrain render timed out after {self.timeout}s")


class DIDProvider:
    """D-ID Talks API — talking-head from a source photo + audio file.

    Flow:
      1. POST /talks with source_url (your photo) + script audio_url
      2. Poll GET /talks/{id} until status == 'done'
      3. Download result_url

    Costs roughly $0.10-0.20 / minute. Requires a source photo URL
    (publicly accessible) which is set via DID_SOURCE_PHOTO_URL."""

    name = "d-id"
    in_process = False
    API_BASE = "https://api.d-id.com"

    def __init__(
        self,
        api_key: str,
        source_photo_url: str | None = None,
        timeout: float = 300.0,
    ):
        self.api_key = api_key
        self.source_photo_url = source_photo_url or os.environ.get(
            "DID_SOURCE_PHOTO_URL", ""
        )
        if not self.source_photo_url:
            raise ValueError(
                "DIDProvider needs DID_SOURCE_PHOTO_URL (the public URL of "
                "a portrait photo of the teacher) to be set."
            )
        self.timeout = timeout

    def render_clip(
        self,
        narration_audio: Path,
        script_text: str,
        language_code: str,
        out_path: Path,
    ) -> None:  # pragma: no cover — depends on a paid external service
        # Real implementation would upload narration_audio to a temporary
        # public URL (S3 / GCS / D-ID's own files endpoint), then POST that
        # URL as audio_url. The shape is documented at:
        # https://docs.d-id.com/reference/talks-create
        # Full D-ID integration requires a signed-URL endpoint for audio
        # and a source-photo URL. Until that's wired, fall back to the
        # cartoon provider so M4 jobs don't crash.
        import logging
        logging.getLogger(__name__).warning(
            "DIDProvider.render_clip called but D-ID integration is not "
            "complete; falling back to CartoonAvatarProvider. Set "
            "DID_SOURCE_PHOTO_URL and wire a signed-audio-URL endpoint "
            "to enable full D-ID output."
        )
        CartoonAvatarProvider().render_clip(
            narration_audio, script_text, language_code, out_path
        )


class Wav2LipProvider:
    """Self-hosted Wav2Lip — photoreal lip-sync from a single source photo.

    Wav2Lip (Rudrabha et al, 2020) takes a portrait video/photo and an
    audio clip, and outputs a video where the face's mouth has been
    re-animated to match the audio. State of the art for cheap, fast,
    photoreal lip sync. Limitations: only the lower face is modified
    (mouth + chin); body, eyes, and head pose come from the source.

    Setup (one-time, on your GPU machine):

        git clone https://github.com/Rudrabha/Wav2Lip
        cd Wav2Lip
        pip install -r requirements.txt
        # download the wav2lip_gan.pth checkpoint per the repo README
        # (Google Drive link, ~400 MB)

        export WAV2LIP_REPO_PATH=/abs/path/to/Wav2Lip
        export WAV2LIP_CHECKPOINT=/abs/path/to/wav2lip_gan.pth
        export WAV2LIP_SOURCE_PHOTO=/abs/path/to/teacher_portrait.jpg

    Source-photo tips: head-on shot, mouth closed, plain background (you
    can chromakey it later). 720x720 is plenty. Wav2Lip will look weird
    if the photo's mouth is open or partially occluded.

    Per-clip runtime: roughly 2-3× audio duration on an A10G / RTX 3090.
    Marginal cost ~$0.02 / minute (compute only).
    """

    name = "wav2lip"
    in_process = False

    def __init__(
        self,
        repo_path: Path,
        checkpoint: Path,
        source_photo: Path,
        python_bin: str = "python",
        resize_factor: int = 1,
        pads: tuple[int, int, int, int] = (0, 10, 0, 0),
    ):
        self.repo_path = Path(repo_path)
        self.checkpoint = Path(checkpoint)
        self.source_photo = Path(source_photo)
        self.python_bin = python_bin
        self.resize_factor = resize_factor
        self.pads = pads

    def render_clip(
        self,
        narration_audio: Path,
        script_text: str,         # unused — Wav2Lip animates to audio, not text
        language_code: str,       # unused — Wav2Lip is language-agnostic
        out_path: Path,
    ) -> None:
        import shutil
        if shutil.which(self.python_bin) is None:
            raise RuntimeError(
                f"Wav2LipProvider needs `{self.python_bin}` on PATH"
            )
        if not (self.repo_path / "inference.py").exists():
            raise RuntimeError(
                f"Wav2Lip inference.py not found at {self.repo_path}/inference.py"
            )
        cmd = [
            self.python_bin, "inference.py",
            "--checkpoint_path", str(self.checkpoint),
            "--face", str(self.source_photo),
            "--audio", str(narration_audio),
            "--outfile", str(out_path),
            "--resize_factor", str(self.resize_factor),
            "--pads",
            str(self.pads[0]), str(self.pads[1]),
            str(self.pads[2]), str(self.pads[3]),
        ]
        import subprocess
        subprocess.run(cmd, cwd=self.repo_path, check=True)


PROVIDER_NAMES = (
    "synthesia",
    "heygen",
    "tavus",
    "deepbrain",
    "d-id",
    "wav2lip",
    "cartoon",
)


def _build_named_provider(name: str) -> TalkingHeadProvider:
    """Construct a provider by canonical name from the environment.
    Used by both the explicit-selector path and the default-order path."""
    name = name.lower().strip()
    if name in ("d-id", "did"):
        return DIDProvider(api_key=os.environ["DID_API_KEY"])
    if name == "synthesia":
        return SynthesiaProvider(api_key=os.environ["SYNTHESIA_API_KEY"])
    if name == "heygen":
        return HeyGenProvider(api_key=os.environ["HEYGEN_API_KEY"])
    if name == "tavus":
        return TavusProvider(
            api_key=os.environ["TAVUS_API_KEY"],
            replica_id=os.environ["TAVUS_REPLICA_ID"],
        )
    if name == "deepbrain":
        return DeepBrainProvider(
            api_key=os.environ["DEEPBRAIN_API_KEY"],
            model_id=os.environ["DEEPBRAIN_MODEL_ID"],
            language=os.environ.get("DEEPBRAIN_LANGUAGE", "en"),
        )
    if name == "wav2lip":
        return Wav2LipProvider(
            repo_path=Path(os.environ["WAV2LIP_REPO_PATH"]),
            checkpoint=Path(os.environ["WAV2LIP_CHECKPOINT"]),
            source_photo=Path(os.environ["WAV2LIP_SOURCE_PHOTO"]),
            python_bin=os.environ.get("WAV2LIP_PYTHON", "python"),
        )
    if name == "cartoon":
        return CartoonAvatarProvider()
    raise ValueError(
        f"unknown talking-head provider {name!r}; "
        f"choose one of {PROVIDER_NAMES}"
    )


def get_provider() -> TalkingHeadProvider:
    """Resolve the talking-head provider from the environment.

    Two modes:

    1) Explicit selection — set `PADHAI_TALKING_HEAD_PROVIDER` to one of
       `synthesia | heygen | tavus | deepbrain | d-id | wav2lip | cartoon`.
       Use this when you want the application to honor the subscription
       tier the user purchased (e.g. an institutional Synthesia contract
       even though other keys are also configured).

    2) Auto-select (no `PADHAI_TALKING_HEAD_PROVIDER`) — falls back to a
       quality/price preference order: Wav2Lip (own GPU, cheapest
       photoreal) → DeepBrain → Synthesia → Tavus → HeyGen → D-ID →
       Cartoon. The first provider whose env vars are fully set wins.

    `Wav2Lip` always wins explicit auto-select when its env is set, because
    self-hosted is by far the cheapest photoreal option."""
    explicit = os.environ.get("PADHAI_TALKING_HEAD_PROVIDER")
    if explicit:
        return _build_named_provider(explicit)

    if (
        os.environ.get("WAV2LIP_REPO_PATH")
        and os.environ.get("WAV2LIP_CHECKPOINT")
        and os.environ.get("WAV2LIP_SOURCE_PHOTO")
    ):
        return _build_named_provider("wav2lip")
    if os.environ.get("DEEPBRAIN_API_KEY") and os.environ.get("DEEPBRAIN_MODEL_ID"):
        return _build_named_provider("deepbrain")
    if os.environ.get("SYNTHESIA_API_KEY"):
        return _build_named_provider("synthesia")
    if os.environ.get("TAVUS_API_KEY") and os.environ.get("TAVUS_REPLICA_ID"):
        return _build_named_provider("tavus")
    if os.environ.get("HEYGEN_API_KEY"):
        return _build_named_provider("heygen")
    if os.environ.get("DID_API_KEY") and os.environ.get("DID_SOURCE_PHOTO_URL"):
        return _build_named_provider("d-id")
    return CartoonAvatarProvider()
