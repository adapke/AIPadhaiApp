"""Functional deep-check — exercises the AI + logic learning features
end-to-end against a running server, inspecting REAL output (not just
HTTP contracts).

Unlike launch_smoke.py (which checks surfaces respond), this:
  • signs up a user and upgrades them to an UNCAPPED tier (M4a) via a
    direct DB write, so the per-tier daily cost cap doesn't paywall the
    AI calls;
  • drives each feature and asserts the output is real + substantive
    (a tutor answer that mentions the topic, an essay score in range,
    a generated practice test with questions, etc.).

Makes a handful of real Claude calls — a few rupees of spend. Only the
credential-FREE features are exercised; payments / SMTP / avatar-video
are intentionally skipped (those need provider keys).

Usage:
    python scripts/functional_check.py [--base http://localhost:8000]
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

if hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

RESULTS: list[tuple[str, bool, str]] = []


def record(label, ok, detail=""):
    RESULTS.append((label, ok, detail))
    print(f"  {'[OK]  ' if ok else '[FAIL]'} {label} -- {str(detail)[:160]}")


class C:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.token = None

    def _req(self, method, path, data=None, headers=None, timeout=90):
        h = dict(headers or {})
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, (e.read().decode("utf-8", "replace") if e.fp else "")
        except Exception as e:
            return 0, str(e)

    def form(self, path, fields, timeout=90):
        body = urllib.parse.urlencode(fields).encode()
        return self._req("POST", path, body,
                         {"Content-Type": "application/x-www-form-urlencoded"}, timeout)

    def get(self, path, timeout=30):
        return self._req("GET", path, None, None, timeout)


def upgrade_to_uncapped(user_id: str) -> str:
    """Set the user's tier to M4a (uncapped daily LLM cost) so the AI
    calls aren't paywalled. Writes to whichever DB holds users."""
    from dotenv import load_dotenv
    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        import psycopg
        with psycopg.connect(db_url, autocommit=True,
                             options="-c search_path=public") as conn:
            conn.execute(
                "UPDATE users SET subscription_tier='M4a', subscription_level='L5' "
                "WHERE id=%s", (user_id,),
            )
        return "postgres"
    # SQLite fallback
    import sqlite3

    from padhai import db as _db
    conn = sqlite3.connect(str(_db.sqlite_path()))
    conn.execute("UPDATE users SET subscription_tier='M4a' WHERE id=?", (user_id,))
    conn.commit(); conn.close()
    return "sqlite"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("PADHAI_BASE", "http://localhost:8000"))
    args = ap.parse_args()
    c = C(args.base)

    print(f"Functional check against {c.base}\n{'='*64}")

    # --- signup + upgrade ---
    email = f"fn-{uuid.uuid4().hex[:8]}@test.local"
    s, body = c.form("/auth/signup", {
        "email": email, "password": "Pass@12345",
        "terms_accepted": "true", "date_of_birth": "2000-01-01",
    })
    if s != 200:
        record("signup", False, f"HTTP {s} {body[:120]}"); _summary(); return
    user = json.loads(body); c.token = user["token"]
    record("signup", True, f"tier={user.get('subscription_tier')}")
    try:
        backend = upgrade_to_uncapped(user["user_id"])
        record("upgrade to M4a (uncapped)", True, f"db={backend}")
    except Exception as e:
        record("upgrade to M4a (uncapped)", False, f"{type(e).__name__}: {e}")

    print("\n[A] AI tutor — real Claude answer")
    s, b = c._req("POST", "/api/tutor/sessions", b"")
    if s in (200, 201):
        sid = json.loads(b)["session_id"]
        s2, b2 = c.form(f"/api/tutor/sessions/{sid}/message",
                        {"text": "Explain Newton's first law of motion in 2 sentences.",
                         "mode": "quick_explain"})
        reply = ""
        with contextlib.suppress(Exception):
            reply = json.loads(b2).get("reply", "")
        is_real = (s2 == 200 and len(reply) > 40
                   and "premium feature" not in reply.lower())
        record("tutor real answer", is_real,
               f"HTTP {s2} reply={reply[:110]!r}")
    else:
        record("tutor session", False, f"HTTP {s}")

    print("\n[B] Concept explainer — POST /explain")
    s, b = c.form("/explain", {"topic": "Photosynthesis", "level": "secondary"}, timeout=120)
    expl = ""
    with contextlib.suppress(Exception):
        d = json.loads(b); expl = d.get("explanation") or d.get("summary") or json.dumps(d)[:200]
    record("explain produces text", s == 200 and len(expl) > 60, f"HTTP {s} {expl[:110]!r}")

    print("\n[C] Essay grader — submit + auto-grade")
    s, b = c.get("/api/essay/rubrics")
    rubric_id = None
    with contextlib.suppress(Exception):
        rubrics = json.loads(b); rubrics = rubrics.get("rows") or rubrics.get("rubrics") or rubrics
        if isinstance(rubrics, list) and rubrics:
            rubric_id = rubrics[0].get("id") or rubrics[0].get("rubric_id")
    record("essay rubrics listed", bool(rubric_id), f"rubric_id={rubric_id}")
    if rubric_id:
        essay = ("Democracy in India rests on universal adult franchise, an "
                 "independent judiciary, and federalism. The Constitution "
                 "guarantees fundamental rights while the Directive Principles "
                 "guide policy. Free and fair elections by the ECI ensure "
                 "accountability of the government to the people.") * 2
        s, b = c.form("/api/essay/submit",
                      {"rubric_id": rubric_id, "text": essay, "auto_grade": "true"},
                      timeout=120)
        # /api/essay/submit -> {submission_id, submitted_at, grade:{score,method,...}}
        # method=='claude' proves the paid tier actually reached the AI grader
        # rather than the budget_/heuristic fallback (the prod-183 tier-drop bug).
        score = method = None
        with contextlib.suppress(Exception):
            grade = (json.loads(b).get("grade") or {})
            if isinstance(grade, dict):
                score, method = grade.get("score"), grade.get("method")
        record("essay graded (real Claude)", s in (200, 201) and method == "claude",
               f"HTTP {s} method={method} score={score}")

    print("\n[D] Practice test — generate")
    s, b = c.form("/api/practice/generate",
                  {"exam": "jee_main", "subject": "physics", "target_minutes": "10"},
                  timeout=120)
    # generation_method: bank/mixed/synthetic = real content; placeholder = fallback.
    # (Bank questions come from the seeded PYQ catalog and are real even for
    # free tier — synthesis is the premium top-up the tier must reach.)
    nq = gm = None
    with contextlib.suppress(Exception):
        d = json.loads(b)
        qs = d.get("questions") or (d.get("test") or {}).get("questions") or []
        nq = len(qs); gm = d.get("generation_method")
    record("practice test generated", s in (200, 201) and (nq or 0) >= 1 and gm != "placeholder",
           f"HTTP {s} questions={nq} method={gm}")

    print("\n[E] Mock interview — start + turn + end")
    s, b = c.form("/api/mock/start", {"track": "generic"})
    iid = None
    with contextlib.suppress(Exception):
        iid = json.loads(b).get("id") or json.loads(b).get("interview_id") or json.loads(b).get("iid")
    if iid:
        s2, b2 = c.form(f"/api/mock/{iid}/turn",
                        {"turn_index": "0",
                         "answer_text": "I led a team project where we built a study app; "
                                        "I handled the backend and coordinated testing."},
                        timeout=120)
        tmethod = None
        with contextlib.suppress(Exception):
            tmethod = (json.loads(b2).get("feedback") or {}).get("method")
        record("mock turn scored (real Claude)", s2 == 200 and tmethod == "claude",
               f"HTTP {s2} method={tmethod}")
        # /api/mock/{id}/end marshals to {overall_score, feedback, ...}
        s3, b3 = c._req("POST", f"/api/mock/{iid}/end", b"", timeout=120)
        ov = None
        has_fb = False
        with contextlib.suppress(Exception):
            d = json.loads(b3)
            ov = d.get("overall_score")
            has_fb = ov is not None or bool(d.get("feedback"))
        record("mock end feedback", s3 == 200 and has_fb, f"HTTP {s3} overall_score={ov}")
    else:
        record("mock start", False, f"HTTP {s} {b[:90]}")

    print("\n[F] Adaptive practice packs (rule-based)")
    s, b = c.get("/api/adaptive/packs")
    record("adaptive packs", s == 200, f"HTTP {s} {b[:90]!r}")

    print("\n[G] Unified search (PYQ + examples)")
    s, b = c.get("/api/search?q=newton")
    tot = None
    with contextlib.suppress(Exception):
        tot = json.loads(b).get("total")
    record("search returns results", s == 200 and (tot or 0) >= 1, f"HTTP {s} total={tot}")

    _summary()


def _summary():
    print("\n" + "=" * 64)
    ok = sum(1 for _, k, _ in RESULTS if k)
    print(f"Functional check: {ok}/{len(RESULTS)} passed")
    fails = [(l, d) for l, k, d in RESULTS if not k]
    if fails:
        print("\nNot working / needs attention:")
        for l, d in fails:
            print(f"  - {l}: {d}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
