"""Smoke for v3.12 — Offline packs + low-data mode.

Covers:
- Low-data prefs (get/set + defaults)
- Manifest generation w/ tier-based filtering
- Manifest idempotency on (user, pack, version, tier)
- Download lifecycle (start → progress → complete/cancel)
- Daily usage tracking + quota enforcement
- HTTP endpoints + auth gates

Run: PYTHONPATH=. python scripts/test_v3_12.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v3_12_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import offline_packs as off
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

    print("v3.12 smoke test — Offline packs + low-data")
    print("--------------------------------------------")

    off.migrate()
    off.migrate()

    # ============================================================
    # Low-data prefs — defaults
    # ============================================================
    default_prefs = off.get_low_data_prefs("brand-new-user")
    check(
        "Default prefs: quality_tier='standard', auto_downgrade=True",
        default_prefs.quality_tier == "standard"
        and default_prefs.auto_downgrade_on_cellular,
    )

    # Set prefs
    p = off.set_low_data_prefs(
        user_id="stu-1", quality_tier="text_only",
        max_daily_mb=100,
    )
    check(
        "set_low_data_prefs persists text_only + max_daily_mb",
        p.quality_tier == "text_only" and p.max_daily_mb == 100,
    )

    # Bad tier
    check(
        "set_low_data_prefs rejects bad quality_tier",
        raises(off.set_low_data_prefs, exc_type=ValueError,
               user_id="x", quality_tier="cosmic"),
    )

    # Bad quota
    check(
        "set_low_data_prefs rejects max_daily_mb > 100k",
        raises(off.set_low_data_prefs, exc_type=ValueError,
               user_id="x", max_daily_mb=999_999),
    )

    # Partial update — leave tier alone
    p2 = off.set_low_data_prefs(
        user_id="stu-1", auto_downgrade_on_cellular=False,
    )
    check(
        "set_low_data_prefs partial update preserves tier",
        p2.quality_tier == "text_only"
        and not p2.auto_downgrade_on_cellular,
    )

    # ============================================================
    # Manifest generation — tier filtering
    # ============================================================
    full_files = [
        {"ref_kind": "lesson_text", "ref_id": "txt-1",
         "priority": 1, "bytes": 20_000},
        {"ref_kind": "flashcards", "ref_id": "fc-1",
         "priority": 1, "bytes": 30_000},
        {"ref_kind": "pdf", "ref_id": "pdf-1",
         "priority": 2, "bytes": 2_000_000},
        {"ref_kind": "audio_recap", "ref_id": "au-1",
         "priority": 3, "bytes": 3_000_000},
        {"ref_kind": "lesson_video", "ref_id": "vid-1",
         "priority": 5, "bytes": 50_000_000},
    ]

    # User's pref = text_only → 2 files (priority 1 + 2)
    m_text = off.generate_manifest(
        user_id="stu-1", title="Class 10 Algebra Chapter 4",
        files=full_files, pack_code="cbse_class_10_2026",
    )
    check(
        "Manifest at text_only tier keeps priority 1-2 only",
        m_text.file_count == 3,
    )
    check(
        "Text_only total_bytes excludes audio + video",
        m_text.total_bytes == 20_000 + 30_000 + 2_000_000,
    )

    # Explicit standard tier → priority 1-3
    m_std = off.generate_manifest(
        user_id="stu-1", title="Class 10 Algebra Ch4 (std)",
        files=full_files, pack_code="cbse_class_10_2026",
        quality_tier="standard", version=2,
    )
    check(
        "Manifest at standard tier keeps priority 1-3",
        m_std.file_count == 4,
    )

    # Explicit full tier → all priorities
    m_full = off.generate_manifest(
        user_id="stu-1", title="Class 10 Algebra Ch4 (full)",
        files=full_files, pack_code="cbse_class_10_2026",
        quality_tier="full", version=3,
    )
    check(
        "Manifest at full tier keeps all 5 files",
        m_full.file_count == 5,
    )

    # Empty files
    check(
        "generate_manifest rejects empty files",
        raises(off.generate_manifest, exc_type=ValueError,
               user_id="x", title="empty pack", files=[]),
    )

    # Bad ref_kind
    check(
        "generate_manifest rejects bad ref_kind",
        raises(off.generate_manifest, exc_type=ValueError,
               user_id="x", title="bad pack",
               files=[{"ref_kind": "alien", "ref_id": "x"}]),
    )

    # Bad priority
    check(
        "generate_manifest rejects priority > 5",
        raises(off.generate_manifest, exc_type=ValueError,
               user_id="x", title="bad prio",
               files=[{"ref_kind": "pdf", "ref_id": "x",
                       "priority": 99}]),
    )

    # Bytes default fills in from EST table
    minimal_files = [
        {"ref_kind": "lesson_text", "ref_id": "tx-1",
         "priority": 1},
    ]
    m_min = off.generate_manifest(
        user_id="stu-2", title="Minimal pack",
        files=minimal_files,
    )
    check(
        "Manifest auto-fills bytes from EST table",
        m_min.files[0]["bytes"] == off.EST_BYTES_BY_KIND["lesson_text"],
    )

    # Idempotency on (user, pack, version, tier)
    m_again = off.generate_manifest(
        user_id="stu-1", title="Class 10 Algebra Chapter 4",
        files=full_files, pack_code="cbse_class_10_2026",
        quality_tier="text_only",  # match stu-1 default + version=1
    )
    check(
        "Manifest idempotent on (user, pack, version, tier)",
        m_again.id == m_text.id,
    )

    # List user manifests
    rows = off.list_user_manifests("stu-1")
    check(
        "list_user_manifests returns the 3 manifests",
        len(rows) == 3,
    )

    # Delete
    check(
        "delete_manifest True for owner",
        off.delete_manifest(
            manifest_id=m_full.id, user_id="stu-1",
        ),
    )

    # Non-owner can't delete
    check(
        "delete_manifest False for non-owner",
        not off.delete_manifest(
            manifest_id=m_std.id, user_id="stranger",
        ),
    )

    # Expired manifest — manually age it
    aged = off.generate_manifest(
        user_id="stu-3", title="aged manifest",
        files=full_files, expires_in_hours=1,
    )
    with off._conn() as conn:
        conn.execute(
            "UPDATE offline_pack_manifests SET expires_at = ? "
            "WHERE id = ?",
            (time.time() - 60, aged.id),
        )
    actives = off.list_user_manifests("stu-3", active_only=True)
    all_mans = off.list_user_manifests("stu-3", active_only=False)
    check(
        "Expired manifest excluded from active_only list",
        len(actives) == 0 and len(all_mans) == 1,
    )

    # ============================================================
    # Downloads
    # ============================================================
    d = off.start_download(
        manifest_id=m_text.id, user_id="stu-1",
        network="wifi",
    )
    check(
        "start_download returns in_progress download",
        d.status == "in_progress" and d.network == "wifi",
    )

    # Resume — second start returns same row
    d_again = off.start_download(
        manifest_id=m_text.id, user_id="stu-1",
    )
    check(
        "start_download idempotent on (manifest, user)",
        d_again.id == d.id,
    )

    # Bad network
    check(
        "start_download rejects bad network",
        raises(off.start_download, exc_type=ValueError,
               manifest_id=m_std.id, user_id="stu-1",
               network="alien_net"),
    )

    # Permission — wrong user
    check(
        "start_download refuses non-manifest-owner",
        raises(off.start_download, exc_type=PermissionError,
               manifest_id=m_text.id, user_id="stranger"),
    )

    # Expired manifest → start refused
    check(
        "start_download refuses expired manifest",
        raises(off.start_download, exc_type=ValueError,
               manifest_id=aged.id, user_id="stu-3"),
    )

    # Progress updates
    d_progress = off.update_progress(
        download_id=d.id, user_id="stu-1",
        bytes_downloaded=20_000, files_completed=1,
    )
    check(
        "Progress update increments byte/file counts",
        d_progress.bytes_downloaded == 20_000
        and d_progress.files_completed == 1,
    )

    # Permission check on progress
    check(
        "update_progress refuses non-owner",
        raises(off.update_progress, exc_type=PermissionError,
               download_id=d.id, user_id="stranger",
               bytes_downloaded=100, files_completed=0),
    )

    # Negative bytes rejected
    check(
        "update_progress rejects negative bytes",
        raises(off.update_progress, exc_type=ValueError,
               download_id=d.id, user_id="stu-1",
               bytes_downloaded=-1, files_completed=0),
    )

    # Auto-complete when all files done
    d_done = off.update_progress(
        download_id=d.id, user_id="stu-1",
        bytes_downloaded=m_text.total_bytes,
        files_completed=m_text.file_count,
    )
    check(
        "Progress reaching file_count auto-completes",
        d_done.status == "completed"
        and d_done.completed_at is not None,
    )

    # Can't update completed
    check(
        "update_progress refuses completed download",
        raises(off.update_progress, exc_type=ValueError,
               download_id=d.id, user_id="stu-1",
               bytes_downloaded=999, files_completed=999),
    )

    # Cancel another download
    d2 = off.start_download(
        manifest_id=m_std.id, user_id="stu-1",
    )
    check(
        "cancel_download True for in-progress",
        off.cancel_download(
            download_id=d2.id, user_id="stu-1",
        ),
    )
    check(
        "cancel_download False for already-cancelled",
        not off.cancel_download(
            download_id=d2.id, user_id="stu-1",
        ),
    )

    # List my downloads
    downloads = off.list_user_downloads("stu-1")
    check(
        "list_user_downloads returns user's downloads",
        len(downloads) >= 2,
    )

    # Filter by status
    completed = off.list_user_downloads(
        "stu-1", status="completed",
    )
    check(
        "list_user_downloads filter by status",
        all(d.status == "completed" for d in completed),
    )

    # Bad status filter
    check(
        "list_user_downloads rejects bad status",
        raises(off.list_user_downloads, exc_type=ValueError,
               user_id="stu-1", status="cosmic"),
    )

    # ============================================================
    # Daily usage
    # ============================================================
    usage = off.user_data_usage_today("stu-1")
    check(
        "user_data_usage_today returns bytes_today + mb_today",
        "bytes_today" in usage and "mb_today" in usage,
    )
    check(
        "user_data_usage_today reflects completed download",
        usage["bytes_today"] >= m_text.total_bytes,
    )

    # Set tight quota → quota_exceeded
    off.set_low_data_prefs(user_id="stu-1", max_daily_mb=1)
    usage_tight = off.user_data_usage_today("stu-1")
    check(
        "quota_exceeded fires when usage > max_daily_mb",
        usage_tight["quota_exceeded"],
    )

    # ============================================================
    # HTTP layer
    # ============================================================
    with TestClient(app) as c:
        # Auth gates
        r = c.get("/api/offline/prefs")
        check(
            "GET /api/offline/prefs → 401",
            r.status_code == 401,
        )

        r = c.post(
            "/api/offline/prefs",
            data={"quality_tier": "standard"},
        )
        check(
            "POST /api/offline/prefs → 401",
            r.status_code == 401,
        )

        r = c.post(
            "/api/offline/manifests",
            data={"title": "Auth Test",
                  "files_json": "[]"},
        )
        check(
            "POST /api/offline/manifests → 401",
            r.status_code == 401,
        )

        r = c.get("/api/offline/manifests/me")
        check(
            "GET /api/offline/manifests/me → 401",
            r.status_code == 401,
        )

        r = c.get("/api/offline/usage/today")
        check(
            "GET /api/offline/usage/today → 401",
            r.status_code == 401,
        )

        # Form validation
        r = c.post(
            "/api/offline/prefs",
            data={"max_daily_mb": 999_999},
        )
        check(
            "POST /api/offline/prefs rejects max_daily_mb > 100k (422)",
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
        "v3.12 brought route count above 540",
        len(app.routes) > 540,
        f"routes={len(app.routes)}",
    )

    print("--------------------------------------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
