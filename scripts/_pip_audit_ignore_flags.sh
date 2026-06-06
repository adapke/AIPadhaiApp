#!/usr/bin/env bash
# prod-7 helper — reads .pip-audit-ignore and emits the
# `--ignore-vuln <id>` flags that pip-audit understands.
# Lines beginning with `#` or blank are skipped.
#
# Used by both the Makefile audit target and the CI workflow so they
# can't disagree about which CVEs are suppressed.

set -euo pipefail

IGNORE_FILE="$(dirname "$0")/../.pip-audit-ignore"
[ -f "$IGNORE_FILE" ] || exit 0

grep -vE '^\s*(#|$)' "$IGNORE_FILE" \
    | awk '{print "--ignore-vuln " $1}' \
    | tr '\n' ' '
