"""Smoke for v3.9 — Socratic tutor mode.

Covers:
- Exchange start + state machine (diagnose → hint → check → reveal)
- Confusion detection (short text, idk patterns, slow + short)
- Reveal-demand detection (explicit asks)
- Confusion tolerance breach forces reveal
- HTTP endpoints + auth gates

Run: PYTHONPATH=. python scripts/test_v3_9.py
"""
from __future__ import annotations
import os, sys, tempfile, time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v3_9_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient
    from padhai import socratic_tutor as st
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

    print("v3.9 smoke test — Socratic tutor")
    print("--------------------------------")

    st.migrate()
    st.migrate()  # idempotent

    # ============================================================
    # Detection helpers
    # ============================================================
    check(
        "_is_confused: 'idk' → True",
        st._is_confused("idk", None),
    )
    check(
        "_is_confused: 'I don't know' → True",
        st._is_confused("I don't know", None),
    )
    check(
        "_is_confused: 'I do not understand' → True",
        st._is_confused("I do not understand the formula", None),
    )
    check(
        "_is_confused: empty / whitespace → True",
        st._is_confused("   ", None),
    )
    check(
        "_is_confused: 'because plants absorb sunlight' → False",
        not st._is_confused(
            "because plants absorb sunlight via chlorophyll",
            None,
        ),
    )
    check(
        "_is_confused: short reply after slow timer → True",
        st._is_confused("ok", 90),
    )
    check(
        "_is_confused: short reply within fast timer → False",
        not st._is_confused("ok", 5),
    )

    check(
        "_wants_reveal: 'just tell me the answer' → True",
        st._wants_reveal("just tell me the answer"),
    )
    check(
        "_wants_reveal: 'I give up' → True",
        st._wants_reveal("I give up please reveal"),
    )
    check(
        "_wants_reveal: normal answer → False",
        not st._wants_reveal("I think it's because A is true"),
    )

    # ============================================================
    # Start exchange
    # ============================================================
    ex = st.start_exchange(
        session_id="sess-1", user_id="stu-1",
        student_question="What causes photosynthesis to happen?",
        target_depth=3,
    )
    check(
        "start_exchange returns state='diagnose'",
        ex.state == "diagnose" and ex.target_depth == 3,
    )
    check(
        "Initial turns contains student question",
        len(ex.turns) == 1 and ex.turns[0]["role"] == "student",
    )

    # Bad question (too short)
    check(
        "start_exchange rejects too-short question",
        raises(st.start_exchange, exc_type=ValueError,
               session_id="s", user_id="u",
               student_question="hi"),
    )

    # Bad depth
    check(
        "start_exchange rejects depth out of range",
        raises(st.start_exchange, exc_type=ValueError,
               session_id="s", user_id="u",
               student_question="A real question to ask?",
               target_depth=99),
    )

    # ============================================================
    # Advance: diagnose → hint
    # ============================================================

    # Tutor responds with diagnose sub-question
    st.append_tutor_turn(
        exchange_id=ex.id,
        tutor_text="Before I answer, what do plants need from "
                   "their environment to make food?",
    )

    # Student gives a thoughtful answer (not confused)
    adv1 = st.advance(
        exchange_id=ex.id, user_id="stu-1",
        student_text="Plants need sunlight and water and CO2",
        time_seconds=20,
    )
    check(
        "diagnose → hint after thoughtful answer",
        adv1.next_state == "hint" and not adv1.student_confused,
    )

    # Tutor drops a hint
    st.append_tutor_turn(
        exchange_id=ex.id,
        tutor_text="Right — and what does the leaf use to "
                   "trap that sunlight specifically?",
    )

    # Student responds — not confused, gives reasonable answer
    adv2 = st.advance(
        exchange_id=ex.id, user_id="stu-1",
        student_text="Chlorophyll inside the leaves",
        time_seconds=15,
    )
    check(
        "hint → check after thoughtful answer",
        adv2.next_state == "check",
    )

    # ============================================================
    # Confusion handling — back off from check to hint
    # ============================================================
    st.append_tutor_turn(
        exchange_id=ex.id,
        tutor_text="So can you put it together — what 3 ingredients "
                   "+ chlorophyll yields what product?",
    )
    adv3 = st.advance(
        exchange_id=ex.id, user_id="stu-1",
        student_text="idk",
        time_seconds=10,
    )
    check(
        "check + confusion → backs to hint",
        adv3.next_state == "hint"
        and adv3.student_confused,
    )

    ex_now = st.get_exchange(ex.id)
    check(
        "confusion_count bumped to 1",
        ex_now.confusion_count == 1,
    )

    # ============================================================
    # Reveal demand — student asks for the answer
    # ============================================================
    st.append_tutor_turn(
        exchange_id=ex.id,
        tutor_text="Think of the chemical formula...",
    )
    adv4 = st.advance(
        exchange_id=ex.id, user_id="stu-1",
        student_text="just tell me the answer please",
    )
    check(
        "explicit reveal-demand → next_state='reveal'",
        adv4.next_state == "reveal"
        and adv4.student_demanded_reveal,
    )

    # ============================================================
    # Reveal flow — records final answer + provenance
    # ============================================================
    revealed = st.reveal(
        exchange_id=ex.id, user_id="stu-1",
        final_answer=(
            "Photosynthesis happens when chlorophyll in plant leaves "
            "absorbs sunlight and uses that energy to convert CO2 "
            "and water into glucose, releasing O2 as a byproduct."
        ),
        citations=[
            {"source_kind": "upload",
             "source_id": "bio-ncert-class10",
             "page_number": 102,
             "citation_text": "Photosynthesis: 6CO2 + 6H2O → C6H12O6 + 6O2",
             "relevance": 0.91},
        ],
    )
    check(
        "reveal sets state='reveal' + final_answer + completed_at",
        revealed.state == "reveal"
        and revealed.final_answer is not None
        and revealed.completed_at is not None,
    )
    check(
        "reveal records provenance_id",
        revealed.final_provenance_id is not None,
    )

    # Final turn contains the reveal step
    last_turn = revealed.turns[-1]
    check(
        "Final turn marked step='reveal' with provenance_id",
        last_turn.get("step") == "reveal"
        and last_turn.get("provenance_id") is not None,
    )

    # Cannot advance an already-revealed exchange
    check(
        "advance refuses revealed exchange",
        raises(st.advance, exc_type=ValueError,
               exchange_id=ex.id, user_id="stu-1",
               student_text="more please"),
    )

    # ============================================================
    # Confusion tolerance breach forces reveal
    # ============================================================
    ex2 = st.start_exchange(
        session_id="sess-2", user_id="stu-2",
        student_question="Explain Article 21 in detail?",
    )
    st.append_tutor_turn(
        exchange_id=ex2.id, tutor_text="What rights does it protect?",
    )
    # 3 confused responses in a row should force reveal after 1 attempt
    st.advance(
        exchange_id=ex2.id, user_id="stu-2",
        student_text="idk", time_seconds=5,
    )
    st.append_tutor_turn(
        exchange_id=ex2.id, tutor_text="Think about life and...",
    )
    st.advance(
        exchange_id=ex2.id, user_id="stu-2",
        student_text="not sure", time_seconds=5,
    )
    st.append_tutor_turn(
        exchange_id=ex2.id, tutor_text="Liberty maybe?",
    )
    adv_fail = st.advance(
        exchange_id=ex2.id, user_id="stu-2",
        student_text="huh confused", time_seconds=5,
    )
    check(
        "Confusion tolerance breached → forces reveal state",
        adv_fail.next_state == "reveal",
    )

    # ============================================================
    # Ownership + abandon
    # ============================================================
    ex3 = st.start_exchange(
        session_id="s-x", user_id="stu-3",
        student_question="Tell me about gravity?",
    )
    check(
        "advance refuses non-owner",
        raises(st.advance, exc_type=PermissionError,
               exchange_id=ex3.id, user_id="stranger",
               student_text="hi"),
    )
    check(
        "reveal refuses non-owner",
        raises(st.reveal, exc_type=PermissionError,
               exchange_id=ex3.id, user_id="stranger",
               final_answer="Gravity attracts masses."),
    )

    check(
        "abandon by owner → True",
        st.abandon(exchange_id=ex3.id, user_id="stu-3"),
    )
    check(
        "abandon idempotent → False",
        not st.abandon(exchange_id=ex3.id, user_id="stu-3"),
    )

    # ============================================================
    # Listing + stats
    # ============================================================
    mine = st.list_user_exchanges("stu-1")
    check(
        "list_user_exchanges returns user's exchanges",
        len(mine) >= 1,
    )

    stats = st.user_stats("stu-1")
    check(
        "user_stats returns exchanges + completed_rate",
        stats["exchanges"] >= 1
        and "completed_rate" in stats
        and "avg_confusion_per_exchange" in stats,
    )

    # Bad state filter
    check(
        "list_user_exchanges rejects bad state",
        raises(st.list_user_exchanges, exc_type=ValueError,
               user_id="stu-1", state="cosmic"),
    )

    # ============================================================
    # HTTP layer
    # ============================================================
    with TestClient(app) as c:
        # Auth gates
        r = c.post(
            "/api/socratic/exchanges",
            data={"session_id": "s",
                  "student_question": "Hi there what is this?"},
        )
        check(
            "POST /api/socratic/exchanges → 401",
            r.status_code == 401,
        )

        r = c.post(
            "/api/socratic/exchanges/x/advance",
            data={"student_text": "answer"},
        )
        check(
            "POST /api/socratic/exchanges/{}/advance → 401",
            r.status_code == 401,
        )

        r = c.get("/api/socratic/me")
        check(
            "GET /api/socratic/me → 401",
            r.status_code == 401,
        )

        r = c.get("/api/socratic/me/stats")
        check(
            "GET /api/socratic/me/stats → 401",
            r.status_code == 401,
        )

        # Form validation: question too short
        r = c.post(
            "/api/socratic/exchanges",
            data={"session_id": "s", "student_question": "hi"},
        )
        check(
            "POST /api/socratic/exchanges rejects short question (422)",
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
        "v3.9 brought route count above 500",
        len(app.routes) > 500,
        f"routes={len(app.routes)}",
    )

    print("--------------------------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
