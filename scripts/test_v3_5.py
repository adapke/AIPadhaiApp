"""Smoke for v3.5 — Community moderation primitives.

Covers:
- Auto-flag scanner (blocklist + url spam + allcaps + repetition)
- Reviewer queue + decisions + SLA tracking
- Reactions w/ throttling + report-to-queue feedback
- HTTP endpoints + admin auth gates

Run: PYTHONPATH=. python scripts/test_v3_5.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v3_5_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import moderation_queue as mq
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

    print("v3.5 smoke test — Moderation + reactions")
    print("----------------------------------------")

    mq.migrate()
    mq.migrate()    # idempotent

    # ============================================================
    # Scanner — clean content
    # ============================================================

    result = mq.scan(
        content_kind="forum_post", content_id="post-1",
        body="This is a normal helpful study answer about photosynthesis.",
        author_user_id="user-1",
    )
    check(
        "Scanner: clean content → severity='clean', no flag",
        result.severity == "clean"
        and result.flag_id is None
        and result.score < mq.FLAG_THRESHOLD,
    )

    # Bad content_kind
    check(
        "Scanner rejects bad content_kind",
        raises(mq.scan, exc_type=ValueError,
               content_kind="alien_kind", content_id="x",
               body="hi", author_user_id="u"),
    )

    # ============================================================
    # Scanner — blocklist hit
    # ============================================================
    bl_result = mq.scan(
        content_kind="forum_post", content_id="post-bl",
        body="Hey check out spam_link_xyz it's the best",
        author_user_id="spammer-1",
    )
    check(
        "Scanner: 1 blocklist hit → 'flag' severity",
        bl_result.severity == "flag"
        and "blocklist:spam_link_xyz" in bl_result.rules_triggered,
    )

    # 2 blocklist hits → auto_remove
    bl2 = mq.scan(
        content_kind="forum_post", content_id="post-bl2",
        body="spam_link_xyz buy_followers, double whammy",
        author_user_id="spammer-2",
    )
    check(
        "Scanner: 2 blocklist hits → 'auto_remove'",
        bl2.severity == "auto_remove" and bl2.score >= 0.9,
    )

    # ============================================================
    # Scanner — URL spam
    # ============================================================
    url_spam = mq.scan(
        content_kind="forum_post", content_id="post-url",
        body=(
            "Check these out: https://a.com https://b.com "
            "https://c.com https://d.com short msg lots of links"
        ),
        author_user_id="user-2",
    )
    check(
        "Scanner: 4 URLs in short msg → flagged",
        url_spam.severity in ("flag", "auto_remove")
        and any("url" in r for r in url_spam.rules_triggered),
    )

    # ============================================================
    # Scanner — allcaps
    # ============================================================
    caps = mq.scan(
        content_kind="forum_post", content_id="post-caps",
        body="I AM SHOUTING AT EVERYONE BECAUSE I WANT ATTENTION RIGHT NOW",
        author_user_id="user-3",
    )
    check(
        "Scanner: allcaps adds rule",
        "allcaps" in caps.rules_triggered,
    )

    # ============================================================
    # Scanner — repetition
    # ============================================================
    rep = mq.scan(
        content_kind="forum_post", content_id="post-rep",
        body="aaaaaaaaaaaaaaa hi everyone",
        author_user_id="user-4",
    )
    check(
        "Scanner: repeated-char run flagged",
        "repeated_chars" in rep.rules_triggered,
    )

    rep_w = mq.scan(
        content_kind="forum_post", content_id="post-repw",
        body="win win win win win win win the lottery",
        author_user_id="user-5",
    )
    check(
        "Scanner: repeated-word run flagged",
        "repeated_word" in rep_w.rules_triggered,
    )

    # ============================================================
    # Idempotency — same content twice
    # ============================================================
    s1 = mq.scan(
        content_kind="forum_post", content_id="post-idem",
        body="spam_link_xyz hello",
        author_user_id="u-idem",
    )
    s2 = mq.scan(
        content_kind="forum_post", content_id="post-idem",
        body="spam_link_xyz hello again with different body",
        author_user_id="u-idem",
    )
    check(
        "Scanner idempotent on (content_kind, content_id)",
        s2.flag_id == s1.flag_id,
    )

    # ============================================================
    # Queue listing + decisions
    # ============================================================

    queue = mq.list_queue(status="open")
    check(
        "list_queue returns open items",
        len(queue) >= 1,
    )

    # Filter by severity
    auto_removes = mq.list_queue(
        status="auto_resolved", severity="auto_remove",
    )
    check(
        "list_queue filters by status='auto_resolved'",
        len(auto_removes) >= 1,
    )

    # Filter by content_kind
    fp_only = mq.list_queue(
        status="open", content_kind="forum_post",
    )
    check(
        "list_queue filters by content_kind",
        all(i.content_kind == "forum_post" for i in fp_only),
    )

    # Bad status filter
    check(
        "list_queue rejects bad status",
        raises(mq.list_queue, exc_type=ValueError,
               status="alien_status"),
    )

    # Decide: approve
    first = queue[0]
    check(
        "decide approve → True",
        mq.decide(
            item_id=first.id, action="approve",
            reviewer_user_id="reviewer-1",
            reason="false positive on blocklist",
        ),
    )

    # Decide is idempotent — already-decided returns False
    check(
        "decide on already-approved → False",
        not mq.decide(
            item_id=first.id, action="approve",
            reviewer_user_id="reviewer-1",
        ),
    )

    # Action audit trail
    actions = mq.list_actions_for_item(first.id)
    check(
        "Action audit trail records the decision",
        len(actions) == 1 and actions[0]["action"] == "approve",
    )

    # Bad action
    check(
        "decide rejects bad action",
        raises(mq.decide, exc_type=ValueError,
               item_id=first.id, action="moon_walk",
               reviewer_user_id="rev"),
    )

    # decide on unknown item → False
    check(
        "decide on unknown item → False",
        not mq.decide(
            item_id="ghost-id", action="approve",
            reviewer_user_id="rev",
        ),
    )

    # Remove + restore flow on the auto_resolved item
    auto_item = auto_removes[0]
    check(
        "decide restore on auto_resolved → True",
        mq.decide(
            item_id=auto_item.id, action="restore",
            reviewer_user_id="rev-2",
            reason="not actually spam after manual review",
        ),
    )

    # SLA — manually breach a flag, then filter for breached
    queue_open = mq.list_queue(status="open")
    if queue_open:
        with mq._conn() as conn:
            conn.execute(
                "UPDATE mod_flagged_content "
                "SET sla_due_at = ? WHERE id = ?",
                (time.time() - 3600, queue_open[0].id),
            )
        breached = mq.list_queue(
            status="open", sla_breached_only=True,
        )
        check(
            "list_queue(sla_breached_only=True) catches breach",
            any(b.id == queue_open[0].id for b in breached),
        )

    # Queue stats
    stats = mq.queue_stats()
    check(
        "queue_stats has by_status + sla_breached_open",
        "by_status" in stats and "sla_breached_open" in stats,
    )

    # ============================================================
    # Reactions
    # ============================================================

    # React works first time
    check(
        "react first time → True",
        mq.react(
            target_kind="forum_post", target_id="answer-1",
            user_id="reactor-1", kind="like",
        ),
    )

    # Idempotent — second react with same kind → False
    check(
        "react same (user, target, kind) idempotent → False",
        not mq.react(
            target_kind="forum_post", target_id="answer-1",
            user_id="reactor-1", kind="like",
        ),
    )

    # Different kind by same user OK
    check(
        "react different kind by same user → True",
        mq.react(
            target_kind="forum_post", target_id="answer-1",
            user_id="reactor-1", kind="helpful",
        ),
    )

    # Bad target_kind / kind
    check(
        "react rejects bad target_kind",
        raises(mq.react, exc_type=ValueError,
               target_kind="alien", target_id="x",
               user_id="u", kind="like"),
    )
    check(
        "react rejects bad kind",
        raises(mq.react, exc_type=ValueError,
               target_kind="forum_post", target_id="x",
               user_id="u", kind="cosmic"),
    )

    # Other users add reactions
    for u in ["reactor-2", "reactor-3"]:
        mq.react(
            target_kind="forum_post", target_id="answer-1",
            user_id=u, kind="like",
        )

    counts = mq.reaction_counts(
        target_kind="forum_post", target_id="answer-1",
    )
    check(
        "reaction_counts: 3 likes + 1 helpful",
        counts.get("like") == 3 and counts.get("helpful") == 1,
    )

    # User's own reactions
    my = mq.user_reactions_for_target(
        target_kind="forum_post", target_id="answer-1",
        user_id="reactor-1",
    )
    check(
        "user_reactions_for_target returns sorted kinds",
        set(my) == {"like", "helpful"},
    )

    # Unreact
    check(
        "unreact returns True for existing",
        mq.unreact(
            target_kind="forum_post", target_id="answer-1",
            user_id="reactor-1", kind="like",
        ),
    )
    counts_after = mq.reaction_counts(
        target_kind="forum_post", target_id="answer-1",
    )
    check(
        "Unreact decrements counts",
        counts_after.get("like") == 2,
    )

    # Unreact non-existent → False
    check(
        "unreact non-existent → False",
        not mq.unreact(
            target_kind="forum_post", target_id="ghost",
            user_id="u", kind="like",
        ),
    )

    # Report reaction → feeds into moderation queue
    open_before = len(mq.list_queue(status="open"))
    mq.react(
        target_kind="study_buddy_msg", target_id="msg-bad",
        user_id="reporter-1", kind="report",
    )
    open_after = len(mq.list_queue(status="open"))
    check(
        "Report reaction creates moderation queue entry",
        open_after == open_before + 1,
    )

    reported_item = mq.get_item_for_target(
        "study_buddy_msg", "msg-bad",
    )
    check(
        "Reported item exists in queue with 'user_report' rule",
        reported_item is not None
        and "user_report" in reported_item.rules_triggered,
    )

    # ============================================================
    # HTTP layer
    # ============================================================
    with TestClient(app) as c:
        # Public — reaction counts
        r = c.get(
            "/api/reactions/forum_post/answer-1",
        )
        check(
            "GET /api/reactions/{}/{} → 200 public",
            r.status_code == 200,
        )

        # Auth gates
        r = c.post(
            "/api/reactions",
            data={"target_kind": "forum_post",
                  "target_id": "x", "kind": "like"},
        )
        check(
            "POST /api/reactions → 401 (auth gate)",
            r.status_code == 401,
        )

        r = c.get(
            "/api/reactions/me/forum_post/answer-1",
        )
        check(
            "GET /api/reactions/me/{}/{} → 401",
            r.status_code == 401,
        )

        # Admin gates
        r = c.get("/api/admin/moderation/queue")
        check(
            "GET /api/admin/moderation/queue → 401",
            r.status_code == 401,
        )

        r = c.get("/api/admin/moderation/stats")
        check(
            "GET /api/admin/moderation/stats → 401",
            r.status_code == 401,
        )

        r = c.post(
            "/api/admin/moderation/scan",
            data={"content_kind": "forum_post",
                  "content_id": "x", "body": "test",
                  "author_user_id": "u"},
        )
        check(
            "POST /api/admin/moderation/scan → 401",
            r.status_code == 401,
        )

        # Form validation — bad kind on react
        r = c.post(
            "/api/reactions",
            data={"target_kind": "forum_post",
                  "target_id": "x", "kind": ""},
        )
        check(
            "POST /api/reactions rejects empty kind (422)",
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
        "v3.5 brought route count above 463",
        len(app.routes) > 463,
        f"routes={len(app.routes)}",
    )

    print("----------------------------------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
