#!/usr/bin/env python3
"""CI guard: every router file in `padhai/routers/` is registered in
`_ROUTER_NAMES`, and every name in `_ROUTER_NAMES` has a real file.

Closes the bug class where someone adds a router file but forgets to
wire it into the package registry — the endpoints silently don't
mount and the SPA gets 404s for the new surface. The mirror bug
(name in `_ROUTER_NAMES` but the file was removed) crashes app boot
because `all_routers()` does `__import__(name)` for each.

The router unit tests in `tests/test_routers.py` already catch the
half-extracted-slice case (a representative URL missing from
`app.routes`), but that's a behavioural test. This script is a
structural check — fast, runs without booting FastAPI, and gives a
diagnostic that points directly at the typo.

Allowlist: `__init__.py` and `__pycache__` are skipped explicitly.

Wired into `make verify`. Standalone:
    python scripts/check_router_registry.py
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    routers_dir = repo_root / "padhai" / "routers"
    init_py = routers_dir / "__init__.py"

    if not init_py.is_file():
        print(
            f"[check_router_registry] FAIL — {init_py} is missing.",
            file=sys.stderr,
        )
        return 1

    # 1. Files on disk (one per slice; the package `__init__.py` is
    #    metadata, not a slice).
    on_disk: set[str] = set()
    for p in routers_dir.iterdir():
        if p.is_file() and p.suffix == ".py" and p.name != "__init__.py":
            on_disk.add(p.stem)

    # 2. Names registered in `_ROUTER_NAMES`. We parse the tuple by
    #    importing the module — that's faster than regexing the
    #    source and tolerates whatever formatting the maintainer
    #    chose. The package import is lightweight (no router files
    #    are eagerly loaded — `all_routers()` is a generator).
    sys.path.insert(0, str(repo_root))
    try:
        from padhai.routers import _ROUTER_NAMES
    except Exception as e:  # pragma: no cover — broken init is its own error
        print(
            f"[check_router_registry] FAIL — could not import "
            f"padhai.routers._ROUTER_NAMES: {e}",
            file=sys.stderr,
        )
        return 1

    registered = set(_ROUTER_NAMES)

    missing_from_registry = sorted(on_disk - registered)
    missing_from_disk = sorted(registered - on_disk)

    if not missing_from_registry and not missing_from_disk:
        print(
            f"[check_router_registry] OK — {len(on_disk)} router file(s) "
            f"all registered in _ROUTER_NAMES.",
        )
        return 0

    if missing_from_registry:
        print(
            "[check_router_registry] FAIL — router file(s) on disk but "
            "NOT registered in _ROUTER_NAMES (add the name to the "
            "tuple in padhai/routers/__init__.py):",
            file=sys.stderr,
        )
        for name in missing_from_registry:
            print(f"  + padhai/routers/{name}.py", file=sys.stderr)

    if missing_from_disk:
        print(
            "[check_router_registry] FAIL — name(s) in _ROUTER_NAMES "
            "but the corresponding .py file is MISSING (either "
            "create the file or remove the name from the tuple):",
            file=sys.stderr,
        )
        for name in missing_from_disk:
            print(f"  - padhai/routers/{name}.py", file=sys.stderr)

    return 1


if __name__ == "__main__":
    sys.exit(main())
