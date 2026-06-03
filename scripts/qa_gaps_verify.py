"""QA verification for the 3 gap fixes."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB = ROOT / "qa_gaps.db"
os.environ["PADHAI_DB_PATH"] = str(DB)

user_id = sys.argv[1] if len(sys.argv) > 1 else "qa-fallback"

print("=== Gap #2 — last 5 jobs payload introspection ===")
if not DB.exists():
    print("  DB missing — server hasn't run yet.")
else:
    conn = sqlite3.connect(str(DB))
    rows = conn.execute(
        "SELECT id, payload, status FROM jobs ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    for jid, payload_json, status in rows:
        p = json.loads(payload_json) if payload_json else {}
        kind = p.get("kind", "lesson")
        page = p.get("page_number")
        total = p.get("total_pages")
        parent = p.get("parent_job_id")
        parent_disp = parent[:8] + "..." if parent else "(self)"
        print(
            f"  job {jid[:8]}... kind={kind:9} status={status:10} "
            f"page={page} total={total} parent={parent_disp}"
        )

print("\n=== Gap #1 — explainer provenance round-trip ===")
from padhai import citations

prov = citations.record_answer(
    surface="lesson",
    user_id=user_id,
    question_text="Explainer video request: linear equations",
    answer_text="A linear equation has variables of degree one.",
    citations=None,
    answer_mode="general",
    fallback_reason="topic_explainer_no_source",
)
print(f"  recorded id={prov.id[:12]} grounded={prov.grounded} fallback={prov.fallback_reason}")

rate = citations.grounding_rate(surface="lesson")
print(
    f"  surface=lesson rollup: total={rate['total_answers']} "
    f"grounded={rate['grounded_answers']} "
    f"rate={rate['grounding_rate']}"
)

# Show the user's lesson-surface answers — confirms both lesson AND
# explainer entries land in the same denominator.
user_answers = citations.list_user_answers(user_id=user_id, surface="lesson")
print(f"  user lesson-surface answers: {len(user_answers)}")
for a in user_answers:
    print(
        f"    {a.id[:8]}... grounded={a.grounded} citations={len(a.citations)} "
        f"reason={a.fallback_reason}"
    )

print("\n=== Gap #3 — confirm /explain/video signature accepts image ===")
from padhai.web import app
for r in app.routes:
    if getattr(r, "path", "") == "/explain/video":
        # FastAPI stores dependants on the endpoint
        deps = r.dependant.body_params + r.dependant.path_params + r.dependant.query_params
        names = [d.name for d in deps] + [
            d.name for d in r.dependant.dependencies
        ]
        param_names = [p.name for p in r.dependant.body_params]
        print(f"  /explain/video body params: {param_names}")
        if "image" in param_names:
            print("  [FIXED] image param accepted on /explain/video")
        else:
            print("  [!] image param missing")
        break
