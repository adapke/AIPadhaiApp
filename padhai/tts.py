"""TTS provider abstraction.

Two providers ship today:

- GTTSProvider (default): uses gtts-python which hits Google's unofficial
  translate.google.com endpoint. Free but rate-limited and not production
  grade.

- BhashiniProvider: uses the Government of India's Bhashini / ULCA inference
  pipeline. Production-grade Indic-language TTS, free for DPIIT-recognised
  startups. Requires registration at https://bhashini.gov.in/ to obtain a
  user ID and API key, and a pipeline ID for the TTS task.

Auto-selection: if BHASHINI_API_KEY is set in the environment, Bhashini is
used; otherwise gTTS. The cache keys audio entries by provider name so the
two providers' outputs don't collide."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Protocol


class TTSProvider(Protocol):
    name: str

    def synthesise(self, text: str, language_code: str, out_path: Path) -> None: ...


class GTTSProvider:
    name = "gtts"

    def synthesise(self, text: str, language_code: str, out_path: Path) -> None:
        from gtts import gTTS  # imported lazily so other providers don't pull it in

        gTTS(text=text, lang=language_code, slow=False).save(str(out_path))


class PiperProvider:
    """Piper TTS — offline neural voices that sound genuinely human, not
    formant-robot like espeak.

    Free, runs on CPU, ~1x realtime on a modest laptop. Voice models are
    ~60-100 MB each, distributed from the Piper GitHub releases. Setup:

        pip install piper-tts
        # download a voice model (see https://github.com/rhasspy/piper)
        export PIPER_MODEL=/abs/path/to/en-us-amy-low.onnx
        # optional Hindi voice:
        # export PIPER_MODEL_HI=/abs/path/to/hi_IN-priyamvada-medium.onnx

    Pick a soft, calm voice for the teacher — `amy-low` (en-US) and
    `priyamvada-medium` (hi-IN) read well at the default rate."""

    name = "piper"

    def __init__(
        self,
        model_path: Path,
        models_by_language: dict[str, Path] | None = None,
        length_scale: float = 1.15,
        noise_scale: float = 0.667,
        noise_w: float = 0.8,
    ):
        self.model_path = Path(model_path)
        self.models_by_language = models_by_language or {}
        # length_scale > 1.0 slows speech down — kinder for K-5 students
        # and gives the typewriter draw-on-board more room to breathe.
        self.length_scale = length_scale
        self.noise_scale = noise_scale
        self.noise_w = noise_w

    def _pick_model(self, language_code: str) -> Path:
        return self.models_by_language.get(language_code, self.model_path)

    def synthesise(self, text: str, language_code: str, out_path: Path) -> None:
        import shutil
        import subprocess
        import tempfile

        if shutil.which("piper") is None:
            raise RuntimeError(
                "piper not on PATH — run `pip install piper-tts`"
            )

        model = self._pick_model(language_code)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = Path(f.name)
        subprocess.run(
            [
                "piper",
                "--model", str(model),
                "--length_scale", f"{self.length_scale:.3f}",
                "--noise_scale", f"{self.noise_scale:.3f}",
                "--noise_w", f"{self.noise_w:.3f}",
                "--output_file", str(wav_path),
            ],
            input=text.encode("utf-8"),
            check=True, capture_output=True,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav_path),
             "-c:a", "libmp3lame", "-q:a", "4", str(out_path)],
            check=True, capture_output=True,
        )
        wav_path.unlink()


class BhashiniProvider:
    """Bhashini / ULCA inference-API TTS provider.

    Two-step protocol (per Bhashini docs):
      1. POST /ulca/apis/v0/model/getModelsPipeline with the task spec.
         The response carries the inference callback URL and the headers
         (key + value) we must echo back.
      2. POST <callback URL> with the text. Audio comes back base64-encoded
         in pipelineResponse[0].audio[0].audioContent.
    """

    name = "bhashini"
    PIPELINE_DISCOVERY_URL = (
        "https://meity-auth.ulcacontrib.org/ulca/apis/v0/model/getModelsPipeline"
    )

    def __init__(
        self,
        user_id: str,
        api_key: str,
        pipeline_id: str,
        timeout: float = 30.0,
    ):
        self.user_id = user_id
        self.api_key = api_key
        self.pipeline_id = pipeline_id
        self.timeout = timeout

    def _discover(self, language_code: str) -> tuple[str, dict[str, str], str]:
        """Call getModelsPipeline; return (callback_url, headers, service_id)."""
        import httpx

        body = {
            "pipelineTasks": [
                {
                    "taskType": "tts",
                    "config": {"language": {"sourceLanguage": language_code}},
                }
            ],
            "pipelineRequestConfig": {"pipelineId": self.pipeline_id},
        }
        resp = httpx.post(
            self.PIPELINE_DISCOVERY_URL,
            json=body,
            headers={
                "userID": self.user_id,
                "ulcaApiKey": self.api_key,
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        callback = data["pipelineInferenceAPIEndPoint"]["callbackUrl"]
        auth_name = data["pipelineInferenceAPIEndPoint"]["inferenceApiKey"]["name"]
        auth_value = data["pipelineInferenceAPIEndPoint"]["inferenceApiKey"]["value"]
        service_id = data["pipelineResponseConfig"][0]["config"][0]["serviceId"]
        return callback, {auth_name: auth_value}, service_id

    def synthesise(self, text: str, language_code: str, out_path: Path) -> None:
        import httpx

        callback_url, auth_headers, service_id = self._discover(language_code)
        body = {
            "pipelineTasks": [
                {
                    "taskType": "tts",
                    "config": {
                        "language": {"sourceLanguage": language_code},
                        "serviceId": service_id,
                        "gender": "female",
                    },
                }
            ],
            "inputData": {"input": [{"source": text}]},
        }
        resp = httpx.post(
            callback_url,
            json=body,
            headers={**auth_headers, "Content-Type": "application/json"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        audio_b64 = resp.json()["pipelineResponse"][0]["audio"][0]["audioContent"]
        out_path.write_bytes(base64.b64decode(audio_b64))


class ElevenLabsProvider:
    """ElevenLabs neural TTS / voice clone.

    Two ways to use it:

    1) Stock voice — pick any voice from your ElevenLabs library:
           ELEVENLABS_API_KEY=...
           ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM   # "Rachel", soft female
       Defaults to the Rachel voice if no voice_id is set.

    2) Cloned voice — clone a specific teacher's voice once (upload ~3 min
       of reference audio via the dashboard or POST /v1/voices/add), then
       set ELEVENLABS_VOICE_ID to that cloned voice. The lesson narration
       will sound like that teacher.

    Pricing (May 2026): ~$0.30 per 1k characters for premium voices;
    cloned voices are billed at the same rate. For a 7-min lesson
    (~5k characters of narration) that's ~₹125. Used at the Pro tier of
    the subscription matrix (see LEARN.md §7.1)."""

    name = "elevenlabs"

    def __init__(
        self,
        api_key: str,
        voice_id: str = "21m00Tcm4TlvDq8ikWAM",   # "Rachel" — soft female
        model_id: str = "eleven_multilingual_v2",
        stability: float = 0.55,
        similarity_boost: float = 0.8,
        timeout: float = 60.0,
    ):
        self.api_key = api_key
        self.voice_id = voice_id
        self.model_id = model_id
        self.stability = stability
        self.similarity_boost = similarity_boost
        self.timeout = timeout

    def synthesise(self, text: str, language_code: str, out_path: Path) -> None:
        import httpx

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}"
        body = {
            "text": text,
            "model_id": self.model_id,
            "voice_settings": {
                "stability": self.stability,
                "similarity_boost": self.similarity_boost,
            },
        }
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        resp = httpx.post(url, json=body, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        out_path.write_bytes(resp.content)


class EspeakProvider:
    """espeak-ng formant synthesis — last-ditch fully-offline fallback.

    Sounds robotic (no neural model — just formant synthesis from text),
    but it speaks every Indian language and runs on any Linux box with
    no network access. We keep it around for two cases:
      1) Airgapped / sandbox demos where Piper/Bhashini aren't reachable.
      2) Languages we don't yet have a Piper model for.

    Output is .wav then transcoded to .mp3 via ffmpeg to keep the same
    file extension contract as the other providers.
    """

    name = "espeak"

    # Map our ISO-639-1 codes → espeak-ng voice names. Some codes are
    # the same; the explicit map keeps this readable.
    _VOICE_MAP = {  # noqa: RUF012
        "en": "en-us", "hi": "hi-IN", "ta": "ta", "te": "te",
        "kn": "kn", "mr": "mr", "bn": "bn", "gu": "gu",
        "pa": "pa", "ml": "ml",
    }

    def __init__(self, rate_wpm: int = 145, pitch: int = 50):
        # 145 wpm + slight pitch drop reads more like a soft teacher than
        # the default robotic 175 wpm clip.
        self.rate_wpm = rate_wpm
        self.pitch = pitch
        import shutil
        if shutil.which("espeak-ng") is None:
            raise RuntimeError("espeak-ng not on PATH — apt-get install espeak-ng")
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg not on PATH — needed to transcode WAV→MP3")

    def synthesise(self, text: str, language_code: str, out_path: Path) -> None:
        import subprocess
        voice = self._VOICE_MAP.get(language_code, language_code)
        wav_path = out_path.with_suffix(".wav")
        subprocess.run(
            [
                "espeak-ng", "-v", voice,
                "-s", str(self.rate_wpm),
                "-p", str(self.pitch),
                "-w", str(wav_path),
                text,
            ],
            check=True, capture_output=True,
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(wav_path),
                "-b:a", "96k", str(out_path),
            ],
            check=True, capture_output=True,
        )
        wav_path.unlink(missing_ok=True)


def get_provider() -> TTSProvider:
    """Pick a provider from the environment. Preference order chosen to
    match the subscription matrix in LEARN.md §7.1:

      ElevenLabs (Pro tier — voice clone)
        > Bhashini (Standard tier — production Indic TTS)
            > Piper (Free tier — offline neural, listenable)
                > espeak-ng (universal offline fallback)
                    > gTTS (last-resort network fallback).

    Set `PADHAI_TTS_PROVIDER=espeak` to force espeak (useful for
    sandboxes / demos / languages without a Piper voice installed).
    """
    forced = os.environ.get("PADHAI_TTS_PROVIDER", "").lower()
    if forced == "espeak":
        return EspeakProvider()
    if os.environ.get("ELEVENLABS_API_KEY"):
        return ElevenLabsProvider(
            api_key=os.environ["ELEVENLABS_API_KEY"],
            voice_id=os.environ.get(
                "ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM",
            ),
            model_id=os.environ.get(
                "ELEVENLABS_MODEL_ID", "eleven_multilingual_v2",
            ),
        )
    if os.environ.get("BHASHINI_API_KEY"):
        return BhashiniProvider(
            user_id=os.environ.get("BHASHINI_USER_ID", ""),
            api_key=os.environ["BHASHINI_API_KEY"],
            pipeline_id=os.environ.get(
                "BHASHINI_PIPELINE_ID",
                "64392f96daac500b55c543cd",
            ),
        )
    piper_model = os.environ.get("PIPER_MODEL")
    if piper_model:
        per_lang: dict[str, Path] = {}
        for code in ("hi", "ta", "te", "kn", "mr", "bn", "gu", "pa", "ml"):
            env = os.environ.get(f"PIPER_MODEL_{code.upper()}")
            if env:
                per_lang[code] = Path(env)
        return PiperProvider(
            model_path=Path(piper_model),
            models_by_language=per_lang,
        )
    return GTTSProvider()
