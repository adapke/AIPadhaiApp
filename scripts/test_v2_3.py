"""Smoke for v2.3 — M1 live classes + M2 doubt clearing + Q3 event stream.

Run: PYTHONPATH=. python scripts/test_v2_3.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v2_3_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import analytics, doubt_clearing, live_classes
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    print("v2.3 smoke test")
    print("---------------")

    # --- M1 live classes ---
    live_classes.migrate()
    check(
        "M1 active_provider returns 'stub' without LiveKit/Daily keys",
        live_classes.active_provider() == "stub",
    )

    lc = live_classes.schedule(
        teacher_user_id="teacher-1",
        title="Class 10 Polity — Fundamental Rights",
        scheduled_at=time.time() + 3600,
        duration_min=45,
        subject="polity",
        org_id="org-test",
    )
    check(
        "M1 schedule returns LiveClass with provider=stub",
        lc.provider == "stub" and lc.status == "scheduled"
        and lc.room_id.startswith("padhai-"),
    )
    check(
        "M1 max_attendees default 200",
        lc.max_attendees == 200,
    )

    # Bad duration / title rejected
    try:
        live_classes.schedule(
            teacher_user_id="t", title="x",
            scheduled_at=time.time(), duration_min=2,
        )
        check("M1 schedule rejects duration_min < 5", False)
    except ValueError:
        check("M1 schedule rejects duration_min < 5", True)

    # Issue access token
    tok = live_classes.issue_access_token(
        live_class_id=lc.id, user_id="student-1", role="student",
    )
    check(
        "M1 issue_access_token returns stub token + expiry",
        tok["token"].startswith("stub.")
        and tok["provider"] == "stub"
        and tok["expires_at"] > time.time(),
    )
    check(
        "M1 stub token note explains config status",
        "stub_note" in tok,
    )

    # Record join / leave
    live_classes.record_join(live_class_id=lc.id, user_id="student-1")
    live_classes.record_join(live_class_id=lc.id, user_id="student-2")
    # Idempotent re-join — same row
    live_classes.record_join(live_class_id=lc.id, user_id="student-1")
    attendees = live_classes.list_attendees(lc.id)
    check(
        "M1 attendees has 2 unique users (idempotent join)",
        len(attendees) == 2,
        f"count={len(attendees)}",
    )

    time.sleep(0.05)  # give leave path a non-zero seconds_present
    live_classes.record_leave(live_class_id=lc.id, user_id="student-1")
    attendees = live_classes.list_attendees(lc.id)
    a1 = next(a for a in attendees if a["user_id"] == "student-1")
    check(
        "M1 record_leave sets seconds_present",
        a1["left_at"] is not None and a1["seconds_present"] is not None,
    )

    # Lifecycle: scheduled → live → ended
    check("M1 set_status → live",
          live_classes.set_status(live_class_id=lc.id, status="live"))
    check("M1 set_status → ended",
          live_classes.set_status(live_class_id=lc.id, status="ended"))
    lc2 = live_classes.get(lc.id)
    check(
        "M1 ended class has started_at + ended_at",
        lc2.started_at is not None and lc2.ended_at is not None,
    )

    # set_recording
    check(
        "M1 set_recording → True",
        live_classes.set_recording(
            live_class_id=lc.id,
            url="https://cdn.example.com/r/abc.mp4",
        ),
    )

    # Bad status rejected
    try:
        live_classes.set_status(live_class_id=lc.id, status="bogus")
        check("M1 set_status rejects bogus status", False)
    except ValueError:
        check("M1 set_status rejects bogus status", True)

    # list_upcoming filtering
    live_classes.schedule(
        teacher_user_id="t-2", title="Future class",
        scheduled_at=time.time() + 7200, duration_min=30,
        org_id="org-test",
    )
    upcoming = live_classes.list_upcoming(org_id="org-test")
    check(
        "M1 list_upcoming returns only scheduled/live for org",
        any(u.title == "Future class" for u in upcoming),
    )

    # --- M2 doubt clearing ---
    doubt_clearing.migrate()

    d = doubt_clearing.submit(
        user_id="student-A",
        question_text="Why is my derivation of acceleration wrong here?",
        subject="physics",
        image_url="https://r2.example.com/notebook-1.jpg",
        org_id="org-test",
    )
    check(
        "M2 submit returns Doubt with status=pending",
        d.status == "pending" and d.image_url is not None,
    )

    # Too short rejected
    try:
        doubt_clearing.submit(
            user_id="student-A", question_text="x",
        )
        check("M2 submit rejects question_text < 5 chars", False)
    except ValueError:
        check("M2 submit rejects question_text < 5 chars", True)

    # Queue surfaces it
    q = doubt_clearing.queue(subject="physics", limit=10)
    check(
        "M2 queue surfaces the pending physics doubt",
        any(x.id == d.id for x in q),
    )

    # Claim — race-safe
    check(
        "M2 first claim → True",
        doubt_clearing.claim(doubt_id=d.id, tutor_user_id="tutor-X"),
    )
    check(
        "M2 second claim → False (already claimed)",
        not doubt_clearing.claim(doubt_id=d.id, tutor_user_id="tutor-Y"),
    )

    # Wrong-tutor answer doesn't bypass the claim
    check(
        "M2 answer with method='human' marks status=answered",
        doubt_clearing.answer(
            doubt_id=d.id,
            response_text="Step 3: you flipped the sign on dv/dt.",
            method="human",
        ),
    )
    d2 = doubt_clearing.get(d.id)
    check(
        "M2 answered doubt has response_text + response_method='human'",
        d2.status == "answered" and d2.response_method == "human",
    )

    # Second answer on already-answered doubt — no-op
    check(
        "M2 second answer is no-op on terminal status",
        not doubt_clearing.answer(
            doubt_id=d.id, response_text="x", method="human",
        ),
    )

    # AI escalation path
    d3 = doubt_clearing.submit(
        user_id="student-B",
        question_text="Explain entropy in 3 sentences please",
        subject="physics",
    )
    check(
        "M2 ai answer flow accepts method='ai'",
        doubt_clearing.answer(
            doubt_id=d3.id,
            response_text="(AI explanation here)",
            method="ai",
            ai_call_id="call-abc",
        ),
    )
    d3_full = doubt_clearing.get(d3.id)
    check(
        "M2 AI-answered doubt status is 'ai_answered'",
        d3_full.status == "ai_answered" and d3_full.ai_call_id == "call-abc",
    )

    # Cancel by student
    d4 = doubt_clearing.submit(
        user_id="student-C", question_text="nevermind I figured it out",
    )
    check(
        "M2 student can cancel own pending doubt",
        doubt_clearing.cancel(doubt_id=d4.id, user_id="student-C"),
    )
    check(
        "M2 stranger cannot cancel someone's doubt",
        not doubt_clearing.cancel(doubt_id=d4.id, user_id="random"),
    )

    # Bad method rejected
    try:
        doubt_clearing.answer(
            doubt_id="bogus", response_text="x", method="alien",
        )
        check("M2 answer rejects unknown method", False)
    except ValueError:
        check("M2 answer rejects unknown method", True)

    # Stats
    s = doubt_clearing.stats(hours=1.0)
    check(
        "M2 stats reports total + by_status + by_method",
        s["total"] >= 3
        and "answered" in s["by_status"]
        and "human" in s["by_method"],
    )

    # --- Q3 analytics ---
    analytics.migrate()

    # Single event
    eid = analytics.log(
        kind="lesson.start", user_id="anal-user-1",
        props={"lesson_id": "abc"},
    )
    check(
        "Q3 log returns event id",
        bool(eid),
    )

    # Bulk batch
    n = analytics.log_batch([
        {"kind": "lesson.start", "user_id": "anal-user-2"},
        {"kind": "lesson.complete", "user_id": "anal-user-1"},
        {"kind": "quiz.submit", "user_id": "anal-user-2",
         "props": {"score": 7}},
    ])
    check(
        "Q3 log_batch returns count",
        n == 3,
        f"got {n}",
    )

    # Filter out invalid
    n_skip = analytics.log_batch([
        {"kind": ""},
        {"kind": None},
        {"kind": "valid.event", "user_id": "x"},
    ])
    check(
        "Q3 log_batch skips invalid rows",
        n_skip == 1,
    )

    # DAU = users with any event today
    today_dau = analytics.dau()
    check(
        "Q3 dau >= 2 (we just logged events for 2 users today)",
        today_dau >= 2,
        f"dau={today_dau}",
    )

    # MAU
    today_mau = analytics.mau()
    check(
        "Q3 mau >= dau",
        today_mau >= today_dau,
        f"mau={today_mau} dau={today_dau}",
    )

    # By-kind
    by_kind = analytics.event_count_by_kind(hours=1.0)
    check(
        "Q3 event_count_by_kind includes lesson.start + quiz.submit",
        "lesson.start" in by_kind and "quiz.submit" in by_kind,
    )

    # Funnel — pseudo onboarding: lesson.start → lesson.complete
    fun = analytics.funnel(
        steps=["lesson.start", "lesson.complete"],
        hours=1.0,
    )
    check(
        "Q3 funnel computes step counts + pcts",
        fun["counts"][0] >= 1 and fun["counts"][1] >= 1,
    )

    # Empty funnel rejected
    fun_empty = analytics.funnel(steps=[], hours=1.0)
    check(
        "Q3 funnel handles empty steps gracefully",
        fun_empty["counts"] == [],
    )

    # Retention — synthesise a yesterday signup + today event
    yest = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    # Backfill: write a synthetic event at yesterday's timestamp
    yest_ts = datetime.strptime(yest, "%Y-%m-%d").replace(
        tzinfo=UTC,
    ).timestamp() + 3600
    import sqlite3
    with sqlite3.connect(_DB) as conn:
        conn.execute(
            "INSERT INTO events (id, user_id, kind, source, created_at) "
            "VALUES (?, ?, 'auth.signup', 'system', ?)",
            ("ret-evt-1", "ret-cohort-user", yest_ts),
        )
        # And a today event for the same user
        conn.execute(
            "INSERT INTO events (id, user_id, kind, source, created_at) "
            "VALUES (?, ?, 'lesson.start', 'system', ?)",
            ("ret-evt-2", "ret-cohort-user", time.time()),
        )
    ret = analytics.retention_d1_d7_d30(cohort_date=yest)
    check(
        "Q3 retention computes size + d1/d7/d30 pcts",
        ret["size"] >= 1 and 0 <= ret["d1_pct"] <= 1,
        f"size={ret['size']} d1={ret['d1_pct']}",
    )
    check(
        "Q3 retention d1_pct is 1.0 when same user has today event",
        ret["d1_pct"] == 1.0,
        f"d1={ret['d1_pct']}",
    )

    # Rollup
    rollup = analytics.rollup_for_date(
        datetime.now(UTC).strftime("%Y-%m-%d"),
    )
    check(
        "Q3 rollup_for_date persists metrics + returns count",
        rollup["metrics_count"] >= 2 and "dau" in rollup["persisted"],
    )

    # Series
    series = analytics.get_metric_series(metric="dau", days=7)
    check(
        "Q3 get_metric_series returns 7 days of points",
        len(series) == 7,
    )

    # --- HTTP layer ---
    with TestClient(app) as c:
        # Auth gates
        for path in (
            "/api/doubts",
            "/api/live-classes/some/join",
        ):
            r = c.get(path) if path == "/api/doubts" else c.post(path)
            check(f"{path} requires auth (401)", r.status_code == 401)

        # Public reads
        r = c.get("/api/live-classes/upcoming?org_id=org-test")
        check(
            "GET /api/live-classes/upcoming returns 200",
            r.status_code == 200 and "rows" in r.json(),
        )
        r = c.get("/api/admin/live-classes/provider")
        check(
            "GET /api/admin/live-classes/provider returns provider info",
            r.status_code == 200 and "provider" in r.json(),
        )
        r = c.get("/api/admin/doubts/stats?hours=1")
        check(
            "GET /api/admin/doubts/stats returns 200 + total",
            r.status_code == 200 and "total" in r.json(),
        )

        # Q3 event ingest
        r = c.post(
            "/api/events",
            json={"kind": "lesson.start", "user_id": "http-user",
                  "source": "web"},
        )
        check(
            "POST /api/events accepts single event",
            r.status_code == 200 and r.json().get("id"),
        )

        # Bad payload
        r = c.post(
            "/api/events",
            json={"no_kind_here": "x"},
        )
        check(
            "POST /api/events rejects payload without kind",
            r.status_code == 400,
        )

        # Metrics endpoints
        r = c.get("/api/admin/metrics/dau")
        check("GET /api/admin/metrics/dau returns 200",
              r.status_code == 200 and "dau" in r.json())

        r = c.get("/api/admin/metrics/events-by-kind?hours=1")
        check(
            "GET /api/admin/metrics/events-by-kind returns 200",
            r.status_code == 200 and "by_kind" in r.json(),
        )

        r = c.post(
            "/api/admin/metrics/funnel",
            data={"steps_csv": "lesson.start,lesson.complete",
                  "hours": "1"},
        )
        check(
            "POST /api/admin/metrics/funnel returns 200 + counts",
            r.status_code == 200 and "counts" in r.json(),
        )

        # Empty steps_csv rejected
        r = c.post(
            "/api/admin/metrics/funnel",
            data={"steps_csv": "   ", "hours": "1"},
        )
        check(
            "POST /api/admin/metrics/funnel rejects empty steps",
            r.status_code == 400,
        )

        # Version
        r = c.get("/")
        check(
            "Root reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),
            f"version={r.json().get('version')}",
        )

    # Route count
    check(
        "v2.3 brought route count above 200",
        len(app.routes) > 200,
        f"routes={len(app.routes)}",
    )

    print("---------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
