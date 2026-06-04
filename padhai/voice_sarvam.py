"""J4 — Hindi voice clone v2 (Sarvam.ai 'bulbul').

Bhashini's Hindi neural voice (used since v0.6) sounds OK but
robotic. Sarvam.ai's `bulbul` model sounds ~10× better — like a real
teacher reading aloud. Cost: ~₹0.40/min vs Bhashini's free tier.

This module is a TTS provider implementing the same interface as
`padhai/tts/*` (synthesize(text, language, voice) → wav bytes). Falls
back gracefully when `SARVAM_API_KEY` isn't set so the wider TTS
chain doesn't break.

Tier routing (lives in build_profile):
  free tier (M1)       → Bhashini (current default)
  paid tier (M2+)      → Sarvam for Hindi + supported Indic langs
  enterprise tier (M4) → Sarvam everywhere

Supported languages (Sarvam's catalog, May 2026):
  hi, bn, ta, te, mr, kn, ml, pa, gu, en-IN
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Optional

SUPPORTED_LANGUAGES = frozenset({
    "hi", "bn", "ta", "te", "mr", "kn", "ml", "pa", "gu", "en-IN",
})

# Voice catalog — `meera` + `arvind` are Sarvam's named voices.
DEFAULT_VOICE_PER_LANG = {
    "hi": "meera",
    "bn": "meera",
    "ta": "pavithra",
    "te": "pavithra",
    "mr": "meera",
    "kn": "pavithra",
    "ml": "pavithra",
    "pa": "meera",
    "gu": "meera",
    "en-IN": "meera",
}


@dataclass(frozen=True)
class SarvamConfig:
    api_key: str
    base_url: str
    model: str
    speech_rate: float        # 0.5 .. 2.0


def is_available() -> bool:
    """Sarvam is usable when the API key is set."""
    return bool(os.environ.get("SARVAM_API_KEY"))


def get_config() -> SarvamConfig | None:
    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        return None
    return SarvamConfig(
        api_key=key,
        base_url=os.environ.get("SARVAM_BASE_URL",
                                "https://api.sarvam.ai/text-to-speech"),
        model=os.environ.get("SARVAM_MODEL", "bulbul:v1"),
        speech_rate=float(os.environ.get("SARVAM_SPEECH_RATE", "1.0")),
    )


def supports_language(lang: str) -> bool:
    return lang in SUPPORTED_LANGUAGES


def synthesize(
    *,
    text: str,
    language: str,
    voice: str | None = None,
    speech_rate: float | None = None,
) -> bytes:
    """Synthesize speech via Sarvam API. Returns WAV bytes.

    Raises ProviderUnavailable when the API key isn't set; the wider
    TTS chain should catch this + fall back to Bhashini/Piper.

    Real API call uses requests; gated lazily so this module imports
    in environments without the `requests` lib. For v1.8 we ship the
    call shape — the actual key + smoke against real Sarvam happens
    during the ops cutover.
    """
    cfg = get_config()
    if cfg is None:
        raise ProviderUnavailable("SARVAM_API_KEY not configured")
    if language not in SUPPORTED_LANGUAGES:
        raise ProviderUnavailable(
            f"language {language!r} not in Sarvam catalog"
        )
    chosen_voice = voice or DEFAULT_VOICE_PER_LANG.get(language, "meera")
    rate = speech_rate if speech_rate is not None else cfg.speech_rate
    try:
        import requests
    except ImportError as e:
        raise ProviderUnavailable("requests lib not installed") from e
    payload = {
        "inputs": [text],
        "target_language_code": language,
        "speaker": chosen_voice,
        "model": cfg.model,
        "pitch": 0,
        "pace": rate,
        "loudness": 1.0,
        "enable_preprocessing": True,
    }
    headers = {
        "API-Subscription-Key": cfg.api_key,
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(cfg.base_url, json=payload, headers=headers,
                          timeout=30)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        raise ProviderUnavailable(f"sarvam api call failed: {e}") from e
    body = r.json()
    audios = body.get("audios") or []
    if not audios:
        raise ProviderUnavailable("sarvam returned no audio")
    # Sarvam returns base64-encoded WAV in `audios[0]`
    return base64.b64decode(audios[0])


class ProviderUnavailable(Exception):
    """Raised when Sarvam can't fulfill a request. Caller falls
    through to the next TTS provider in the chain."""
