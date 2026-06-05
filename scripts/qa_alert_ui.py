"""QA — admin LLM-costs page renders the new alerts block."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["PADHAI_DB_PATH"] = str(ROOT / "qa_alert_ui.db")
qa_db = Path(os.environ["PADHAI_DB_PATH"])
if qa_db.exists():
    qa_db.unlink()

from padhai import llm_obs

# Push alice over 80% of M2 cap (~₹17 / ₹20)
llm_obs.record_call(
    module="test", prompt_version="qa", model="claude-haiku-4-5",
    tokens_in=100, tokens_out=50, latency_ms=200,
    user_id="alice-uid-12345", cost_inr_paise=1700,
    subscription_tier="M2",
)
# Push bob over 100% of M2 cap
llm_obs.record_call(
    module="test", prompt_version="qa", model="claude-sonnet-4-6",
    tokens_in=4000, tokens_out=2000, latency_ms=2500,
    user_id="bob-uid-67890", cost_inr_paise=2500,
    subscription_tier="M2",
)

from admin import data, templates
from admin.auth import AdminUser

alerts = data.llm_recent_alerts()
print("alerts in DB:", len(alerts))
for a in alerts:
    print(
        f"  {a['user_id'][:8]}... bucket={a['bucket']} "
        f"spent_inr={a['spent_inr_at_crossing']} cap_inr={a['cap_inr']}"
    )

u = AdminUser(
    id="admin-1", email="admin@test", display_name="QA",
    created_at=time.time(), last_login_at=time.time(),
)
stats = data.llm_cost_stats()
html = templates.render_llm_costs(
    user=u, stats=stats, selected_hours=24, alerts=alerts,
)

checks = [
    ("html length > 5000", len(html) > 5000),
    ("contains 'Users approaching'", "Users approaching" in html),
    ("contains alice user_id prefix", "alice-uid-12345"[:14] in html),
    ("contains bob user_id prefix", "bob-uid-67890"[:14] in html),
    ("contains bucket-80 pill", "bucket-pill bucket-80" in html),
    ("contains bucket-100 pill", "bucket-pill bucket-100" in html),
    ("contains 80% summary chip", "approaching" in html),
    ("contains blocked summary chip", "blocked" in html),
]
fail = 0
for name, ok in checks:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}")
    if not ok:
        fail += 1

if fail:
    sys.exit(1)
print("ALL alert UI checks passed")
