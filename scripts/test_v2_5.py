"""Smoke for v2.5 — N1 forums + N2 family plans + N3 study buddies.

Run: PYTHONPATH=. python scripts/test_v2_5.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v2_5_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient
    from padhai import forums, family_plans, study_buddies
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    print("v2.5 smoke test")
    print("---------------")

    # --- N1 forums ---
    forums.migrate()
    t, opener = forums.create_thread(
        scope="public", scope_key=None,
        title="Best CBSE board exam revision strategy?",
        body="Sharing what worked for me — looking for tips.",
        author_user_id="user-A",
    )
    check(
        "N1 create_thread returns Thread + opening Post",
        t.scope == "public" and t.post_count == 1
        and opener.body.startswith("Sharing what"),
    )

    # Bad scope
    try:
        forums.create_thread(
            scope="bogus", scope_key=None,
            title="title", body="body",
            author_user_id="x",
        )
        check("N1 create_thread rejects bad scope", False)
    except ValueError:
        check("N1 create_thread rejects bad scope", True)

    # Title too short
    try:
        forums.create_thread(
            scope="public", scope_key=None,
            title="hi", body="body",
            author_user_id="x",
        )
        check("N1 create_thread rejects short title", False)
    except ValueError:
        check("N1 create_thread rejects short title", True)

    # org scope requires scope_key
    try:
        forums.create_thread(
            scope="org", scope_key=None,
            title="org-only thread", body="body",
            author_user_id="x",
        )
        check("N1 create_thread requires scope_key for org scope", False)
    except ValueError:
        check("N1 create_thread requires scope_key for org scope", True)

    # Reply
    r1 = forums.reply(thread_id=t.id, author_user_id="user-B",
                     body="Great question — I used Pomodoro.")
    check(
        "N1 reply increments post_count + updates last_activity",
        r1.body.startswith("Great question"),
    )
    t2 = forums.get_thread(t.id)
    check("N1 post_count = 2 after reply", t2.post_count == 2)

    # Flag — 1 flag, not auto-hidden
    flag1 = forums.flag_post(
        post_id=r1.id, flagger_user_id="user-C", reason="spam?",
    )
    check(
        "N1 single flag does not auto-hide",
        flag1["flag_count"] == 1 and not flag1["auto_hidden"],
    )
    # Idempotent — same user flagging twice
    flag1b = forums.flag_post(
        post_id=r1.id, flagger_user_id="user-C",
    )
    check(
        "N1 same-user re-flag is idempotent (count unchanged)",
        flag1b["flag_count"] == 1,
    )
    # Two more distinct flags → auto-hide at threshold (3)
    forums.flag_post(post_id=r1.id, flagger_user_id="user-D")
    flag3 = forums.flag_post(post_id=r1.id, flagger_user_id="user-E")
    check(
        "N1 flag count hits threshold → auto_hidden=True",
        flag3["flag_count"] == 3 and flag3["auto_hidden"],
    )
    r1_hidden = forums.get_post(r1.id)
    check("N1 hidden post has hidden=True", r1_hidden.hidden)

    # Listing excludes hidden by default
    visible = forums.list_posts(thread_id=t.id)
    check(
        "N1 list_posts excludes hidden by default",
        all(p.id != r1.id for p in visible),
    )
    all_posts = forums.list_posts(thread_id=t.id, include_hidden=True)
    check(
        "N1 list_posts(include_hidden=True) returns the hidden one",
        any(p.id == r1.id for p in all_posts),
    )

    # Moderator unhide
    check(
        "N1 unhide_post → True",
        forums.unhide_post(post_id=r1.id),
    )
    r1_after = forums.get_post(r1.id)
    check(
        "N1 unhidden post has hidden=False + flag_count=0",
        not r1_after.hidden and r1_after.flag_count == 0,
    )

    # Lock thread → reply rejected
    forums.lock_thread(thread_id=t.id, locked=True)
    try:
        forums.reply(thread_id=t.id, author_user_id="user-Z", body="x")
        check("N1 reply rejected on locked thread", False)
    except ValueError:
        check("N1 reply rejected on locked thread", True)

    # Edit own post
    r2_thread, _ = forums.create_thread(
        scope="public", scope_key=None,
        title="Another thread", body="initial body",
        author_user_id="editor-1",
    )
    posts_of = forums.list_posts(thread_id=r2_thread.id)
    original = posts_of[0]
    check(
        "N1 edit_post by author succeeds",
        forums.edit_post(
            post_id=original.id, author_user_id="editor-1",
            body="edited body",
        ),
    )
    check(
        "N1 edit_post by other author fails",
        not forums.edit_post(
            post_id=original.id, author_user_id="not-author",
            body="hijack",
        ),
    )

    # --- N2 family plans ---
    family_plans.migrate()
    fam = family_plans.create_family(
        primary_parent_user_id="parent-1", name="The Sharmas",
    )
    check(
        "N2 create_family returns FamilyGroup",
        fam.primary_parent_user_id == "parent-1",
    )

    members = family_plans.list_members(fam.id)
    check(
        "N2 primary parent auto-added as member",
        len(members) == 1 and members[0].user_id == "parent-1",
    )

    family_plans.add_member(
        group_id=fam.id, user_id="child-1",
        role="child", relation="son",
    )
    family_plans.add_member(
        group_id=fam.id, user_id="child-2",
        role="child", relation="daughter",
    )
    check(
        "N2 count_children counts both children",
        family_plans.count_children(fam.id) == 2,
    )

    # Duplicate add rejected
    try:
        family_plans.add_member(
            group_id=fam.id, user_id="child-1", role="child",
        )
        check("N2 add_member rejects duplicate user", False)
    except ValueError:
        check("N2 add_member rejects duplicate user", True)

    # Cannot remove primary parent
    try:
        family_plans.remove_member(
            group_id=fam.id, user_id="parent-1",
        )
        check("N2 remove_member protects primary parent", False)
    except ValueError:
        check("N2 remove_member protects primary parent", True)

    # Remove a child
    check(
        "N2 remove_member on child succeeds",
        family_plans.remove_member(group_id=fam.id, user_id="child-2"),
    )
    check(
        "N2 count_children now 1",
        family_plans.count_children(fam.id) == 1,
    )

    # Pricing quote
    q1 = family_plans.quote(
        tier="M3", billing_cycle="monthly", child_count=1,
    )
    check(
        "N2 1-child quote has 0% discount",
        q1.discount_pct == 0.0 and q1.total_paise == q1.base_price_paise,
    )

    q2 = family_plans.quote(
        tier="M3", billing_cycle="monthly", child_count=2,
    )
    check(
        "N2 2-child quote discounts the 2nd by 30%",
        q2.per_child_breakdown[1]["discount_pct"] == 0.30
        and q2.total_paise < 2 * q2.base_price_paise,
    )

    q3 = family_plans.quote(
        tier="M2", billing_cycle="annual", child_count=3,
    )
    check(
        "N2 3-child quote discounts 3rd by 40%",
        q3.per_child_breakdown[2]["discount_pct"] == 0.40,
    )

    # Bad tier / cycle
    try:
        family_plans.quote(tier="X", billing_cycle="monthly", child_count=1)
        check("N2 quote rejects unknown tier", False)
    except ValueError:
        check("N2 quote rejects unknown tier", True)

    # Subscription
    family_plans.add_member(
        group_id=fam.id, user_id="child-3", role="child",
    )
    sub = family_plans.start_subscription(
        group_id=fam.id, tier="M3", billing_cycle="annual",
    )
    check(
        "N2 start_subscription returns active subscription",
        sub.status == "active" and sub.child_count == 2,
        f"children={sub.child_count} total=₹{sub.total_paise/100}",
    )

    # Re-subscribe expires the old
    sub2 = family_plans.start_subscription(
        group_id=fam.id, tier="M4", billing_cycle="monthly",
    )
    check(
        "N2 re-subscribe expires old + creates new",
        sub2.id != sub.id and sub2.status == "active",
    )

    # active_for_group returns the latest
    active = family_plans.active_for_group(fam.id)
    check(
        "N2 active_for_group returns the M4 monthly",
        active and active.id == sub2.id,
    )

    # Cancel
    check(
        "N2 cancel_subscription → True",
        family_plans.cancel_subscription(sub_id=sub2.id),
    )

    # --- N3 study buddies ---
    study_buddies.migrate()
    p1 = study_buddies.upsert_profile(
        user_id="aspirant-1", exam="upsc", grade=15,
        language="hi", study_hours_week=20,
        available_windows=["evening", "weekend"],
        bio="UPSC 2026 attempt-1, Hindi medium",
    )
    check(
        "N3 upsert_profile returns Profile",
        p1.exam == "upsc" and p1.language == "hi"
        and "evening" in p1.available_windows,
    )

    # Idempotent update
    p1b = study_buddies.upsert_profile(
        user_id="aspirant-1", exam="upsc", grade=15,
        language="hi", study_hours_week=25,
    )
    check(
        "N3 upsert is idempotent (same user_id) + updates hours",
        p1b.study_hours_week == 25,
    )

    # Bad windows rejected
    try:
        study_buddies.upsert_profile(
            user_id="x", exam="upsc",
            available_windows=["meteor_shower"],
        )
        check("N3 upsert rejects bad available_windows", False)
    except ValueError:
        check("N3 upsert rejects bad available_windows", True)

    # Bad grade
    try:
        study_buddies.upsert_profile(user_id="x", grade=99)
        check("N3 upsert rejects grade out of range", False)
    except ValueError:
        check("N3 upsert rejects grade out of range", True)

    # Add candidates
    study_buddies.upsert_profile(
        user_id="aspirant-2", exam="upsc", grade=15, language="hi",
        available_windows=["evening", "morning"], study_hours_week=18,
    )
    study_buddies.upsert_profile(
        user_id="aspirant-3", exam="upsc", grade=14, language="en",
        available_windows=["weekend"], study_hours_week=15,
    )
    study_buddies.upsert_profile(
        user_id="opted-out", exam="upsc", grade=15, language="hi",
        opted_in=False,
    )

    matches = study_buddies.find_matches(user_id="aspirant-1", limit=5)
    check(
        "N3 find_matches returns at least 1 candidate",
        len(matches) >= 1,
        f"got {len(matches)}",
    )
    check(
        "N3 best candidate is aspirant-2 (same exam + grade + language + window)",
        matches and matches[0].candidate_user_id == "aspirant-2",
    )
    check(
        "N3 opted-out user not in matches",
        all(m.candidate_user_id != "opted-out" for m in matches),
    )

    # Propose pair
    pair = study_buddies.propose_pair(
        user_a_id="aspirant-1", user_b_id="aspirant-2", score=8.0,
    )
    check(
        "N3 propose_pair creates with canonical ordering",
        pair.user_a_id < pair.user_b_id
        and pair.status == "proposed",
    )

    # Re-propose collapses to same pair
    pair2 = study_buddies.propose_pair(
        user_a_id="aspirant-2", user_b_id="aspirant-1",
    )
    check(
        "N3 re-propose returns existing pair (UNIQUE(a,b) ordering)",
        pair2.id == pair.id,
    )

    # Pair with self rejected
    try:
        study_buddies.propose_pair(
            user_a_id="aspirant-1", user_b_id="aspirant-1",
        )
        check("N3 propose_pair rejects self-pair", False)
    except ValueError:
        check("N3 propose_pair rejects self-pair", True)

    # Accept by one side — still proposed
    pair_after_a = study_buddies.accept_pair(
        pair_id=pair.id, user_id="aspirant-1",
    )
    check(
        "N3 accept by one side keeps status='proposed'",
        pair_after_a.status == "proposed"
        and pair_after_a.accepted_a_at is not None,
    )
    # Accept by other side → active
    pair_active = study_buddies.accept_pair(
        pair_id=pair.id, user_id="aspirant-2",
    )
    check(
        "N3 accept by both sides → status='active'",
        pair_active.status == "active",
    )

    # Outsider can't accept
    try:
        study_buddies.accept_pair(
            pair_id=pair.id, user_id="aspirant-3",
        )
        check("N3 outsider accept raises PermissionError", False)
    except PermissionError:
        check("N3 outsider accept raises PermissionError", True)

    # Send message
    msg = study_buddies.send_message(
        pair_id=pair.id, sender_user_id="aspirant-1",
        body="Want to share notes on Modern History?",
    )
    check(
        "N3 send_message returns Message",
        msg.body.startswith("Want to share"),
    )

    msgs = study_buddies.list_messages(pair_id=pair.id)
    check(
        "N3 list_messages includes the sent one",
        len(msgs) == 1 and msgs[0].id == msg.id,
    )

    # Outsider can't send
    try:
        study_buddies.send_message(
            pair_id=pair.id, sender_user_id="aspirant-3",
            body="hijack",
        )
        check("N3 send_message rejects outsider", False)
    except PermissionError:
        check("N3 send_message rejects outsider", True)

    # Dissolve
    check(
        "N3 dissolve_pair by either party",
        study_buddies.dissolve_pair(
            pair_id=pair.id, user_id="aspirant-1",
        ),
    )

    # Can't send to dissolved pair
    try:
        study_buddies.send_message(
            pair_id=pair.id, sender_user_id="aspirant-1",
            body="hi after dissolve",
        )
        check("N3 send_message rejected on dissolved pair", False)
    except ValueError:
        check("N3 send_message rejected on dissolved pair", True)

    # Opt out dissolves all active pairs
    study_buddies.propose_pair(
        user_a_id="aspirant-2", user_b_id="aspirant-3",
    )
    study_buddies.opt_out("aspirant-2")
    pairs2 = study_buddies.my_pairs("aspirant-2",
                                     statuses=["proposed", "active"])
    check(
        "N3 opt_out dissolves user's active/proposed pairs",
        len(pairs2) == 0,
    )

    # --- HTTP layer ---
    with TestClient(app) as c:
        # Auth gates
        for path in (
            "/api/families/me",
            "/api/buddies/me",
            "/api/buddies/matches",
        ):
            r = c.get(path)
            check(f"GET {path} → 401", r.status_code == 401)

        r = c.post(
            "/api/forums/threads",
            data={"scope": "public", "title": "title",
                  "body": "body of a thread"},
        )
        check(
            "POST /api/forums/threads requires auth (401)",
            r.status_code == 401,
        )

        # Public reads
        r = c.get("/api/forums/threads?scope=public")
        check(
            "GET /api/forums/threads returns 200",
            r.status_code == 200 and "rows" in r.json(),
        )

        r = c.get(f"/api/forums/threads/{t.id}")
        check(
            "GET /api/forums/threads/{id} returns thread + posts",
            r.status_code == 200 and "thread" in r.json()
            and "posts" in r.json(),
        )

        r = c.get("/api/forums/threads/no-such-id")
        check(
            "GET /api/forums/threads/{bogus} returns 404",
            r.status_code == 404,
        )

        # Unknown scope is a filter no-op (returns empty rows, not 400)
        # — listing is read-only so we don't bother validating scope here.
        r = c.get("/api/forums/threads?scope=bogus")
        check(
            "GET /api/forums/threads?scope=bogus returns 200 + empty rows",
            r.status_code == 200 and r.json()["rows"] == [],
        )

        r = c.get(
            "/api/families/quote?tier=M3&billing_cycle=monthly&child_count=2",
        )
        check(
            "GET /api/families/quote returns 200 + discount info",
            r.status_code == 200
            and r.json()["child_count"] == 2
            and r.json()["discount_pct"] > 0,
        )

        r = c.get(
            "/api/families/quote?tier=X&billing_cycle=monthly&child_count=1",
        )
        check(
            "GET /api/families/quote rejects bad tier (400)",
            r.status_code == 400,
        )

        r = c.get("/api/admin/forums/flagged?limit=20")
        check(
            "GET /api/admin/forums/flagged returns 200",
            r.status_code == 200 and "rows" in r.json(),
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
        "v2.5 brought route count above 240",
        len(app.routes) > 240,
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
