"""v3.10 — Research / PhD tools.

Per review §9 (College + PhD gap) — college students + PhD
researchers need different tooling than school students. The
existing platform is built around exam prep; research workflows
need paper-reading + literature-review + citation-manager + thesis
outline support.

Six tables:
  research_papers      ingested papers (PDF upload + extracted metadata)
  paper_summaries      LLM-generated summary per paper (cached)
  literature_collections  user-curated reading lists
  collection_papers       (collection, paper) link rows
  research_citations      citations the user has flagged for use
  research_gaps           detected gaps in a body of literature

Each paper is identified by:
  • our internal id
  • DOI when known
  • arXiv id when known
  • upload_id (link to ingested PDF in document_pages)

Summaries are generated on demand and cached. We don't store the
full LLM call here — that's via `llm_obs.record_call`. We store
the structured summary: abstract, key_findings (list), methods,
limitations, citations_used (list of DOIs/refs).

Literature collections are like Zotero folders — students/PhDs
group papers for a thesis chapter or lit review. Each collection
can emit a literature map (graph of papers + their citation
relationships) and detect gaps (themes mentioned but not
elaborated on across the collection).

`research_citations` is the citation manager — flag any sentence/
chunk from any paper for export to BibTeX / APA / Chicago later.
A separate `padhai/cite_export.py` (not in this release) handles
the formatting.

Functions:
  ingest_paper(upload_id, doi?, arxiv_id?, title, ...)
  summarize_paper(paper_id, user_id)
  create_collection(user_id, title)
  add_to_collection(collection_id, paper_id, user_id)
  flag_citation(paper_id, chunk_id, note, user_id)
  literature_map(collection_id)
  detect_gaps(collection_id)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS research_papers (
    id              TEXT PRIMARY KEY,
    upload_id       TEXT,                            -- FK uploads
    user_id         TEXT NOT NULL,                   -- owner / ingester
    title           TEXT NOT NULL,
    authors         TEXT,                             -- JSON array
    year            INTEGER,
    venue           TEXT,                             -- journal / conference
    doi             TEXT,
    arxiv_id        TEXT,
    abstract        TEXT,
    keywords        TEXT,                             -- JSON array
    citation_count  INTEGER,                          -- if known (e.g. from Semantic Scholar API)
    created_at      REAL NOT NULL,
    UNIQUE (user_id, doi)
);
CREATE INDEX IF NOT EXISTS idx_rp_user
    ON research_papers(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rp_arxiv
    ON research_papers(arxiv_id) WHERE arxiv_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS paper_summaries (
    paper_id        TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    short_summary   TEXT NOT NULL,                    -- 1-paragraph
    key_findings    TEXT,                              -- JSON list of strings
    methods         TEXT,
    limitations     TEXT,
    future_work     TEXT,
    ai_call_id      TEXT,                              -- llm_obs.ai_calls.id
    generated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS literature_collections (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    paper_count     INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lc_user
    ON literature_collections(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS collection_papers (
    collection_id   TEXT NOT NULL,
    paper_id        TEXT NOT NULL,
    position        INTEGER NOT NULL,
    notes           TEXT,
    added_at        REAL NOT NULL,
    PRIMARY KEY (collection_id, paper_id)
);
CREATE INDEX IF NOT EXISTS idx_cp_position
    ON collection_papers(collection_id, position);

CREATE TABLE IF NOT EXISTS research_citations (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    paper_id        TEXT NOT NULL,
    page_number     INTEGER,
    section         TEXT,
    citation_text   TEXT NOT NULL,
    note            TEXT,
    tags_json       TEXT,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rc_user
    ON research_citations(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rc_paper
    ON research_citations(paper_id);

CREATE TABLE IF NOT EXISTS research_gaps (
    id              TEXT PRIMARY KEY,
    collection_id   TEXT NOT NULL,
    theme           TEXT NOT NULL,                    -- 'reinforcement learning in robotics'
    rationale       TEXT,
    coverage_score  REAL,                             -- 0..1, lower = bigger gap
    suggested_keywords TEXT,                            -- JSON
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rg_collection
    ON research_gaps(collection_id, coverage_score);
"""


# Min paper count below which gap-detection isn't useful
MIN_PAPERS_FOR_GAP_DETECTION = 3

# Year sanity bounds
MIN_PUB_YEAR = 1900
MAX_PUB_YEAR = 2100


# DOI / arXiv regex
_DOI_RE = re.compile(
    r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.IGNORECASE,
)
_ARXIV_RE = re.compile(
    r"^\d{4}\.\d{4,5}(v\d+)?$",
)


def _db_path() -> Path:
    custom = os.environ.get("PADHAI_DB_PATH")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".padhai" / "jobs.db"


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.executescript(SCHEMA)
    return conn


def migrate() -> None:
    with _conn():
        pass


# ---------- dataclasses ----------

@dataclass(frozen=True)
class Paper:
    id: str
    upload_id: str | None
    user_id: str
    title: str
    authors: list[str]
    year: int | None
    venue: str | None
    doi: str | None
    arxiv_id: str | None
    abstract: str | None
    keywords: list[str]
    citation_count: int | None
    created_at: float


@dataclass(frozen=True)
class Summary:
    paper_id: str
    user_id: str
    short_summary: str
    key_findings: list[str]
    methods: str | None
    limitations: str | None
    future_work: str | None
    ai_call_id: str | None
    generated_at: float


@dataclass(frozen=True)
class Collection:
    id: str
    user_id: str
    title: str
    description: str | None
    paper_count: int
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class Citation:
    id: str
    user_id: str
    paper_id: str
    page_number: int | None
    section: str | None
    citation_text: str
    note: str | None
    tags: list[str]
    created_at: float


@dataclass(frozen=True)
class Gap:
    id: str
    collection_id: str
    theme: str
    rationale: str | None
    coverage_score: float | None
    suggested_keywords: list[str]
    created_at: float


# ---------- paper ingest ----------

def ingest_paper(
    *,
    user_id: str,
    title: str,
    upload_id: str | None = None,
    authors: list[str] | None = None,
    year: int | None = None,
    venue: str | None = None,
    doi: str | None = None,
    arxiv_id: str | None = None,
    abstract: str | None = None,
    keywords: list[str] | None = None,
    citation_count: int | None = None,
) -> Paper:
    title = (title or "").strip()
    if len(title) < 3 or len(title) > 500:
        raise ValueError("title must be [3, 500] chars")
    if year is not None and (year < MIN_PUB_YEAR or year > MAX_PUB_YEAR):
        raise ValueError(
            f"year must be in [{MIN_PUB_YEAR}, {MAX_PUB_YEAR}]",
        )
    if doi is not None:
        doi = doi.strip()
        if not _DOI_RE.match(doi):
            raise ValueError(f"invalid DOI: {doi!r}")
    if arxiv_id is not None:
        arxiv_id = arxiv_id.strip()
        if not _ARXIV_RE.match(arxiv_id):
            raise ValueError(f"invalid arXiv id: {arxiv_id!r}")
    pid = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO research_papers "
                "(id, upload_id, user_id, title, authors, year, "
                " venue, doi, arxiv_id, abstract, keywords, "
                " citation_count, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, upload_id, user_id, title,
                 json.dumps(authors) if authors else None,
                 year, venue, doi, arxiv_id, abstract,
                 json.dumps(keywords) if keywords else None,
                 citation_count, time.time()),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(
            f"paper with DOI {doi!r} already ingested by this user",
        ) from e
    return get_paper(pid)  # type: ignore[return-value]


def get_paper(paper_id: str) -> Paper | None:
    with _conn() as conn:
        r = conn.execute(
            _P_SEL + " WHERE id = ?", (paper_id,),
        ).fetchone()
    return _row_to_paper(r) if r else None


_P_SEL = (
    "SELECT id, upload_id, user_id, title, authors, year, "
    "       venue, doi, arxiv_id, abstract, keywords, "
    "       citation_count, created_at "
    "FROM research_papers"
)


def _row_to_paper(r) -> Paper:
    return Paper(
        id=r[0], upload_id=r[1], user_id=r[2], title=r[3],
        authors=json.loads(r[4]) if r[4] else [],
        year=r[5], venue=r[6], doi=r[7], arxiv_id=r[8],
        abstract=r[9],
        keywords=json.loads(r[10]) if r[10] else [],
        citation_count=r[11], created_at=r[12],
    )


def list_user_papers(user_id: str) -> list[Paper]:
    with _conn() as conn:
        rows = conn.execute(
            _P_SEL + " WHERE user_id = ? "
            "ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [_row_to_paper(r) for r in rows]


# ---------- summarisation ----------

def save_summary(
    *,
    paper_id: str,
    user_id: str,
    short_summary: str,
    key_findings: list[str] | None = None,
    methods: str | None = None,
    limitations: str | None = None,
    future_work: str | None = None,
    ai_call_id: str | None = None,
) -> Summary:
    """Persist an LLM-generated summary. Caller does the LLM call
    + passes the structured result here. Idempotent on paper_id —
    later writes replace."""
    paper = get_paper(paper_id)
    if not paper:
        raise ValueError("paper not found")
    if paper.user_id != user_id:
        raise PermissionError("not your paper")
    short_summary = (short_summary or "").strip()
    if len(short_summary) < 20 or len(short_summary) > 8000:
        raise ValueError("short_summary must be [20, 8000] chars")
    if key_findings is not None and len(key_findings) > 30:
        raise ValueError("max 30 key_findings")
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO paper_summaries "
            "(paper_id, user_id, short_summary, key_findings, "
            " methods, limitations, future_work, ai_call_id, "
            " generated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (paper_id, user_id, short_summary,
             json.dumps(key_findings) if key_findings else None,
             methods, limitations, future_work, ai_call_id, now),
        )
    return get_summary(paper_id)  # type: ignore[return-value]


def get_summary(paper_id: str) -> Summary | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT paper_id, user_id, short_summary, "
            "       key_findings, methods, limitations, "
            "       future_work, ai_call_id, generated_at "
            "FROM paper_summaries WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
    if not r:
        return None
    return Summary(
        paper_id=r[0], user_id=r[1], short_summary=r[2],
        key_findings=json.loads(r[3]) if r[3] else [],
        methods=r[4], limitations=r[5], future_work=r[6],
        ai_call_id=r[7], generated_at=r[8],
    )


# ---------- literature collections ----------

def create_collection(
    *,
    user_id: str,
    title: str,
    description: str | None = None,
) -> Collection:
    title = (title or "").strip()
    if len(title) < 3 or len(title) > 200:
        raise ValueError("title must be [3, 200] chars")
    cid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO literature_collections "
            "(id, user_id, title, description, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cid, user_id, title, description, now, now),
        )
    return get_collection(cid)  # type: ignore[return-value]


def get_collection(collection_id: str) -> Collection | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, user_id, title, description, "
            "       paper_count, created_at, updated_at "
            "FROM literature_collections WHERE id = ?",
            (collection_id,),
        ).fetchone()
    if not r:
        return None
    return Collection(
        id=r[0], user_id=r[1], title=r[2],
        description=r[3], paper_count=r[4],
        created_at=r[5], updated_at=r[6],
    )


def list_user_collections(user_id: str) -> list[Collection]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, user_id, title, description, "
            "       paper_count, created_at, updated_at "
            "FROM literature_collections WHERE user_id = ? "
            "ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
    return [
        Collection(
            id=r[0], user_id=r[1], title=r[2],
            description=r[3], paper_count=r[4],
            created_at=r[5], updated_at=r[6],
        )
        for r in rows
    ]


def add_to_collection(
    *,
    collection_id: str,
    paper_id: str,
    user_id: str,
    notes: str | None = None,
) -> int:
    """Append a paper to a collection. Returns the new position
    (1-indexed). Idempotent — re-adding returns existing position."""
    collection = get_collection(collection_id)
    if not collection:
        raise ValueError("collection not found")
    if collection.user_id != user_id:
        raise PermissionError("not your collection")
    paper = get_paper(paper_id)
    if not paper:
        raise ValueError("paper not found")
    now = time.time()
    with _conn() as conn:
        existing = conn.execute(
            "SELECT position FROM collection_papers "
            "WHERE collection_id = ? AND paper_id = ?",
            (collection_id, paper_id),
        ).fetchone()
        if existing:
            return int(existing[0])
        pos_row = conn.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 "
            "FROM collection_papers WHERE collection_id = ?",
            (collection_id,),
        ).fetchone()
        position = int(pos_row[0])
        conn.execute(
            "INSERT INTO collection_papers "
            "(collection_id, paper_id, position, notes, "
            " added_at) VALUES (?, ?, ?, ?, ?)",
            (collection_id, paper_id, position, notes, now),
        )
        conn.execute(
            "UPDATE literature_collections SET "
            " paper_count = paper_count + 1, updated_at = ? "
            "WHERE id = ?",
            (now, collection_id),
        )
    return position


def remove_from_collection(
    *,
    collection_id: str,
    paper_id: str,
    user_id: str,
) -> bool:
    collection = get_collection(collection_id)
    if not collection or collection.user_id != user_id:
        return False
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM collection_papers "
            "WHERE collection_id = ? AND paper_id = ?",
            (collection_id, paper_id),
        )
        if cur.rowcount > 0:
            conn.execute(
                "UPDATE literature_collections SET "
                " paper_count = paper_count - 1, "
                " updated_at = ? "
                "WHERE id = ?",
                (time.time(), collection_id),
            )
        return cur.rowcount > 0


def list_collection_papers(collection_id: str) -> list[dict]:
    """Returns papers + notes + position, ordered."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT cp.position, cp.paper_id, cp.notes, "
            "       cp.added_at, p.title, p.authors, p.year, "
            "       p.venue, p.doi, p.abstract, p.keywords "
            "FROM collection_papers cp "
            "JOIN research_papers p ON p.id = cp.paper_id "
            "WHERE cp.collection_id = ? "
            "ORDER BY cp.position",
            (collection_id,),
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "position": r[0], "paper_id": r[1],
            "notes": r[2], "added_at": r[3],
            "title": r[4],
            "authors": json.loads(r[5]) if r[5] else [],
            "year": r[6], "venue": r[7], "doi": r[8],
            "abstract": r[9],
            "keywords": json.loads(r[10]) if r[10] else [],
        })
    return out


# ---------- citation manager ----------

def flag_citation(
    *,
    user_id: str,
    paper_id: str,
    citation_text: str,
    page_number: int | None = None,
    section: str | None = None,
    note: str | None = None,
    tags: list[str] | None = None,
) -> Citation:
    paper = get_paper(paper_id)
    if not paper:
        raise ValueError("paper not found")
    citation_text = (citation_text or "").strip()
    if len(citation_text) < 10 or len(citation_text) > 4000:
        raise ValueError("citation_text must be [10, 4000] chars")
    cid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO research_citations "
            "(id, user_id, paper_id, page_number, section, "
            " citation_text, note, tags_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, user_id, paper_id, page_number, section,
             citation_text, note,
             json.dumps(tags) if tags else None, now),
        )
    return Citation(
        id=cid, user_id=user_id, paper_id=paper_id,
        page_number=page_number, section=section,
        citation_text=citation_text, note=note,
        tags=tags or [], created_at=now,
    )


def list_citations(
    *,
    user_id: str,
    paper_id: str | None = None,
    tag: str | None = None,
    limit: int = 100,
) -> list[Citation]:
    limit = max(1, min(limit, 500))
    sql = (
        "SELECT id, user_id, paper_id, page_number, section, "
        "       citation_text, note, tags_json, created_at "
        "FROM research_citations WHERE user_id = ?"
    )
    params: list = [user_id]
    if paper_id:
        sql += " AND paper_id = ?"; params.append(paper_id)
    if tag:
        sql += " AND tags_json LIKE ?"; params.append(f"%{tag}%")
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        Citation(
            id=r[0], user_id=r[1], paper_id=r[2],
            page_number=r[3], section=r[4],
            citation_text=r[5], note=r[6],
            tags=json.loads(r[7]) if r[7] else [],
            created_at=r[8],
        )
        for r in rows
    ]


def delete_citation(*, citation_id: str, user_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM research_citations "
            "WHERE id = ? AND user_id = ?",
            (citation_id, user_id),
        )
        return cur.rowcount > 0


# ---------- literature map ----------

def literature_map(collection_id: str) -> dict:
    """Returns a graph view of a collection: nodes (papers) +
    edges (shared keywords / authors / years). Drives the
    'literature review map' UX.

    This is a deliberately simple keyword-overlap edge weighting.
    Production-grade work pulls in Semantic Scholar's citation
    graph; for v3.10 the local-keyword version is enough."""
    papers = list_collection_papers(collection_id)
    if not papers:
        return {"nodes": [], "edges": []}
    nodes = []
    for p in papers:
        nodes.append({
            "id": p["paper_id"],
            "title": p["title"],
            "year": p["year"],
            "authors": p["authors"][:3],
            "keywords": p["keywords"],
        })
    # Edges — for each pair, count shared keywords; emit if >= 1
    edges = []
    for i, p1 in enumerate(papers):
        kw1 = set(k.lower() for k in p1["keywords"])
        au1 = set(a.lower() for a in p1["authors"])
        for p2 in papers[i + 1:]:
            kw2 = set(k.lower() for k in p2["keywords"])
            au2 = set(a.lower() for a in p2["authors"])
            kw_overlap = len(kw1 & kw2)
            au_overlap = len(au1 & au2)
            if kw_overlap or au_overlap:
                edges.append({
                    "source": p1["paper_id"],
                    "target": p2["paper_id"],
                    "shared_keywords": sorted(kw1 & kw2),
                    "shared_authors": sorted(au1 & au2),
                    "weight": kw_overlap + au_overlap * 2,
                })
    return {"nodes": nodes, "edges": edges}


# ---------- gap detection ----------

def detect_gaps(
    *,
    collection_id: str,
    user_id: str,
    proposed_themes: list[str] | None = None,
) -> list[Gap]:
    """Run gap detection over a collection. Two strategies:
      1. Auto-detection: keywords mentioned in <30% of papers'
         abstracts are flagged as 'thin coverage' candidates.
      2. Hypothesised themes: caller passes specific themes
         they're worried about; we compute coverage_score per
         theme = papers_mentioning / total_papers.

    Persists the detected gaps in `research_gaps` so the user
    can act on them later. Returns the newly-detected gaps.
    """
    collection = get_collection(collection_id)
    if not collection:
        raise ValueError("collection not found")
    if collection.user_id != user_id:
        raise PermissionError("not your collection")
    papers = list_collection_papers(collection_id)
    if len(papers) < MIN_PAPERS_FOR_GAP_DETECTION:
        return []
    # Clear prior gaps for this collection (re-detect from scratch)
    with _conn() as conn:
        conn.execute(
            "DELETE FROM research_gaps WHERE collection_id = ?",
            (collection_id,),
        )
    detected: list[Gap] = []
    now = time.time()
    total = len(papers)
    # Strategy 1 — auto: low-frequency keywords across the collection
    kw_counts: Counter[str] = Counter()
    for p in papers:
        for k in p["keywords"]:
            kw_counts[k.lower()] += 1
    auto_themes = [
        k for k, n in kw_counts.items()
        if n / total < 0.3 and n >= 1
    ]
    # Strategy 2 — caller-supplied
    proposed = proposed_themes or []
    all_themes = list(set(auto_themes) | set(proposed))
    for theme in all_themes:
        # Coverage = fraction of papers whose abstract/keywords
        # mention this theme (case-insensitive substring match)
        theme_l = theme.lower()
        hits = 0
        for p in papers:
            blob = " ".join([
                (p["title"] or "").lower(),
                (p["abstract"] or "").lower(),
                " ".join((k.lower() for k in p["keywords"])),
            ])
            if theme_l in blob:
                hits += 1
        coverage = hits / total
        if coverage >= 0.5:
            continue   # well covered, not a gap
        rationale = (
            f"Theme {theme!r} appears in {hits}/{total} papers "
            f"({coverage:.0%} coverage)"
        )
        gid = uuid.uuid4().hex
        suggested = _suggest_keywords(theme, papers)
        with _conn() as conn:
            conn.execute(
                "INSERT INTO research_gaps "
                "(id, collection_id, theme, rationale, "
                " coverage_score, suggested_keywords, "
                " created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (gid, collection_id, theme, rationale,
                 round(coverage, 3),
                 json.dumps(suggested), now),
            )
        detected.append(Gap(
            id=gid, collection_id=collection_id,
            theme=theme, rationale=rationale,
            coverage_score=round(coverage, 3),
            suggested_keywords=suggested,
            created_at=now,
        ))
    return detected


def _suggest_keywords(theme: str, papers: list[dict]) -> list[str]:
    """For a theme we're under-covered on, suggest 3-5 related
    keywords the student could search. Pulled from co-occurring
    keywords in the collection's papers."""
    related: Counter[str] = Counter()
    theme_l = theme.lower()
    for p in papers:
        kws = [k.lower() for k in p["keywords"]]
        if theme_l in kws:
            for k in kws:
                if k != theme_l:
                    related[k] += 1
    return [k for k, _ in related.most_common(5)]


def list_gaps(collection_id: str) -> list[Gap]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, collection_id, theme, rationale, "
            "       coverage_score, suggested_keywords, "
            "       created_at "
            "FROM research_gaps WHERE collection_id = ? "
            "ORDER BY coverage_score ASC",
            (collection_id,),
        ).fetchall()
    return [
        Gap(
            id=r[0], collection_id=r[1], theme=r[2],
            rationale=r[3], coverage_score=r[4],
            suggested_keywords=(
                json.loads(r[5]) if r[5] else []
            ),
            created_at=r[6],
        )
        for r in rows
    ]
