"""v3.3 — Retrieval / RAG over document_pages.

The piece missing between v3.1 citations + v3.2 tutor_grounding:
given a student query, **find the chunks** to cite. Today the
citation flow is `caller-supplies-chunks → record_answer stores
them`; this module is the *caller* that picks them.

Three layers:

  1. `doc_chunks`  — chunked extracted text per page (split on
     paragraph + size cap); 1 page typically yields 1-5 chunks
  2. `chunk_embeddings` — vector per chunk. We support two backends:
     • token-overlap BM25-ish scoring (default, zero-dep)
     • Anthropic-style external embedding via env-gated provider
     The dimension is stored so we don't mix providers.
  3. `retrieve()` — query → top-k chunks with relevance score,
     ready to hand to `citations.record_answer` / `tutor_grounding`

The default scorer is intentionally **dependency-free**: token
overlap + IDF. Production swaps in an embedding provider when the
env var is set; the API stays identical.

We deliberately do NOT pull in `sentence-transformers` or
`faiss` — those drag in 500MB of torch + libgomp. The token
scorer is "good enough" for one-document chats; once corpora
grow past ~10k chunks per user, swap to an external vector DB
(Pinecone / pgvector) via the `PADHAI_VECTOR_PROVIDER` shim.

Three public APIs:
  index_page(upload_id, page_number)       — chunk + embed one page
  index_upload(upload_id)                   — fan over all pages
  retrieve(query, sources, top_k=5)         — query → ranked chunks
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS doc_chunks (
    id                  TEXT PRIMARY KEY,
    upload_id           TEXT NOT NULL,
    page_number         INTEGER NOT NULL,
    page_id             TEXT,                       -- FK document_pages.id
    chunk_index         INTEGER NOT NULL,           -- 0-indexed within page
    section             TEXT,                        -- best-effort '4.2 Linear Equations'
    chunk_text          TEXT NOT NULL,
    token_count         INTEGER NOT NULL,
    created_at          REAL NOT NULL,
    UNIQUE (upload_id, page_number, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_dc_upload
    ON doc_chunks(upload_id, page_number);

CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id            TEXT PRIMARY KEY,
    provider            TEXT NOT NULL,               -- 'tokenscore' / 'anthropic' / 'oss'
    dimension           INTEGER,                     -- NULL for token-overlap (no real vector)
    vector_json         TEXT,                        -- JSON array; NULL for tokenscore
    term_freq_json      TEXT NOT NULL,               -- always populated; bag-of-tokens
    created_at          REAL NOT NULL
);

-- Inverted index for token-scorer — built lazily on first retrieve
CREATE TABLE IF NOT EXISTS chunk_token_index (
    token               TEXT NOT NULL,
    chunk_id            TEXT NOT NULL,
    term_freq           INTEGER NOT NULL,
    PRIMARY KEY (token, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_cti_chunk
    ON chunk_token_index(chunk_id);
"""


# Chunk size — words per chunk. Tuned so a typical NCERT page (≈300
# words) becomes 1-2 chunks. Smaller chunks → more precise
# citations + worse recall; larger → opposite. 200 strikes a good
# balance for textbook content; less for lecture transcripts.
CHUNK_TARGET_WORDS = 200
CHUNK_MIN_WORDS = 40       # don't emit a tiny tail chunk
CHUNK_OVERLAP_WORDS = 30    # carry overlap between chunks


# Stop words — short list, English + Hindi-Romanized hybrids.
# IDF still discounts these effectively; this list just trims
# upstream noise during tokenisation.
_STOP = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "of",
    "in", "on", "at", "to", "for", "and", "or", "but", "if",
    "then", "else", "as", "by", "with", "from", "this", "that",
    "these", "those", "it", "its", "he", "she", "they", "them",
    "his", "her", "their", "i", "we", "you", "your", "my", "our",
    "ka", "ki", "ke", "ko", "se", "me", "hai", "nahi", "kya",
})


# Default provider — falls back to tokenscore. Override via env.
DEFAULT_PROVIDER = os.environ.get(
    "PADHAI_RETRIEVAL_PROVIDER", "tokenscore",
)


_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9]+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """Lowercase + stopword-strip + min length 3. Cheap, good
    enough for English + transliterated Indic."""
    return [
        t for t in (m.group(0).lower() for m in _TOKEN_RE.finditer(text or ""))
        if len(t) >= 3 and t not in _STOP
    ]


def _db_path() -> Path:
    from . import db as _db
    return _db.sqlite_path()


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
class Chunk:
    id: str
    upload_id: str
    page_number: int
    page_id: str | None
    chunk_index: int
    section: str | None
    chunk_text: str
    token_count: int
    created_at: float


@dataclass(frozen=True)
class RetrievalHit:
    chunk: Chunk
    score: float                         # normalised 0..1
    matched_tokens: list[str]


# ---------- chunking ----------

def _split_into_chunks(
    text: str,
    *,
    target_words: int = CHUNK_TARGET_WORDS,
    min_words: int = CHUNK_MIN_WORDS,
    overlap: int = CHUNK_OVERLAP_WORDS,
) -> list[str]:
    """Word-window chunker. Respects paragraph boundaries when
    possible — splits inside paragraphs only when they exceed
    target. Overlap reduces the chance of cutting key context."""
    if not text or not text.strip():
        return []
    words = text.split()
    if len(words) <= target_words:
        return [text.strip()]
    chunks: list[str] = []
    i = 0
    while i < len(words):
        end = min(i + target_words, len(words))
        chunk = " ".join(words[i:end])
        chunks.append(chunk.strip())
        if end == len(words):
            break
        # Slide window with overlap
        i = end - overlap
        if i < 0:
            i = 0
    # Drop tail chunk that's too small + merge into prior
    if len(chunks) >= 2 and len(chunks[-1].split()) < min_words:
        tail = chunks.pop()
        chunks[-1] = chunks[-1] + " " + tail
    return chunks


_SECTION_RE = re.compile(
    r"^\s*((?:\d+\.)*\d+)\s+([A-Z][A-Za-z0-9\s,'-]{3,80})\s*$",
)


def _guess_section(text: str) -> str | None:
    """Pluck the first '4.2 Linear Equations'-style heading from
    the chunk's text — purely heuristic. Returns None if nothing
    obvious is found."""
    for line in (text or "").splitlines()[:5]:
        m = _SECTION_RE.match(line)
        if m:
            return f"{m.group(1)} {m.group(2).strip()}"
    return None


# ---------- indexing ----------

def index_page(
    *,
    upload_id: str,
    page_number: int,
    page_id: str | None = None,
    extracted_text: str | None = None,
) -> int:
    """Chunk + embed a single page. Returns the number of chunks
    indexed. Idempotent on (upload, page, chunk_index) — re-running
    skips already-indexed chunks.

    If `extracted_text` is None we try to look it up from
    document_pages. If still missing, no-op + return 0."""
    if extracted_text is None:
        try:
            from . import schema_v2 as _sv2
            pages = _sv2.list_pages_for_upload(upload_id)
            for p in pages:
                if p.page_number == page_number and p.extracted_text:
                    extracted_text = p.extracted_text
                    page_id = page_id or p.id
                    break
        except Exception:  # noqa: BLE001
            pass
    if not extracted_text or not extracted_text.strip():
        return 0
    chunks = _split_into_chunks(extracted_text)
    now = time.time()
    written = 0
    with _conn() as conn:
        for idx, ctext in enumerate(chunks):
            section = _guess_section(ctext)
            cid = uuid.uuid4().hex
            try:
                conn.execute(
                    "INSERT INTO doc_chunks "
                    "(id, upload_id, page_number, page_id, "
                    " chunk_index, section, chunk_text, "
                    " token_count, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (cid, upload_id, page_number, page_id, idx,
                     section, ctext, len(ctext.split()), now),
                )
            except sqlite3.IntegrityError:
                # Re-running for this (upload, page, idx) — refetch id
                r = conn.execute(
                    "SELECT id FROM doc_chunks "
                    "WHERE upload_id = ? AND page_number = ? "
                    "  AND chunk_index = ?",
                    (upload_id, page_number, idx),
                ).fetchone()
                cid = r[0] if r else cid
            _embed_chunk(cid, ctext, conn)
            written += 1
    return written


def _embed_chunk(chunk_id: str, text: str, conn) -> None:
    """Embed a chunk + populate the inverted index. The 'embed' is
    really a bag-of-tokens for the default scorer; an external
    provider drops a real vector into vector_json."""
    tokens = _tokenize(text)
    tf = Counter(tokens)
    provider = DEFAULT_PROVIDER
    vector_json = None
    dimension = None
    if provider == "anthropic" or provider == "oss":
        try:
            vec = _external_embed(text)
            if vec:
                vector_json = json.dumps(vec)
                dimension = len(vec)
        except Exception:  # noqa: BLE001
            provider = "tokenscore"   # fallback silently
    # Upsert embedding
    conn.execute(
        "INSERT OR REPLACE INTO chunk_embeddings "
        "(chunk_id, provider, dimension, vector_json, "
        " term_freq_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (chunk_id, provider, dimension, vector_json,
         json.dumps(dict(tf)), time.time()),
    )
    # Refresh inverted index for the chunk — easier than diffing
    conn.execute(
        "DELETE FROM chunk_token_index WHERE chunk_id = ?",
        (chunk_id,),
    )
    for token, freq in tf.items():
        conn.execute(
            "INSERT INTO chunk_token_index "
            "(token, chunk_id, term_freq) VALUES (?, ?, ?)",
            (token, chunk_id, freq),
        )


def _external_embed(text: str) -> list[float] | None:
    """Stub for external embedding providers. Real impl plugs in
    the user's Anthropic / OSS embed key here. Returns None if no
    provider key is configured — caller falls back to tokenscore."""
    # Intentionally unimplemented at v3.3 ship — the env var gates
    # this code path; tokenscore is the production default until
    # we wire up a vendor. See `padhai/llm_cache.py` for the
    # provider-shim pattern.
    return None


def index_upload(upload_id: str) -> int:
    """Convenience: index every page of an upload that has
    extracted_text. Returns total chunks written."""
    try:
        from . import schema_v2 as _sv2
        pages = _sv2.list_pages_for_upload(upload_id)
    except Exception:  # noqa: BLE001
        return 0
    total = 0
    for p in pages:
        if not p.extracted_text:
            continue
        total += index_page(
            upload_id=upload_id, page_number=p.page_number,
            page_id=p.id, extracted_text=p.extracted_text,
        )
    return total


def list_chunks_for_upload(upload_id: str) -> list[Chunk]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, upload_id, page_number, page_id, "
            "       chunk_index, section, chunk_text, "
            "       token_count, created_at "
            "FROM doc_chunks WHERE upload_id = ? "
            "ORDER BY page_number, chunk_index",
            (upload_id,),
        ).fetchall()
    return [
        Chunk(
            id=r[0], upload_id=r[1], page_number=r[2],
            page_id=r[3], chunk_index=r[4], section=r[5],
            chunk_text=r[6], token_count=r[7], created_at=r[8],
        )
        for r in rows
    ]


def chunk_count(*, upload_id: str | None = None) -> int:
    sql = "SELECT COUNT(*) FROM doc_chunks"
    params: list = []
    if upload_id:
        sql += " WHERE upload_id = ?"; params.append(upload_id)
    with _conn() as conn:
        r = conn.execute(sql, params).fetchone()
    return int(r[0] or 0)


def delete_upload_chunks(upload_id: str) -> int:
    """Remove all chunks + embeddings for an upload. Called when
    the user deletes the source document. Cascades to the inverted
    index."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id FROM doc_chunks WHERE upload_id = ?",
            (upload_id,),
        ).fetchall()
        chunk_ids = [r[0] for r in rows]
        if not chunk_ids:
            return 0
        placeholders = ",".join("?" * len(chunk_ids))
        conn.execute(
            f"DELETE FROM chunk_token_index WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        )
        conn.execute(
            f"DELETE FROM chunk_embeddings WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        )
        conn.execute(
            "DELETE FROM doc_chunks WHERE upload_id = ?",
            (upload_id,),
        )
    return len(chunk_ids)


# ---------- retrieve ----------

def retrieve(
    *,
    query: str,
    upload_ids: list[str] | None = None,
    top_k: int = 5,
    min_score: float = 0.0,
) -> list[RetrievalHit]:
    """Token-overlap retrieval: tokenise the query, look up
    matching chunks via the inverted index, score by:

      score = sum_t(query_t × log(N / df_t) × chunk_tf_t) / chunk_norm

    Plain TF-IDF cosine, normalised by chunk length. `upload_ids`
    scopes the search to a specific set of source documents
    (typically the student's currently-open chapter / book).

    Returns the top-k hits with scores normalised to [0, 1] so they
    plug straight into `citations.record_answer`'s `relevance`
    parameter. Hits below `min_score` are dropped — callers can
    use this to gate strict-mode answers.
    """
    top_k = max(1, min(top_k, 50))
    q_tokens = _tokenize(query)
    if not q_tokens:
        return []
    # Total chunks in scope (for IDF denominator)
    with _conn() as conn:
        if upload_ids:
            ph = ",".join("?" * len(upload_ids))
            r = conn.execute(
                f"SELECT COUNT(*) FROM doc_chunks WHERE upload_id IN ({ph})",
                upload_ids,
            ).fetchone()
        else:
            r = conn.execute(
                "SELECT COUNT(*) FROM doc_chunks",
            ).fetchone()
        n_chunks = int(r[0] or 0)
    if n_chunks == 0:
        return []
    # Document frequency per query token (within scope)
    with _conn() as conn:
        scope_clause = ""
        scope_params: list = []
        if upload_ids:
            ph = ",".join("?" * len(upload_ids))
            scope_clause = f" AND upload_id IN ({ph})"
            scope_params = upload_ids
        idf: dict[str, float] = {}
        ph_q = ",".join("?" * len(q_tokens))
        rows = conn.execute(
            f"SELECT cti.token, COUNT(DISTINCT cti.chunk_id) "
            "FROM chunk_token_index cti "
            "JOIN doc_chunks dc ON dc.id = cti.chunk_id "
            f"WHERE cti.token IN ({ph_q}){scope_clause} "
            "GROUP BY cti.token",
            list(q_tokens) + scope_params,
        ).fetchall()
        for tok, df in rows:
            if df > 0:
                idf[tok] = math.log((n_chunks + 1) / (df + 1)) + 1
        if not idf:
            return []
        # Score every chunk that contains ≥1 query token
        ph_idf = ",".join("?" * len(idf))
        cand_rows = conn.execute(
            f"SELECT cti.chunk_id, cti.token, cti.term_freq "
            "FROM chunk_token_index cti "
            "JOIN doc_chunks dc ON dc.id = cti.chunk_id "
            f"WHERE cti.token IN ({ph_idf}){scope_clause}",
            list(idf.keys()) + scope_params,
        ).fetchall()
        # Accumulate scores
        scores: dict[str, float] = {}
        matched: dict[str, set[str]] = {}
        for chunk_id, tok, tf in cand_rows:
            score_inc = idf.get(tok, 0) * tf
            scores[chunk_id] = scores.get(chunk_id, 0) + score_inc
            matched.setdefault(chunk_id, set()).add(tok)
        # Normalise by chunk length (cosine-ish)
        norms = conn.execute(
            f"SELECT id, token_count FROM doc_chunks "
            f"WHERE id IN ({','.join('?'*len(scores))})",
            list(scores.keys()),
        ).fetchall() if scores else []
        for cid, tokens in norms:
            if tokens and tokens > 0:
                scores[cid] = scores[cid] / math.sqrt(tokens)
        # Sort, take top-k
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        if not ranked:
            return []
        # Normalise scores to [0, 1] by dividing by top score
        top_score = ranked[0][1] if ranked[0][1] > 0 else 1.0
        top_chunk_ids = [cid for cid, _ in ranked]
        # Fetch chunk rows
        ph_c = ",".join("?" * len(top_chunk_ids))
        chunk_rows = conn.execute(
            "SELECT id, upload_id, page_number, page_id, "
            "       chunk_index, section, chunk_text, "
            "       token_count, created_at "
            f"FROM doc_chunks WHERE id IN ({ph_c})",
            top_chunk_ids,
        ).fetchall()
    by_id = {
        r[0]: Chunk(
            id=r[0], upload_id=r[1], page_number=r[2],
            page_id=r[3], chunk_index=r[4], section=r[5],
            chunk_text=r[6], token_count=r[7], created_at=r[8],
        )
        for r in chunk_rows
    }
    out: list[RetrievalHit] = []
    for cid, sc in ranked:
        if cid not in by_id:
            continue
        norm = sc / top_score
        if norm < min_score:
            continue
        out.append(RetrievalHit(
            chunk=by_id[cid],
            score=round(norm, 4),
            matched_tokens=sorted(matched.get(cid, [])),
        ))
    return out


def hits_to_citations(hits: list[RetrievalHit]) -> list[dict]:
    """Convert retrieve() output → citation dicts ready for
    `citations.record_answer` / `tutor_grounding.send_grounded_message`.
    """
    return [
        {
            "source_kind": "upload",
            "source_id": h.chunk.upload_id,
            "page_number": h.chunk.page_number,
            "section": h.chunk.section,
            "citation_text": h.chunk.chunk_text[:2000],
            "relevance": h.score,
        }
        for h in hits
    ]
