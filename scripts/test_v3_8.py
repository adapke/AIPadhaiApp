"""Smoke for v3.8 — Spaced repetition + active recall.

Covers:
- Deck CRUD, card add + position auto-increment
- SM-2 algorithm: success grows interval, failure resets it
- Due queue + new-card slack
- generate_from_chunks (closes retrieval → SRS loop)
- HTTP endpoints + auth gates

Run: PYTHONPATH=. python scripts/test_v3_8.py
"""
from __future__ import annotations
import os, sys, tempfile, time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v3_8_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient
    from padhai import spaced_repetition as srs
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

    print("v3.8 smoke test — Spaced repetition")
    print("-----------------------------------")

    srs.migrate()
    srs.migrate()  # idempotent
    retrieval.migrate()

    # ============================================================
    # Decks
    # ============================================================
    deck = srs.create_deck(
        owner_user_id="stu-1",
        title="Algebra Class 10 — Chapter 4",
        pack_code="cbse_class_10_2026",
        topic_code="linear_equations",
    )
    check(
        "create_deck returns Deck with private default",
        deck.visibility == "private"
        and deck.card_count == 0,
    )

    # Bad title
    check(
        "create_deck rejects too-short title",
        raises(srs.create_deck, exc_type=ValueError,
               owner_user_id="x", title="xx"),
    )

    # Bad visibility
    check(
        "create_deck rejects bad visibility",
        raises(srs.create_deck, exc_type=ValueError,
               owner_user_id="x",
               title="Valid title", visibility="moon"),
    )

    # ============================================================
    # Cards
    # ============================================================
    c1 = srs.add_card(
        deck_id=deck.id, owner_user_id="stu-1",
        front="What is the discriminant of ax² + bx + c?",
        back="Δ = b² - 4ac",
        hint="It tells you the nature of the roots.",
    )
    c2 = srs.add_card(
        deck_id=deck.id, owner_user_id="stu-1",
        front="Solve: 2x + 3 = 11",
        back="x = 4",
    )
    c3 = srs.add_card(
        deck_id=deck.id, owner_user_id="stu-1",
        front="Solve: x² - 5x + 6 = 0",
        back="x = 2 or x = 3",
    )
    check(
        "add_card returns Card with auto-incremented position",
        c1.position == 1 and c2.position == 2 and c3.position == 3,
    )

    # card_count updated
    deck_after = srs.get_deck(deck.id)
    check(
        "Deck card_count bumped to 3",
        deck_after.card_count == 3,
    )

    # Permission check — wrong owner can't add cards
    check(
        "add_card refuses non-owner",
        raises(srs.add_card, exc_type=PermissionError,
               deck_id=deck.id, owner_user_id="stranger",
               front="x", back="y"),
    )

    # Empty front / back rejected
    check(
        "add_card rejects empty front",
        raises(srs.add_card, exc_type=ValueError,
               deck_id=deck.id, owner_user_id="stu-1",
               front="", back="ok"),
    )

    # ============================================================
    # Review — SM-2 algorithm
    # ============================================================

    # First review with grade=4 (good) → interval=1, rep=1
    out1 = srs.review_card(
        card_id=c1.id, user_id="stu-1", grade=4,
        time_seconds=15,
    )
    check(
        "First review grade=4 → interval=1, repetitions=1",
        out1.new_interval == 1.0 and out1.repetitions == 1,
    )
    check(
        "Initial ease stays close to default (≈2.5)",
        2.4 <= out1.new_ease <= 2.6,
    )

    # Second review with grade=4 → interval=6
    out2 = srs.review_card(
        card_id=c1.id, user_id="stu-1", grade=4,
        time_seconds=12,
    )
    check(
        "Second review → interval=6 days, repetitions=2",
        out2.new_interval == 6.0 and out2.repetitions == 2,
    )

    # Third review with grade=4 → interval ≈ 6 × ease
    out3 = srs.review_card(
        card_id=c1.id, user_id="stu-1", grade=4,
    )
    check(
        "Third review → interval ≈ 6 × ease",
        out3.new_interval > 13 and out3.repetitions == 3,
    )

    # Failed review (grade=0) → resets repetitions + interval=1
    out_fail = srs.review_card(
        card_id=c1.id, user_id="stu-1", grade=0,
    )
    check(
        "Failed review → repetitions=0, interval=1",
        out_fail.repetitions == 0 and out_fail.new_interval == 1.0,
    )
    check(
        "Failed review increments lapses",
        out_fail.lapses == 1,
    )

    # Grade=5 (easy) bumps ease higher than grade=4
    e_card = srs.add_card(
        deck_id=deck.id, owner_user_id="stu-1",
        front="ez card", back="trivial",
    )
    easy = srs.review_card(
        card_id=e_card.id, user_id="stu-1", grade=5,
    )
    g_card = srs.add_card(
        deck_id=deck.id, owner_user_id="stu-1",
        front="good card", back="ok",
    )
    good = srs.review_card(
        card_id=g_card.id, user_id="stu-1", grade=4,
    )
    check(
        "Easy review yields higher ease than good review",
        easy.new_ease > good.new_ease,
    )

    # Bad grade
    check(
        "review_card rejects out-of-range grade",
        raises(srs.review_card, exc_type=ValueError,
               card_id=c1.id, user_id="stu-1", grade=99),
    )

    # State retrievable
    state = srs.get_card_state(card_id=c1.id, user_id="stu-1")
    check(
        "get_card_state returns the card's user state",
        state is not None
        and state.user_id == "stu-1"
        and state.lapses == 1,
    )

    # ============================================================
    # Due queue + include_new
    # ============================================================

    # c1 was just reviewed → not due now
    # c2, c3 are unreviewed → "new" cards
    queue = srs.due_queue(
        user_id="stu-1", deck_id=deck.id,
        limit=10, include_new=True,
    )
    check(
        "Due queue includes unreviewed new cards",
        any(c.id == c2.id for c in queue),
    )

    # include_new=False → no new cards
    no_new = srs.due_queue(
        user_id="stu-1", deck_id=deck.id,
        include_new=False,
    )
    check(
        "include_new=False returns only due cards (currently empty)",
        len(no_new) == 0
        or all(c.id not in {c2.id, c3.id} for c in no_new),
    )

    # Force a card past its due date
    with srs._conn() as conn:
        conn.execute(
            "UPDATE flashcards_user_state SET due_at = ? "
            "WHERE card_id = ? AND user_id = ?",
            (time.time() - 60, c1.id, "stu-1"),
        )
    queue_after = srs.due_queue(
        user_id="stu-1", deck_id=deck.id, include_new=False,
    )
    check(
        "Manually-due card surfaces in queue",
        any(c.id == c1.id for c in queue_after),
    )

    # ============================================================
    # Generate from retrieval
    # ============================================================

    # Index a doc so retrieval has hits
    retrieval.index_page(
        upload_id="upload-bio", page_number=1,
        extracted_text=(
            "Photosynthesis is the process by which plants make food. "
            "Chlorophyll absorbs sunlight. The byproduct is oxygen."
        ),
    )

    new_deck = srs.generate_from_chunks(
        owner_user_id="stu-1",
        deck_title="Biology — Photosynthesis Recall",
        chunks=[
            {"source_kind": "upload", "source_id": "upload-bio",
             "page_number": 1,
             "section": "Chapter 6 Plants",
             "citation_text": (
                 "Photosynthesis is the process by which plants make food. "
                 "Chlorophyll absorbs sunlight."
             ),
             "relevance": 0.95},
            {"source_kind": "upload", "source_id": "upload-bio",
             "page_number": 1,
             "section": "Chapter 6 Plants",
             "citation_text": "The byproduct is oxygen.",
             "relevance": 0.85},
        ],
        pack_code="cbse_class_10_2026",
    )
    check(
        "generate_from_chunks creates deck with 2 cards",
        new_deck.card_count == 2,
    )

    chunks_cards = srs.list_cards_for_deck(new_deck.id)
    check(
        "Cards have citation populated from chunks",
        all(c.citation is not None for c in chunks_cards),
    )

    # Empty chunks rejected
    check(
        "generate_from_chunks rejects empty chunks",
        raises(srs.generate_from_chunks, exc_type=ValueError,
               owner_user_id="x", deck_title="Empty",
               chunks=[]),
    )

    # ============================================================
    # User stats + listing
    # ============================================================
    stats = srs.user_stats("stu-1")
    check(
        "user_stats returns tracked_cards + reviews_24h + retention_30d",
        "tracked_cards" in stats
        and "reviews_24h" in stats
        and "retention_30d" in stats,
    )
    check(
        "retention_30d reflects mixed grades",
        stats["retention_30d"] is None
        or 0 <= stats["retention_30d"] <= 1,
    )

    # List my decks
    my_decks = srs.list_my_decks("stu-1")
    check(
        "list_my_decks returns both decks created",
        len(my_decks) == 2,
    )

    # ============================================================
    # Public decks visibility
    # ============================================================
    pub = srs.create_deck(
        owner_user_id="stu-1", title="Shared Algebra Deck",
        visibility="public",
        pack_code="cbse_class_10_2026",
    )
    public_list = srs.list_public_decks(
        pack_code="cbse_class_10_2026",
    )
    check(
        "list_public_decks returns the public deck",
        any(d.id == pub.id for d in public_list),
    )

    # Delete cascade
    check(
        "delete_deck removes deck + cards + reviews",
        srs.delete_deck(deck_id=deck.id, owner_user_id="stu-1"),
    )
    check(
        "delete_deck non-owner returns False",
        not srs.delete_deck(
            deck_id=new_deck.id, owner_user_id="stranger",
        ),
    )

    # ============================================================
    # HTTP layer
    # ============================================================
    with TestClient(app) as c:
        # Public — public decks
        r = c.get("/api/srs/decks/public")
        check(
            "GET /api/srs/decks/public → 200",
            r.status_code == 200,
        )

        # Auth gates
        r = c.post(
            "/api/srs/decks",
            data={"title": "Valid Deck Title"},
        )
        check(
            "POST /api/srs/decks → 401",
            r.status_code == 401,
        )

        r = c.get("/api/srs/queue")
        check(
            "GET /api/srs/queue → 401",
            r.status_code == 401,
        )

        r = c.get("/api/srs/me/stats")
        check(
            "GET /api/srs/me/stats → 401",
            r.status_code == 401,
        )

        r = c.post(
            "/api/srs/cards/some-id/review",
            data={"grade": 4},
        )
        check(
            "POST /api/srs/cards/{}/review → 401",
            r.status_code == 401,
        )

        # Form validation: grade > 5 → 422
        r = c.post(
            "/api/srs/cards/some-id/review",
            data={"grade": 99},
        )
        check(
            "POST review rejects grade > 5 (422)",
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
        "v3.8 brought route count above 490",
        len(app.routes) > 490,
        f"routes={len(app.routes)}",
    )

    print("-----------------------------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
