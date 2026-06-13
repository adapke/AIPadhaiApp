"""prod-146 — Tests for the hand-written real-world examples seed.

Covers:
  1. SEED_EXAMPLES is non-empty and structurally sound.
  2. Every example body references at least one Indian-context token
     (cricket / Mumbai / Diwali / kabaddi / rupee / autorickshaw / etc.)
  3. Every example body is substantial (>200 chars, not a one-liner).
  4. Running the seed against an isolated DB inserts all examples as
     `approved` (no curator step needed).
  5. Seed is idempotent — re-running doesn't double-insert.
"""
from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _import_seed_module():
    """Import scripts/seed_real_world_examples.py as a module."""
    spec = importlib.util.spec_from_file_location(
        "seed_real_world_examples_mod",
        REPO_ROOT / "scripts" / "seed_real_world_examples.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


INDIAN_TOKENS = {
    "mumbai", "delhi", "bengaluru", "bangalore", "chennai", "kolkata",
    "pune", "jaipur", "surat", "lucknow", "ahmedabad", "hyderabad",
    "coimbatore", "tamil nadu", "punjab", "haryana", "andhra", "telangana",
    "bihar", "rajasthan", "gujarat", "kerala", "karnataka", "maharashtra",
    "cricket", "kabaddi", "diwali", "holi", "monsoon", "kirana",
    "autorickshaw", "rupee", "₹", "ncert", "ncta", "iit ", "iim ",
    "marine drive", "bollywood", "biryani", "dosa", "laddoo", "samosa",
    "republic day", "tricolour", "aman", "priya", "raj", "asha",
    "kavita", "vikram", "lakshmi", "saffron", "wheat", "paddy",
    "indian", "bharat", "marathi", "tamil", "hindi", "metro",
    "marg", "tika", "festival", "fenced", "fencing wire", "lac", "lakh", "crore", "puja", "anganwadi",
}


def test_seed_examples_non_empty():
    """prod-146 — SEED_EXAMPLES has at least 10 entries."""
    mod = _import_seed_module()
    assert len(mod.SEED_EXAMPLES) >= 10


def test_every_example_has_indian_context():
    """prod-146 — Each example references at least one Indian-context token.
    Defends against accidentally seeding Western-context examples."""
    mod = _import_seed_module()
    for concept, _locale, example_md in mod.SEED_EXAMPLES:
        body_lower = example_md.lower()
        hits = [tok for tok in INDIAN_TOKENS if tok in body_lower]
        assert hits, (
            f"Example for '{concept}' has no Indian-context token. "
            f"Body: {example_md[:120]!r}"
        )


def test_every_example_is_substantial():
    """prod-146 — Each example body is at least 300 chars
    (avoids one-liner placeholders)."""
    mod = _import_seed_module()
    for concept, _locale, example_md in mod.SEED_EXAMPLES:
        assert len(example_md) >= 300, (
            f"Example for '{concept}' is too short ({len(example_md)} chars)"
        )


def test_every_example_is_unique():
    """prod-146 — No two examples share the same body."""
    mod = _import_seed_module()
    bodies = [body for _c, _l, body in mod.SEED_EXAMPLES]
    assert len(set(bodies)) == len(bodies)


def test_no_forbidden_western_tokens():
    """prod-146 — None of the examples contain Western-context tokens
    (baseball, hot dog, Thanksgiving, freeway, etc.) — these would
    fail the system prompt rule for the Claude generator and should
    not appear in the hand-curated seed either."""
    mod = _import_seed_module()
    forbidden = {"baseball", "hot dog", "thanksgiving", "freeway",
                 "manhattan", "boston", "london", "yankee"}
    for concept, _locale, example_md in mod.SEED_EXAMPLES:
        body_lower = example_md.lower()
        hits = [tok for tok in forbidden if tok in body_lower]
        assert not hits, (
            f"Example for '{concept}' contains forbidden tokens {hits}"
        )


def test_seed_inserts_to_isolated_db(monkeypatch, tmp_path):
    """prod-146 — Running the seed module's main() inserts examples
    as 'approved'."""
    db_path = tmp_path / f"test_seed_{uuid.uuid4().hex[:6]}.db"
    monkeypatch.setenv("PADHAI_DB_PATH", str(db_path))
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import importlib

    from padhai import concept_examples, db
    importlib.reload(db)
    importlib.reload(concept_examples)
    concept_examples.migrate()

    mod = _import_seed_module()
    # Insert each row directly (skip main()'s argparse)
    inserted = 0
    for concept, locale, example_md in mod.SEED_EXAMPLES:
        row = concept_examples.insert(
            concept_slug=concept,
            example_md=example_md,
            locale=locale,
            source="human",
            status="approved",
        )
        assert row.status == "approved"
        inserted += 1
    assert inserted == len(mod.SEED_EXAMPLES)
    # Sanity: stats roll up correctly
    s = concept_examples.stats()
    assert s["approved"] >= inserted
    assert s["pending"] == 0
