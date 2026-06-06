#!/usr/bin/env python3
"""Endpoint tier audit — classifies every route in padhai.web:app
by who can use it (public, anyone signed-in, paid tier, admin-only).

Why this exists:
  Pricing, marketing, and revenue-protection all depend on knowing
  which endpoint is free and which requires payment. The codebase
  has a `_require_tier()` helper in web.py and a `require_admin_role()`
  in api_deps.py, but no canonical inventory of which routes call
  which gate. A bug that accidentally removes `_require_tier(...)`
  on a paid surface is a revenue leak with no automated catch.

  This script introspects the running app's route table, AST-inspects
  each handler's body, and emits both:
    * `data/endpoint_tier_map.json`  (machine-readable; for
      regression tests and pricing-page generators)
    * `docs/ENDPOINT_TIER_MAP.md`    (human-readable; for product
      and ops to review)

Classification (priority order — first match wins):
  ADMIN_ONLY       handler calls `require_admin_role(user)`
  TIER_GATED       handler calls `_require_tier(user, "Mx")`
  AUTH_REQUIRED    handler raises 401/403 when user is None
  ANONYMOUS_OK     handler accepts user=None silently
  PUBLIC           handler has no `current_user` dependency at all

Usage:
  python scripts/audit_endpoint_tiers.py            # human-readable
  python scripts/audit_endpoint_tiers.py --json     # JSON to stdout
  python scripts/audit_endpoint_tiers.py --write    # write both files

Exit code: always 0. This is a measurement script, not a gate.
"""
from __future__ import annotations

import argparse
import ast
import inspect
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Boot the app — pre-emptively populate env so module-level checks
# don't fail. Doesn't need a real Anthropic key for route inspection.
os.environ.setdefault("PADHAI_SKIP_DOTENV", "1")
os.environ.setdefault(
    "PADHAI_JWT_SECRET",
    "audit-only-not-for-prod-abcdef0123456789abcdef0123456789",
)


def _rel_path(p: str | None) -> str | None:
    """Best-effort: return path relative to repo root with forward
    slashes for cross-platform reproducibility."""
    if not p:
        return None
    try:
        return Path(p).resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return p


def _classify_endpoint(endpoint) -> dict:
    """Return {tier_class, min_tier, source_file, lineno} for one handler."""
    try:
        src = inspect.getsource(endpoint)
        src_file = inspect.getsourcefile(endpoint)
        _, lineno = inspect.getsourcelines(endpoint)
    except (OSError, TypeError):
        return {
            "tier_class": "UNKNOWN",
            "min_tier": None,
            "source_file": None,
            "lineno": None,
            "reason": "source unavailable",
        }

    # Dependency-signature check: does the handler accept `current_user`?
    sig = inspect.signature(endpoint)
    has_user_param = any(
        p.name in ("user", "current_user", "auth_user")
        for p in sig.parameters.values()
    )

    # AST-walk the handler body for the four marker calls.
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {
            "tier_class": "UNKNOWN",
            "min_tier": None,
            "source_file": _rel_path(src_file),
            "lineno": lineno,
            "reason": "unparseable source",
        }

    admin_only = False
    tier_min: str | None = None
    raises_401_on_none = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = (
                fn.attr if isinstance(fn, ast.Attribute) else
                fn.id if isinstance(fn, ast.Name) else None
            )
            if name == "require_admin_role":
                admin_only = True
            elif name == "_require_tier" and len(node.args) >= 2:
                tier_arg = node.args[1]
                if isinstance(tier_arg, ast.Constant) and isinstance(
                    tier_arg.value, str,
                ):
                    tier_min = tier_arg.value
        if isinstance(node, ast.If):
            # Look for the pattern: `if user is None: raise HTTPException(401, ...)`
            test = node.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id in ("user", "current_user")
                and any(isinstance(c, ast.Constant) and c.value is None
                        for c in test.comparators)
            ):
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Raise)
                        and isinstance(sub.exc, ast.Call)
                    ):
                        raises_401_on_none = True

    if admin_only:
        cls, mt = "ADMIN_ONLY", None
    elif tier_min:
        cls, mt = "TIER_GATED", tier_min
    elif raises_401_on_none and has_user_param:
        cls, mt = "AUTH_REQUIRED", None
    elif has_user_param:
        cls, mt = "ANONYMOUS_OK", None
    else:
        cls, mt = "PUBLIC", None

    return {
        "tier_class": cls,
        "min_tier": mt,
        "source_file": _rel_path(src_file),
        "lineno": lineno,
    }


_SKIP_PATH_RE = re.compile(r"^/(?:openapi\.json|docs|redoc)(?:/|$)")


def audit() -> dict:
    """Boot the app and classify every route. Returns a dict shaped
    for both JSON output and Markdown rendering."""
    from padhai.web import app

    routes = []
    for r in app.routes:
        path = getattr(r, "path", None)
        methods = sorted(getattr(r, "methods", None) or set())
        endpoint = getattr(r, "endpoint", None)
        if not path or not endpoint or not methods:
            continue
        if _SKIP_PATH_RE.match(path):
            continue
        meta = _classify_endpoint(endpoint)
        routes.append({
            "path": path,
            "methods": [m for m in methods if m != "HEAD"],
            "name": getattr(r, "name", None) or endpoint.__name__,
            **meta,
        })

    routes.sort(key=lambda x: (x["path"], ",".join(x["methods"])))

    counts: dict[str, int] = {}
    for r in routes:
        counts[r["tier_class"]] = counts.get(r["tier_class"], 0) + 1

    return {"routes": routes, "counts": counts, "total": len(routes)}


def render_markdown(data: dict) -> str:
    """Render the audit as a checked-in Markdown reference."""
    counts = data["counts"]
    total = data["total"]
    lines: list[str] = []
    lines.append("# Endpoint tier map\n")
    lines.append(
        "_Generated by `python scripts/audit_endpoint_tiers.py --write`. "
        "Do not edit by hand — re-run after changing any route._\n",
    )
    lines.append(f"**Total endpoints: {total}**\n")
    lines.append("## Counts by tier class\n")
    lines.append("| Class | Count | % | Meaning |")
    lines.append("|---|--:|--:|---|")
    meanings = {
        "PUBLIC": "no auth dependency — anyone can hit it",
        "ANONYMOUS_OK": "auth optional; works when signed out",
        "AUTH_REQUIRED": "any signed-in user (FREE tier OK)",
        "TIER_GATED": "subscription tier ≥ N required (PAID)",
        "ADMIN_ONLY": "org admin or superuser only",
        "UNKNOWN": "could not classify (source unavailable / parse error)",
    }
    order = ["PUBLIC", "ANONYMOUS_OK", "AUTH_REQUIRED", "TIER_GATED",
            "ADMIN_ONLY", "UNKNOWN"]
    for cls in order:
        n = counts.get(cls, 0)
        pct = round(100 * n / total, 1) if total else 0.0
        lines.append(f"| **{cls}** | {n} | {pct}% | {meanings[cls]} |")
    lines.append("")

    # Per-class detail tables — useful for spot-checking and for
    # answering "what does the free tier get you?"
    for cls in order:
        rows = [r for r in data["routes"] if r["tier_class"] == cls]
        if not rows:
            continue
        lines.append(f"## {cls} ({len(rows)} endpoints)\n")
        lines.append("| Method(s) | Path | Min tier | Handler |")
        lines.append("|---|---|---|---|")
        for r in rows:
            methods = ",".join(r["methods"])
            mt = r.get("min_tier") or "—"
            src = r.get("source_file") or "?"
            ln = r.get("lineno") or "?"
            lines.append(
                f"| `{methods}` | `{r['path']}` | {mt} | "
                f"`{src}:{ln}` |",
            )
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true",
                    help="emit JSON to stdout")
    ap.add_argument("--write", action="store_true",
                    help="write data/endpoint_tier_map.json + "
                         "docs/ENDPOINT_TIER_MAP.md")
    args = ap.parse_args()

    data = audit()

    if args.write:
        json_path = ROOT / "data" / "endpoint_tier_map.json"
        md_path = ROOT / "docs" / "ENDPOINT_TIER_MAP.md"
        json_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        md_path.write_text(render_markdown(data), encoding="utf-8")
        print(f"wrote {json_path.relative_to(ROOT)}")
        print(f"wrote {md_path.relative_to(ROOT)}")
        return 0

    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0

    # Human-readable summary
    print(f"=== endpoint tier audit — {data['total']} routes ===")
    print()
    print(f"{'tier class':<18} {'count':>6}  {'%':>5}")
    print("-" * 34)
    for cls, n in sorted(data["counts"].items(), key=lambda x: -x[1]):
        pct = round(100 * n / data["total"], 1)
        print(f"{cls:<18} {n:>6}  {pct:>5}%")
    print()
    tier_gated = [r for r in data["routes"] if r["tier_class"] == "TIER_GATED"]
    if tier_gated:
        print(f"=== TIER_GATED detail ({len(tier_gated)} endpoints) ===")
        for r in tier_gated:
            methods = ",".join(r["methods"])
            print(f"  {methods:<10} {r['path']:<60} min={r['min_tier']}")
    else:
        print("=== TIER_GATED detail: NONE ===")
        print("    No endpoint calls _require_tier() yet. Every paid")
        print("    feature today is effectively free for any signed-in")
        print("    user. This is the gap prod-8 surfaces.")
    print()
    print("Run with --write to update data/endpoint_tier_map.json +")
    print("docs/ENDPOINT_TIER_MAP.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
