"""Smoke for v3.3 — Retrieval / RAG over document_pages.

Covers:
- Chunking + indexing pipeline
- Token-overlap retrieval with TF-IDF scoring
- Citation-format conversion for tutor_grounding hand-off
- HTTP endpoints + auth gates

Run: PYTHONPATH=. python scripts/test_v3_3.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v3_3_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import retrieval as rt
    from padhai import schema_v2
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

    print("v3.3 smoke test — Retrieval (RAG)")
    print("---------------------------------")

    rt.migrate()
    rt.migrate()  # idempotent
    schema_v2.migrate()

    # ============================================================
    # Tokenization + chunking
    # ============================================================

    # Tokenizer drops stopwords + short words
    toks = rt._tokenize("The cat is on the mat and the dog ran away")
    check(
        "Tokenizer drops stopwords + 1-2 char words",
        "cat" in toks and "dog" in toks and "the" not in toks
        and "is" not in toks,
    )

    # Short text → single chunk
    chunks = rt._split_into_chunks("This is a short paragraph.")
    check(
        "Chunker: short text → single chunk",
        len(chunks) == 1,
    )

    # Long text → multiple chunks
    long_text = " ".join(["word"] * 600)
    chunks = rt._split_into_chunks(long_text)
    check(
        "Chunker: 600 words → 3+ chunks with overlap",
        len(chunks) >= 3,
    )

    # Each chunk respects target size
    for c in chunks[:-1]:
        check(
            f"Chunker: chunk length ~target (got {len(c.split())} words)",
            150 <= len(c.split()) <= 250,
        )

    # Tail merge: tiny tail rolls into prior
    short_tail_text = " ".join(["word"] * 220)
    chunks = rt._split_into_chunks(short_tail_text)
    check(
        "Chunker: short tail merges into prior (no tiny chunks)",
        all(len(c.split()) >= rt.CHUNK_MIN_WORDS for c in chunks),
    )

    # Section heuristic
    sec = rt._guess_section("4.2 Linear Equations\nSome content...")
    check(
        "Section heuristic catches '4.2 Linear Equations'",
        sec == "4.2 Linear Equations",
    )
    check(
        "Section heuristic returns None when no match",
        rt._guess_section("Just some prose with no heading") is None,
    )

    # ============================================================
    # Indexing
    # ============================================================

    # Index a page directly
    written = rt.index_page(
        upload_id="upload-1", page_number=1,
        extracted_text=(
            "Photosynthesis is the process by which plants make food. "
            "Chlorophyll absorbs sunlight and converts carbon dioxide "
            "and water into glucose. This happens in the chloroplasts "
            "of plant cells. The byproduct is oxygen which is released "
            "into the atmosphere."
        ),
    )
    check(
        "index_page returns chunk count ≥1",
        written >= 1,
    )

    chunks = rt.list_chunks_for_upload("upload-1")
    check(
        "list_chunks_for_upload returns the indexed chunks",
        len(chunks) == written,
    )
    check(
        "Indexed chunk has token_count > 0",
        all(c.token_count > 0 for c in chunks),
    )

    # Empty text → no chunks
    n_empty = rt.index_page(
        upload_id="upload-empty", page_number=1,
        extracted_text="",
    )
    check(
        "index_page with empty text → 0 chunks",
        n_empty == 0,
    )

    # Re-index same page is idempotent
    written2 = rt.index_page(
        upload_id="upload-1", page_number=1,
        extracted_text=(
            "Photosynthesis is the process by which plants make food. "
            "Chlorophyll absorbs sunlight and converts carbon dioxide "
            "and water into glucose. This happens in the chloroplasts "
            "of plant cells. The byproduct is oxygen which is released "
            "into the atmosphere."
        ),
    )
    chunks_after = rt.list_chunks_for_upload("upload-1")
    check(
        "index_page idempotent (chunk count unchanged on re-run)",
        len(chunks_after) == len(chunks),
    )

    # Index a second page on the same upload
    rt.index_page(
        upload_id="upload-1", page_number=2,
        extracted_text=(
            "Cellular respiration is the reverse of photosynthesis. "
            "Glucose is broken down to release ATP energy that cells "
            "use to function. Mitochondria are the powerhouses where "
            "this happens. Carbon dioxide and water are the byproducts."
        ),
    )
    check(
        "Second page indexed on same upload",
        rt.chunk_count(upload_id="upload-1") >= 2,
    )

    # Index a different upload — UPSC polity content
    rt.index_page(
        upload_id="upload-2", page_number=1,
        extracted_text=(
            "Article 21 of the Indian Constitution protects life and "
            "personal liberty. The Supreme Court has interpreted this "
            "broadly to include the right to privacy, dignity, speedy "
            "trial, and clean environment. This is a fundamental right "
            "available to all persons, not just citizens."
        ),
    )
    rt.index_page(
        upload_id="upload-2", page_number=2,
        extracted_text=(
            "Article 19 guarantees six freedoms: speech, assembly, "
            "association, movement, residence, and profession. These "
            "freedoms are subject to reasonable restrictions imposed "
            "by the state in the interests of sovereignty and public "
            "order."
        ),
    )
    check(
        "Indexed two uploads with multiple pages each",
        rt.chunk_count() >= 3,
    )

    # ============================================================
    # Retrieval
    # ============================================================

    # Query that should match upload-1 (biology)
    hits = rt.retrieve(query="photosynthesis chlorophyll", top_k=5)
    check(
        "Retrieval matches biology query",
        len(hits) >= 1
        and hits[0].chunk.upload_id == "upload-1",
    )
    check(
        "Retrieval top hit score normalised to 1.0",
        hits[0].score == 1.0,
    )

    # Query scoped to upload-2 (polity)
    hits_polity = rt.retrieve(
        query="article 21 privacy liberty",
        upload_ids=["upload-2"], top_k=5,
    )
    check(
        "Scoped retrieval returns only upload-2 chunks",
        all(h.chunk.upload_id == "upload-2" for h in hits_polity)
        and len(hits_polity) >= 1,
    )

    # Query against wrong scope → no hits
    hits_wrong = rt.retrieve(
        query="photosynthesis chlorophyll",
        upload_ids=["upload-2"], top_k=5,
    )
    check(
        "Scoped retrieval drops out-of-scope chunks",
        all(h.chunk.upload_id == "upload-2" for h in hits_wrong),
    )

    # Query with no matches → empty
    no_hits = rt.retrieve(
        query="cricket football basketball sports",
        top_k=5,
    )
    check(
        "Retrieval returns [] when no chunks match",
        no_hits == [],
    )

    # Empty query → no hits
    empty_q = rt.retrieve(query="", top_k=5)
    check(
        "Retrieval returns [] for empty query",
        empty_q == [],
    )

    # min_score filtering — set high threshold, drop low-score hits
    hits_strict = rt.retrieve(
        query="photosynthesis chlorophyll",
        top_k=10, min_score=0.99,
    )
    check(
        "Retrieval min_score filter drops low-relevance hits",
        all(h.score >= 0.99 for h in hits_strict),
    )

    # Hits convert to citation dicts cleanly
    cites = rt.hits_to_citations(hits[:2])
    check(
        "hits_to_citations produces citation dicts with required keys",
        all(
            {"source_kind", "source_id", "page_number",
             "citation_text", "relevance"}.issubset(c)
            for c in cites
        ),
    )
    check(
        "Citations have source_kind='upload'",
        all(c["source_kind"] == "upload" for c in cites),
    )

    # Matched tokens populated
    check(
        "Retrieval hits report matched_tokens",
        any(len(h.matched_tokens) > 0 for h in hits),
    )

    # ============================================================
    # End-to-end: retrieve → citations.record_answer wiring
    # ============================================================

    from padhai import citations as cit
    cit.migrate()
    polity_hits = rt.retrieve(
        query="fundamental rights Article 21",
        upload_ids=["upload-2"], top_k=3,
    )
    chunks_as_cites = rt.hits_to_citations(polity_hits)
    prov = cit.record_answer(
        surface="tutor", user_id="student-1",
        question_text="What does Article 21 protect?",
        answer_text=(
            "Article 21 protects life and personal liberty. "
            "It has been expanded to cover privacy, dignity, "
            "and speedy trial."
        ),
        answer_mode="source_only",
        citations=chunks_as_cites,
    )
    check(
        "End-to-end: retrieve → cite → record",
        prov.grounded and len(prov.citations) >= 1,
    )

    # Cleanup deletes chunks
    n_deleted = rt.delete_upload_chunks("upload-1")
    check(
        "delete_upload_chunks removes all chunks for upload",
        n_deleted >= 2 and rt.chunk_count(upload_id="upload-1") == 0,
    )

    # Delete on non-existent upload → 0
    check(
        "delete_upload_chunks returns 0 for unknown upload",
        rt.delete_upload_chunks("ghost-upload") == 0,
    )

    # ============================================================
    # HTTP layer
    # ============================================================
    with TestClient(app) as c:
        # Auth gates
        r = c.post(
            "/api/retrieval/uploads/some-upload/index",
        )
        check(
            "POST /api/retrieval/uploads/{}/index → 401",
            r.status_code == 401,
        )

        r = c.post(
            "/api/retrieval/query",
            data={"query": "photosynthesis"},
        )
        check(
            "POST /api/retrieval/query → 401",
            r.status_code == 401,
        )

        r = c.get("/api/retrieval/stats")
        check(
            "GET /api/retrieval/stats → 401",
            r.status_code == 401,
        )

        r = c.delete("/api/retrieval/uploads/x/chunks")
        check(
            "DELETE /api/retrieval/uploads/{}/chunks → 401",
            r.status_code == 401,
        )

        # Form validation: too-short query → 422
        r = c.post(
            "/api/retrieval/query",
            data={"query": "x"},
        )
        check(
            "POST /api/retrieval/query rejects 1-char query (422)",
            r.status_code == 422,
        )

        # Form validation: top_k > 50 → 422
        r = c.post(
            "/api/retrieval/query",
            data={"query": "test query",
                  "top_k": 999},
        )
        check(
            "POST /api/retrieval/query rejects top_k > 50 (422)",
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
        "v3.3 brought route count above 445",
        len(app.routes) > 445,
        f"routes={len(app.routes)}",
    )

    print("---------------------------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
