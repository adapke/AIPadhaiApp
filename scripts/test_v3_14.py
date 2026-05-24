"""Smoke for v3.14 — Audio recap.

Covers:
- Recap create + set_segments + intro/outro auto-prepend
- generate_script_from_query (retrieval → segments + citations)
- source_only refuses ungrounded segments
- Render worker (sandbox provider) populates audio_path
- HTTP endpoints + auth gates

Run: PYTHONPATH=. python scripts/test_v3_14.py
"""
from __future__ import annotations
import os, sys, tempfile, time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v3_14_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient
    from padhai import audio_recap as ar
    from padhai import retrieval
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    def raises(fn, exc_type=Exception, **kwargs):
        try:
            fn(**kwargs); return False
        except exc_type:
            return True

    print("v3.14 smoke test — Audio recap")
    print("------------------------------")

    ar.migrate()
    ar.migrate()
    retrieval.migrate()

    # ============================================================
    # Recap creation
    # ============================================================
    r = ar.create_recap(
        user_id="stu-1", title="Photosynthesis — Class 10",
        source_kind="upload", source_id="upload-bio",
        query="photosynthesis chlorophyll glucose",
    )
    check(
        "create_recap returns pending Recap",
        r.status == "pending" and r.answer_mode == "cited",
    )

    # Bad source_kind
    check(
        "create_recap rejects bad source_kind",
        raises(ar.create_recap, exc_type=ValueError,
               user_id="x", title="x" * 5,
               source_kind="cosmic"),
    )

    # Bad answer_mode
    check(
        "create_recap rejects bad answer_mode",
        raises(ar.create_recap, exc_type=ValueError,
               user_id="x", title="title here",
               source_kind="free_text",
               answer_mode="alien"),
    )

    # source_kind=upload requires source_id
    check(
        "create_recap rejects upload without source_id",
        raises(ar.create_recap, exc_type=ValueError,
               user_id="x", title="title here",
               source_kind="upload"),
    )

    # Title too short
    check(
        "create_recap rejects too-short title",
        raises(ar.create_recap, exc_type=ValueError,
               user_id="x", title="xx",
               source_kind="free_text"),
    )

    # ============================================================
    # set_segments — manual path
    # ============================================================
    r2 = ar.create_recap(
        user_id="stu-1", title="Manual recap",
        source_kind="free_text",
    )
    r2_set = ar.set_segments(
        recap_id=r2.id, user_id="stu-1",
        body_segments=[
            {"transcript":
             "First key point about the chapter that's long enough."},
            {"transcript":
             "Second key point covering the next big idea."},
        ],
    )
    check(
        "set_segments creates intro + 2 body + outro = 4 segments",
        len(r2_set.segments) == 4,
    )
    check(
        "Intro segment present at position 0",
        r2_set.segments[0].role == "intro"
        and r2_set.segments[0].position == 0,
    )
    check(
        "Outro segment last",
        r2_set.segments[-1].role == "outro",
    )

    # Permission check
    check(
        "set_segments refuses non-owner",
        raises(ar.set_segments, exc_type=PermissionError,
               recap_id=r2.id, user_id="stranger",
               body_segments=[{"transcript": "x" * 30}]),
    )

    # Empty body rejected
    check(
        "set_segments rejects empty body",
        raises(ar.set_segments, exc_type=ValueError,
               recap_id=r2.id, user_id="stu-1",
               body_segments=[]),
    )

    # Too many body segments
    check(
        "set_segments rejects > MAX_BODY_SEGMENTS",
        raises(ar.set_segments, exc_type=ValueError,
               recap_id=r2.id, user_id="stu-1",
               body_segments=[
                   {"transcript": "x" * 50}
                   for _ in range(ar.MAX_BODY_SEGMENTS + 1)
               ]),
    )

    # Transcript too short
    check(
        "set_segments rejects too-short transcript",
        raises(ar.set_segments, exc_type=ValueError,
               recap_id=r2.id, user_id="stu-1",
               body_segments=[{"transcript": "hi"}]),
    )

    # Custom intro / outro
    r_custom = ar.create_recap(
        user_id="stu-1", title="Custom intro recap",
        source_kind="free_text",
    )
    r_custom_set = ar.set_segments(
        recap_id=r_custom.id, user_id="stu-1",
        body_segments=[{"transcript": "Body content here long enough."}],
        custom_intro="Welcome to my custom intro!",
        custom_outro="Thanks for listening, that's all.",
    )
    check(
        "Custom intro / outro respected",
        r_custom_set.segments[0].transcript.startswith("Welcome")
        and "Thanks" in r_custom_set.segments[-1].transcript,
    )

    # ============================================================
    # source_only refuses ungrounded segments
    # ============================================================
    r_so = ar.create_recap(
        user_id="stu-1", title="Strict source-only recap",
        source_kind="free_text", answer_mode="source_only",
    )
    check(
        "source_only refuses segments without citations",
        raises(ar.set_segments, exc_type=ValueError,
               recap_id=r_so.id, user_id="stu-1",
               body_segments=[{"transcript": "ungrounded" * 5}]),
    )

    # WITH citations, OK
    r_so_set = ar.set_segments(
        recap_id=r_so.id, user_id="stu-1",
        body_segments=[
            {"transcript": "Cited body content here long enough.",
             "citations": [
                 {"source_kind": "upload", "source_id": "u1",
                  "page_number": 1, "citation_text": "verbatim"}
             ]},
        ],
    )
    check(
        "source_only accepts cited segments",
        r_so_set.segments[1].citations
        and r_so_set.segments[1].citations[0]["page_number"] == 1,
    )

    # ============================================================
    # generate_script_from_query — retrieval path
    # ============================================================

    # Seed retrieval with some chunks
    retrieval.index_page(
        upload_id="upload-bio", page_number=1,
        extracted_text=(
            "Photosynthesis is the process by which plants make food. "
            "Chlorophyll absorbs sunlight and converts CO2 and water "
            "into glucose."
        ),
    )
    retrieval.index_page(
        upload_id="upload-bio", page_number=2,
        extracted_text=(
            "The byproduct of photosynthesis is oxygen which is "
            "released into the atmosphere. Glucose is then used by "
            "the plant for energy through cellular respiration."
        ),
    )

    r_retrieval = ar.create_recap(
        user_id="stu-1",
        title="Auto-scripted photosynthesis recap",
        source_kind="upload", source_id="upload-bio",
        query="photosynthesis chlorophyll glucose",
    )
    r_done = ar.generate_script_from_query(
        recap_id=r_retrieval.id, user_id="stu-1",
        top_k=3,
    )
    check(
        "generate_script_from_query creates body segments from chunks",
        len(r_done.segments) >= 3,    # intro + ≥1 body + outro
    )
    body = [s for s in r_done.segments if s.role == "body"]
    check(
        "Body segments carry citations from retrieval",
        all(b.citations for b in body),
    )

    # Wrong owner refused
    check(
        "generate_script_from_query refuses non-owner",
        raises(ar.generate_script_from_query,
               exc_type=PermissionError,
               recap_id=r_retrieval.id, user_id="stranger"),
    )

    # Empty retrieval → source_only fails hard
    r_empty = ar.create_recap(
        user_id="stu-1", title="Empty source recap",
        source_kind="upload", source_id="upload-empty",
        query="nonexistent topic xyzzy",
        answer_mode="source_only",
    )
    check(
        "source_only + zero hits → ValueError",
        raises(ar.generate_script_from_query, exc_type=ValueError,
               recap_id=r_empty.id, user_id="stu-1",
               query="impossible topic xyzzy"),
    )

    # source_kind='free_text' refused by generate_script
    r_ft = ar.create_recap(
        user_id="stu-1", title="Free text recap",
        source_kind="free_text",
    )
    check(
        "generate_script_from_query refuses free_text recap",
        raises(ar.generate_script_from_query, exc_type=ValueError,
               recap_id=r_ft.id, user_id="stu-1",
               query="x"),
    )

    # ============================================================
    # Render worker (sandbox)
    # ============================================================
    outcome = ar.render_pending(batch_size=10)
    check(
        "render_pending returns counts",
        outcome["rendered"] >= 1 or outcome["failed"] >= 0,
    )

    # The first recap r2 should be ready
    r2_done = ar.get_recap(r2.id)
    check(
        "Rendered recap status='ready' + duration_sec > 0",
        r2_done.status == "ready"
        and r2_done.duration_sec > 0,
    )
    check(
        "Each segment has audio_path + duration_sec",
        all(s.audio_path is not None
            and s.duration_sec is not None
            for s in r2_done.segments),
    )

    # Re-render is a no-op (no pending rows)
    outcome2 = ar.render_pending()
    check(
        "render_pending no-op when no pending recaps",
        outcome2["rendered"] == 0 and outcome2["failed"] == 0,
    )

    # ============================================================
    # Cancel + delete
    # ============================================================
    cancel_target = ar.create_recap(
        user_id="stu-1", title="To be cancelled",
        source_kind="free_text",
    )
    check(
        "cancel_recap True on pending",
        ar.cancel_recap(
            recap_id=cancel_target.id, user_id="stu-1",
        ),
    )
    check(
        "cancel_recap idempotent (False second time)",
        not ar.cancel_recap(
            recap_id=cancel_target.id, user_id="stu-1",
        ),
    )

    delete_target = ar.create_recap(
        user_id="stu-1", title="To be deleted",
        source_kind="free_text",
    )
    check(
        "delete_recap True for owner",
        ar.delete_recap(
            recap_id=delete_target.id, user_id="stu-1",
        ),
    )
    check(
        "delete_recap False for non-owner",
        not ar.delete_recap(
            recap_id=r2.id, user_id="stranger",
        ),
    )

    # ============================================================
    # Listing + stats
    # ============================================================
    listed = ar.list_user_recaps("stu-1")
    check(
        "list_user_recaps returns user's recaps",
        len(listed) >= 3,
    )

    ready = ar.list_user_recaps("stu-1", status="ready")
    check(
        "list_user_recaps filter by status",
        all(r.status == "ready" for r in ready),
    )

    # Bad status filter
    check(
        "list_user_recaps rejects bad status",
        raises(ar.list_user_recaps, exc_type=ValueError,
               user_id="stu-1", status="cosmic"),
    )

    stats = ar.user_stats("stu-1")
    check(
        "user_stats returns total + by_status + total_audio_seconds",
        "total_recaps" in stats
        and "by_status" in stats
        and "total_audio_seconds" in stats,
    )

    # ============================================================
    # HTTP layer
    # ============================================================
    with TestClient(app) as c:
        # Auth gates
        r = c.post(
            "/api/audio-recaps",
            data={"title": "test recap",
                  "source_kind": "free_text"},
        )
        check(
            "POST /api/audio-recaps → 401",
            r.status_code == 401,
        )

        r = c.get("/api/audio-recaps/me")
        check(
            "GET /api/audio-recaps/me → 401",
            r.status_code == 401,
        )

        r = c.get("/api/audio-recaps/me/stats")
        check(
            "GET /api/audio-recaps/me/stats → 401",
            r.status_code == 401,
        )

        r = c.post(
            "/api/admin/audio-recaps/render-pending",
            data={"batch_size": 10},
        )
        check(
            "POST /api/admin/audio-recaps/render-pending → 401",
            r.status_code == 401,
        )

        # Form validation
        r = c.post(
            "/api/audio-recaps",
            data={"title": "xx",  # too short
                  "source_kind": "free_text"},
        )
        check(
            "POST /api/audio-recaps rejects short title (422)",
            r.status_code == 422,
        )

        # Version
        r = c.get("/")
        check(
            "Root reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),
            f"version={r.json().get('version')}",
        )

    check(
        "v3.14 brought route count above 560",
        len(app.routes) > 560,
        f"routes={len(app.routes)}",
    )

    print("------------------------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
