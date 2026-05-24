"""Smoke for v3.10 — Research / PhD tools.

Covers:
- Paper ingest + DOI/arXiv format validation
- Summary save (idempotent by paper_id) + retrieval
- Collection lifecycle: create, add/remove papers, list
- Literature map: nodes + edges weighted by shared keywords/authors
- Gap detection: low-coverage themes flagged
- Citation manager: flag, list (filter by tag/paper), delete
- HTTP endpoints + auth gates

Run: PYTHONPATH=. python scripts/test_v3_10.py
"""
from __future__ import annotations
import os, sys, tempfile, time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v3_10_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient
    from padhai import research_tools as rt
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

    print("v3.10 smoke test — Research / PhD tools")
    print("---------------------------------------")

    rt.migrate()
    rt.migrate()

    # ============================================================
    # Paper ingest
    # ============================================================
    p1 = rt.ingest_paper(
        user_id="researcher-1",
        title="Reinforcement Learning for Robotic Manipulation",
        authors=["Smith J", "Patel R"],
        year=2024, venue="NeurIPS",
        doi="10.1234/neurips.2024.42",
        arxiv_id="2401.12345",
        abstract=(
            "We propose a sim-to-real reinforcement learning "
            "approach using domain randomization to train robotic "
            "manipulation policies. Experiments show 80% success "
            "rate on real-world tasks."
        ),
        keywords=["reinforcement learning", "robotics",
                  "sim-to-real", "manipulation"],
        citation_count=12,
    )
    check(
        "ingest_paper returns Paper with auto id + extracted fields",
        p1.doi == "10.1234/neurips.2024.42"
        and p1.arxiv_id == "2401.12345"
        and len(p1.authors) == 2,
    )

    # Bad DOI
    check(
        "ingest_paper rejects malformed DOI",
        raises(rt.ingest_paper, exc_type=ValueError,
               user_id="x", title="Valid Title",
               doi="not-a-doi"),
    )

    # Bad arXiv id
    check(
        "ingest_paper rejects malformed arXiv id",
        raises(rt.ingest_paper, exc_type=ValueError,
               user_id="x", title="Valid Title",
               arxiv_id="bogus_id"),
    )

    # Bad year
    check(
        "ingest_paper rejects year > 2100",
        raises(rt.ingest_paper, exc_type=ValueError,
               user_id="x", title="Future Paper",
               year=9999),
    )

    # Duplicate DOI for same user
    check(
        "ingest_paper rejects duplicate DOI for same user",
        raises(rt.ingest_paper, exc_type=ValueError,
               user_id="researcher-1", title="Dup",
               doi="10.1234/neurips.2024.42"),
    )

    # Different user can ingest same DOI
    p1_other = rt.ingest_paper(
        user_id="researcher-2", title="Dup but Other User",
        doi="10.1234/neurips.2024.42",
    )
    check(
        "Different user can ingest same DOI",
        p1_other.id != p1.id,
    )

    # Ingest more papers for collection / gap detection tests
    p2 = rt.ingest_paper(
        user_id="researcher-1",
        title="Domain Randomization for Policy Transfer",
        authors=["Patel R", "Wei L"],
        year=2023,
        abstract=(
            "Domain randomization technique for transferring "
            "RL policies from simulation to real robots."
        ),
        keywords=["reinforcement learning", "sim-to-real",
                  "domain randomization"],
    )
    p3 = rt.ingest_paper(
        user_id="researcher-1",
        title="Imitation Learning Survey",
        authors=["Brown K"],
        year=2022,
        abstract=(
            "Survey of imitation learning methods including "
            "behavior cloning and inverse RL."
        ),
        keywords=["imitation learning", "behavior cloning",
                  "inverse reinforcement learning"],
    )
    p4 = rt.ingest_paper(
        user_id="researcher-1",
        title="Continuous Control with Deep Networks",
        authors=["Smith J"],
        year=2024,
        abstract=(
            "Deep neural network approach to continuous control "
            "in robotic environments."
        ),
        keywords=["deep learning", "continuous control",
                  "robotics"],
    )

    # ============================================================
    # Summaries
    # ============================================================
    s = rt.save_summary(
        paper_id=p1.id, user_id="researcher-1",
        short_summary=(
            "The paper proposes a sim-to-real RL pipeline using "
            "domain randomization, achieving 80% real-world success."
        ),
        key_findings=[
            "Sim-to-real transfer via domain randomization works",
            "80% success on real tasks",
            "10x sample efficiency over prior baselines",
        ],
        methods="PPO + DR + reward shaping",
        limitations="Tested only on 3 manipulation tasks",
    )
    check(
        "save_summary persists short + key_findings",
        len(s.key_findings) == 3 and "domain" in s.short_summary,
    )

    # save_summary is REPLACE-on-conflict by paper_id
    s2 = rt.save_summary(
        paper_id=p1.id, user_id="researcher-1",
        short_summary=(
            "Updated summary that overwrites the prior cached "
            "summary."
        ),
    )
    check(
        "save_summary REPLACE updates existing row",
        s2.short_summary.startswith("Updated"),
    )

    # Wrong user can't save summary for someone else's paper
    check(
        "save_summary rejects non-owner",
        raises(rt.save_summary, exc_type=PermissionError,
               paper_id=p1.id, user_id="stranger",
               short_summary="x" * 30),
    )

    # Short summary rejected
    check(
        "save_summary rejects too-short summary",
        raises(rt.save_summary, exc_type=ValueError,
               paper_id=p1.id, user_id="researcher-1",
               short_summary="short"),
    )

    # Retrieve
    got = rt.get_summary(p1.id)
    check(
        "get_summary returns the cached summary",
        got is not None and got.paper_id == p1.id,
    )

    # ============================================================
    # Collections
    # ============================================================
    c = rt.create_collection(
        user_id="researcher-1",
        title="RL for Robotics — Thesis Chapter 3",
        description="Lit review on sim-to-real RL.",
    )
    check(
        "create_collection returns Collection",
        c.title.startswith("RL") and c.paper_count == 0,
    )

    # Bad title
    check(
        "create_collection rejects too-short title",
        raises(rt.create_collection, exc_type=ValueError,
               user_id="x", title="xx"),
    )

    # Add papers
    pos1 = rt.add_to_collection(
        collection_id=c.id, paper_id=p1.id,
        user_id="researcher-1", notes="Seminal RL paper",
    )
    pos2 = rt.add_to_collection(
        collection_id=c.id, paper_id=p2.id,
        user_id="researcher-1",
    )
    pos3 = rt.add_to_collection(
        collection_id=c.id, paper_id=p3.id,
        user_id="researcher-1",
    )
    pos4 = rt.add_to_collection(
        collection_id=c.id, paper_id=p4.id,
        user_id="researcher-1",
    )
    check(
        "add_to_collection auto-increments positions 1..4",
        pos1 == 1 and pos2 == 2 and pos3 == 3 and pos4 == 4,
    )

    # Re-add is idempotent — returns existing position
    pos_dup = rt.add_to_collection(
        collection_id=c.id, paper_id=p1.id,
        user_id="researcher-1",
    )
    check(
        "add_to_collection idempotent (same position)",
        pos_dup == 1,
    )

    # paper_count updated
    c_after = rt.get_collection(c.id)
    check(
        "Collection paper_count = 4 after adds",
        c_after.paper_count == 4,
    )

    # Permission check
    check(
        "add_to_collection refuses non-owner",
        raises(rt.add_to_collection, exc_type=PermissionError,
               collection_id=c.id, paper_id=p1.id,
               user_id="stranger"),
    )

    # Bad paper id
    check(
        "add_to_collection rejects unknown paper",
        raises(rt.add_to_collection, exc_type=ValueError,
               collection_id=c.id, paper_id="ghost",
               user_id="researcher-1"),
    )

    # List collection papers
    rows = rt.list_collection_papers(c.id)
    check(
        "list_collection_papers returns 4 ordered rows",
        len(rows) == 4
        and rows[0]["position"] == 1,
    )
    check(
        "Collection paper row has joined paper fields",
        "title" in rows[0] and "authors" in rows[0],
    )

    # Remove
    check(
        "remove_from_collection True for owner",
        rt.remove_from_collection(
            collection_id=c.id, paper_id=p4.id,
            user_id="researcher-1",
        ),
    )
    c_after2 = rt.get_collection(c.id)
    check(
        "paper_count decrements after removal",
        c_after2.paper_count == 3,
    )
    # Non-owner remove
    check(
        "remove_from_collection False for non-owner",
        not rt.remove_from_collection(
            collection_id=c.id, paper_id=p3.id,
            user_id="stranger",
        ),
    )

    # ============================================================
    # Literature map
    # ============================================================
    lit_map = rt.literature_map(c.id)
    check(
        "literature_map returns nodes + edges",
        "nodes" in lit_map and "edges" in lit_map
        and len(lit_map["nodes"]) == 3,
    )
    # p1 + p2 share 'reinforcement learning' + 'sim-to-real' → edge
    has_p1_p2_edge = any(
        (e["source"], e["target"]) in
        [(p1.id, p2.id), (p2.id, p1.id)]
        for e in lit_map["edges"]
    )
    check(
        "Literature map detects shared-keyword edge p1↔p2",
        has_p1_p2_edge,
    )
    # p1 + p2 also share author 'Patel R' → bumps weight
    p1_p2_edge = next(
        (e for e in lit_map["edges"]
         if (e["source"], e["target"]) in
         [(p1.id, p2.id), (p2.id, p1.id)]),
        None,
    )
    check(
        "Shared-author edge present in p1↔p2 weighting",
        p1_p2_edge
        and "patel r" in (p1_p2_edge["shared_authors"]),
    )

    # Empty collection map
    empty_c = rt.create_collection(
        user_id="researcher-1", title="Empty Collection",
    )
    empty_map = rt.literature_map(empty_c.id)
    check(
        "Literature map empty collection returns [], []",
        empty_map["nodes"] == [] and empty_map["edges"] == [],
    )

    # ============================================================
    # Gap detection
    # ============================================================
    gaps = rt.detect_gaps(
        collection_id=c.id, user_id="researcher-1",
        proposed_themes=[
            "safety constraints",   # absent in all papers
            "reinforcement learning",  # well-covered (excluded)
            "human-robot interaction",  # absent
        ],
    )
    themes_found = {g.theme for g in gaps}
    check(
        "detect_gaps surfaces uncovered proposed themes",
        "safety constraints" in themes_found
        and "human-robot interaction" in themes_found,
    )
    check(
        "detect_gaps excludes well-covered themes",
        "reinforcement learning" not in themes_found,
    )

    # Each gap has rationale + coverage_score
    for g in gaps:
        check(
            f"Gap {g.theme!r} has coverage_score in [0,1]",
            0 <= g.coverage_score <= 1,
        )

    # Gaps persisted
    listed = rt.list_gaps(c.id)
    check(
        "list_gaps returns the persisted gaps",
        len(listed) >= len(gaps),
    )

    # Re-detect clears prior gaps
    gaps2 = rt.detect_gaps(
        collection_id=c.id, user_id="researcher-1",
        proposed_themes=["yet another theme"],
    )
    listed2 = rt.list_gaps(c.id)
    check(
        "Re-detect_gaps clears prior + replaces",
        len(listed2) == len(gaps2),
    )

    # Non-owner can't detect gaps
    check(
        "detect_gaps refuses non-owner",
        raises(rt.detect_gaps, exc_type=PermissionError,
               collection_id=c.id, user_id="stranger"),
    )

    # Too few papers
    tiny_c = rt.create_collection(
        user_id="researcher-1", title="Just one paper",
    )
    rt.add_to_collection(
        collection_id=tiny_c.id, paper_id=p1.id,
        user_id="researcher-1",
    )
    no_gaps = rt.detect_gaps(
        collection_id=tiny_c.id, user_id="researcher-1",
        proposed_themes=["x"],
    )
    check(
        "detect_gaps returns [] when too few papers",
        no_gaps == [],
    )

    # ============================================================
    # Citation manager
    # ============================================================
    cit = rt.flag_citation(
        user_id="researcher-1", paper_id=p1.id,
        citation_text=(
            "We propose a sim-to-real reinforcement learning "
            "approach using domain randomization."
        ),
        page_number=2,
        section="2. Method",
        note="Key thesis statement — quote in chapter 3 intro",
        tags=["thesis-chapter-3", "intro"],
    )
    check(
        "flag_citation returns Citation",
        cit.paper_id == p1.id
        and "domain-randomization" not in cit.citation_text,
    )

    # Too-short citation
    check(
        "flag_citation rejects citation_text < 10 chars",
        raises(rt.flag_citation, exc_type=ValueError,
               user_id="x", paper_id=p1.id,
               citation_text="short"),
    )

    # Unknown paper
    check(
        "flag_citation rejects unknown paper",
        raises(rt.flag_citation, exc_type=ValueError,
               user_id="x", paper_id="ghost-paper",
               citation_text="x" * 30),
    )

    # Flag another for filter tests
    rt.flag_citation(
        user_id="researcher-1", paper_id=p2.id,
        citation_text=(
            "Domain randomization technique for transferring "
            "RL policies."
        ),
        tags=["thesis-chapter-3", "methods"],
    )
    rt.flag_citation(
        user_id="researcher-1", paper_id=p3.id,
        citation_text="Survey of imitation learning methods.",
        tags=["thesis-chapter-2"],
    )

    # List all my citations
    all_cites = rt.list_citations(user_id="researcher-1")
    check(
        "list_citations returns all 3 flagged",
        len(all_cites) == 3,
    )

    # Filter by paper
    by_p = rt.list_citations(
        user_id="researcher-1", paper_id=p1.id,
    )
    check(
        "list_citations filters by paper_id",
        all(c.paper_id == p1.id for c in by_p),
    )

    # Filter by tag
    by_tag = rt.list_citations(
        user_id="researcher-1", tag="thesis-chapter-3",
    )
    check(
        "list_citations filters by tag",
        len(by_tag) == 2,
    )

    # Delete
    check(
        "delete_citation True for owner",
        rt.delete_citation(
            citation_id=cit.id, user_id="researcher-1",
        ),
    )
    check(
        "delete_citation False for non-owner",
        not rt.delete_citation(
            citation_id="some-id", user_id="stranger",
        ),
    )

    # ============================================================
    # HTTP layer
    # ============================================================
    with TestClient(app) as c:
        # Auth gates
        r = c.post(
            "/api/research/papers",
            data={"title": "Auth Test Paper"},
        )
        check(
            "POST /api/research/papers → 401",
            r.status_code == 401,
        )

        r = c.get("/api/research/papers/me")
        check(
            "GET /api/research/papers/me → 401",
            r.status_code == 401,
        )

        r = c.post(
            "/api/research/collections",
            data={"title": "Test Collection"},
        )
        check(
            "POST /api/research/collections → 401",
            r.status_code == 401,
        )

        r = c.post(
            "/api/research/citations",
            data={"paper_id": "x",
                  "citation_text": "x" * 30},
        )
        check(
            "POST /api/research/citations → 401",
            r.status_code == 401,
        )

        # Form validation: short title → 422
        r = c.post(
            "/api/research/papers",
            data={"title": "xx"},
        )
        check(
            "POST papers rejects title < 3 (422)",
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
        "v3.10 brought route count above 515",
        len(app.routes) > 515,
        f"routes={len(app.routes)}",
    )

    print("---------------------------------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
