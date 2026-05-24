"""Smoke for v1.8 — J3 curriculum scorer, J4 Sarvam, K1 indic polish.
Run: PYTHONPATH=. python scripts/test_v1_8.py
"""
from __future__ import annotations
import os, sys, tempfile
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v1_8_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient
    from padhai import curriculum_scorer, voice_sarvam, indic_polish
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    print("v1.8 smoke test")
    print("---------------")

    # J3 scorer
    curriculum_scorer.migrate()
    o1 = curriculum_scorer.upsert_objective(
        board="cbse", grade=8, subject="science",
        chapter="Photosynthesis",
        objective="Explain the process of photosynthesis in green plants.",
        revision="2024-25",
    )
    curriculum_scorer.upsert_objective(
        board="cbse", grade=8, subject="science",
        chapter="Photosynthesis",
        objective="Describe the role of chlorophyll in capturing sunlight.",
    )
    curriculum_scorer.upsert_objective(
        board="cbse", grade=8, subject="science",
        chapter="Photosynthesis",
        objective="Compare aerobic and anaerobic respiration in animals.",
    )
    objs = curriculum_scorer.list_objectives(
        board="cbse", grade=8, subject="science",
    )
    check("J3 list_objectives returns 3", len(objs) == 3)

    lesson = (
        "Photosynthesis is the process green plants use to make food. "
        "Chlorophyll in the leaves captures sunlight energy. Carbon "
        "dioxide and water are converted into glucose."
    )
    result = curriculum_scorer.score_lesson(
        lesson_text=lesson, board="cbse", grade=8, subject="science",
    )
    check(
        "J3 score_lesson hits the photosynthesis + chlorophyll objectives",
        len(result.covered) >= 1
        and len(result.missed) >= 1,
        f"covered={len(result.covered)} partial={len(result.partial)} "
        f"missed={len(result.missed)} score={result.score}",
    )
    check("J3 score in [0,100]", 0 <= result.score <= 100)

    # Idempotent re-upsert
    o1b = curriculum_scorer.upsert_objective(
        board="cbse", grade=8, subject="science",
        chapter="Photosynthesis",
        objective="Explain the process of photosynthesis in green plants.",
    )
    check("J3 upsert idempotent on natural key", o1.id == o1b.id)

    # J4 Sarvam
    check("J4 sarvam not available without API key", not voice_sarvam.is_available())
    try:
        voice_sarvam.synthesize(text="Hello", language="hi")
        check("J4 synthesize raises ProviderUnavailable without key", False)
    except voice_sarvam.ProviderUnavailable:
        check("J4 synthesize raises ProviderUnavailable without key", True)
    check(
        "J4 supports_language for Hindi",
        voice_sarvam.supports_language("hi"),
    )
    check(
        "J4 supports_language returns False for Punjabi-XX",
        not voice_sarvam.supports_language("pa-XX"),
    )

    # K1 indic polish
    ta = indic_polish.profile_for("ta")
    check(
        "K1 Tamil profile has Noto Sans Tamil font",
        "Noto Sans Tamil" in ta.font_family,
    )
    te = indic_polish.profile_for("te")
    check(
        "K1 Telugu speech rate slowed below 1.0",
        te.tts_speech_rate < 1.0,
        f"rate={te.tts_speech_rate}",
    )
    ml = indic_polish.profile_for("ml")
    check("K1 Malayalam requires ZWNJ", ml.requires_zwnj)
    default = indic_polish.profile_for("xx")
    check(
        "K1 unknown language falls back to default profile",
        default.language == "default",
    )

    # Normalize roundtrip
    norm = indic_polish.normalize_text(
        "Hello world,,", language="ta",
    )
    check(
        "K1 normalize collapses trailing commas to period (Tamil)",
        norm.endswith("."),
        norm,
    )
    # ZWNJ insertion check on a synthetic Malayalam pair
    ml_in = "രാമൻ വന്നു."
    ml_out = indic_polish.normalize_text(ml_in, language="ml")
    check(
        "K1 Malayalam normalize is idempotent (no double ZWNJ)",
        indic_polish.normalize_text(ml_out, language="ml") == ml_out,
    )

    # HTTP layer
    with TestClient(app) as c:
        r = c.get(
            "/api/curriculum/objectives?board=cbse&grade=8&subject=science",
        )
        check(
            "GET /api/curriculum/objectives returns rows",
            r.status_code == 200 and len(r.json()["rows"]) >= 3,
        )
        r = c.post(
            "/api/curriculum/score",
            data={
                "lesson_text": lesson, "board": "cbse",
                "grade": "8", "subject": "science",
            },
        )
        check(
            "POST /api/curriculum/score returns score + lists",
            r.status_code == 200 and "score" in r.json()
            and isinstance(r.json()["covered"], list),
        )
        r = c.get("/api/tts/providers")
        check(
            "GET /api/tts/providers lists sarvam + bhashini + piper",
            r.status_code == 200
            and {p["name"] for p in r.json()["providers"]}
                >= {"sarvam_bulbul", "bhashini", "piper"},
        )
        r = c.get("/api/indic/profile?language=te")
        check(
            "GET /api/indic/profile returns Telugu profile",
            r.status_code == 200 and r.json()["language"] == "te",
        )
        r = c.post(
            "/api/indic/normalize",
            data={"text": "test,,", "language": "ta"},
        )
        check(
            "POST /api/indic/normalize returns normalized text",
            r.status_code == 200 and r.json()["normalized"].endswith("."),
        )
        r = c.get("/")
        check(
            "Root reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),)

    print("---------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed: print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
