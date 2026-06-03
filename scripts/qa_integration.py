"""Cross-module integration QA: enroll → citation → LLM cost → rollup."""
from __future__ import annotations

import os
import sys
import urllib.request
import urllib.parse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("PADHAI_DB_PATH", str(ROOT / "qa_test.db"))

from padhai import citations, llm_obs  # noqa: E402

# Caller passes the test user_id as argv[1]
user_id = sys.argv[1] if len(sys.argv) > 1 else "qa-fallback-user"

cit = [{
    "source_kind": "upload", "source_id": "qa-integration-pdf",
    "page_number": 7,
    "citation_text": "A linear equation has variables of degree one.",
    "relevance": 0.95,
}]
prov = citations.record_answer(
    surface="lesson", user_id=user_id,
    question_text="Explain linear equations",
    answer_text="A linear equation has variables raised to the first power.",
    citations=cit,
)
print(f"citation provenance id={prov.id[:12]} grounded={prov.grounded}")

llm_obs.record_call(
    module="lesson", prompt_version="v3-grounded",
    model="claude-sonnet-4-6",
    tokens_in=850, tokens_out=420, latency_ms=2100, user_id=user_id,
)
print("recorded LLM call")

rate = citations.grounding_rate()
print(
    f"grounding_rate: total={rate['total_answers']} "
    f"grounded={rate['grounded_answers']} "
    f"rate={rate['grounding_rate']}"
)

stats = llm_obs.stats_for_period(hours=24.0)
print(
    f"llm 24h: calls={stats['total_calls']} "
    f"cost_inr={stats['total_cost_inr']} "
    f"modules={list(stats['by_module'].keys())} "
    f"models={list(stats['by_model'].keys())}"
)
