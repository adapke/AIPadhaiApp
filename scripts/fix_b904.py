#!/usr/bin/env python3
"""AST-based B904 fixer — converts `raise X(...)` inside `except E as n:`
into `raise X(...) from n` (or `from None` when the except clause has no
binding).

Ruff doesn't autofix B904 even with `--unsafe-fixes` because it can't
safely determine the binding name from a text-level view. We walk the
AST instead: every `Raise` node inside an `ExceptHandler` that has
`.exc is not None and .cause is None` is a B904 site, and we know the
binding name from the enclosing handler.

Usage:
    python scripts/fix_b904.py path/to/file.py [path/to/another.py ...]
    python scripts/fix_b904.py --check path/to/file.py   # report only

Idempotent: re-running on a fixed file is a no-op (no remaining
ExceptHandler-nested Raises lack `.cause`).
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path


def find_b904_sites(tree: ast.AST) -> list[tuple[ast.Raise, str | None]]:
    """Walk the tree, return list of (raise_node, binding_name) for every
    B904 site. binding_name is the `as X` name (or None when the except
    clause didn't bind one)."""
    sites: list[tuple[ast.Raise, str | None]] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            # Stack of binding names from enclosing ExceptHandlers; None
            # means the handler caught without an `as` clause.
            self._stack: list[str | None] = []

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            self._stack.append(node.name)  # may be None
            self.generic_visit(node)
            self._stack.pop()

        def visit_Raise(self, node: ast.Raise) -> None:
            # B904 only fires inside an except handler
            if (
                self._stack
                and node.exc is not None
                and node.cause is None
            ):
                sites.append((node, self._stack[-1]))
            self.generic_visit(node)

    Visitor().visit(tree)
    return sites


def insert_from_clause_bytes(
    line_bytes: bytes, end_col_bytes: int, binding: str | None,
) -> bytes:
    """Insert ` from <binding>` (or ` from None`) at the given BYTE
    offset within the line. `ast` end_col_offset is a byte offset for
    UTF-8-encoded source — treating it as a character index breaks on
    lines that contain non-ASCII chars before the raise expression."""
    cause = binding if binding is not None else "None"
    insertion = f" from {cause}".encode()
    return line_bytes[:end_col_bytes] + insertion + line_bytes[end_col_bytes:]


def fix_file(path: Path, check_only: bool = False) -> int:
    """Fix every B904 site in `path`. Returns the count of sites
    processed (whether or not check_only)."""
    raw = path.read_bytes()
    try:
        tree = ast.parse(raw, filename=str(path))
    except SyntaxError as e:
        print(f"[skip] {path}: syntax error: {e}", file=sys.stderr)
        return 0

    sites = find_b904_sites(tree)
    if not sites:
        return 0

    if check_only:
        for node, binding in sites:
            print(
                f"{path}:{node.lineno}:{node.col_offset}: "
                f"B904 raise without `from` (binding: "
                f"{binding or '<none>'})"
            )
        return len(sites)

    # Work entirely in bytes so the AST byte-offsets stay correct on
    # lines containing non-ASCII characters (em-dashes, smart quotes,
    # Devanagari, etc).
    lines: list[bytes] = raw.splitlines(keepends=True)

    # Apply edits in reverse order so line offsets aren't perturbed.
    sites_sorted = sorted(
        sites,
        key=lambda s: (
            s[0].end_lineno or 0, s[0].end_col_offset or 0,
        ),
        reverse=True,
    )
    fixed = 0
    for node, binding in sites_sorted:
        end_line = node.end_lineno
        end_col = node.end_col_offset
        if end_line is None or end_col is None:
            continue
        # 1-based line; index into 0-based list
        lines[end_line - 1] = insert_from_clause_bytes(
            lines[end_line - 1], end_col, binding,
        )
        fixed += 1
    if fixed:
        path.write_bytes(b"".join(lines))
    return fixed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "files", nargs="+", help="Python files to fix",
    )
    ap.add_argument(
        "--check", action="store_true",
        help="Report B904 sites without modifying any file",
    )
    args = ap.parse_args()

    total = 0
    for fp in args.files:
        path = Path(fp)
        if not path.is_file():
            print(f"[skip] {path}: not a file", file=sys.stderr)
            continue
        n = fix_file(path, check_only=args.check)
        verb = "would-fix" if args.check else "fixed"
        if n:
            print(f"[{verb}] {path}: {n} site(s)")
        total += n

    print(f"--- total {'would-fix' if args.check else 'fixed'}: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
