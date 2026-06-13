#!/usr/bin/env bash
# prod-91 — Nightly ops entrypoint for AI Pathshala.
#
# Runs the three things ops needs to happen every night:
#   1. SQLite online backup (prod-69)
#   2. Iframe-health check on verified concept videos (prod-82),
#      with optional auto-demotion of broken rows
#   3. Curator-workflow stats snapshot (prod-78)
#
# Each step is run independently. A step's failure is logged but
# does NOT abort the others — they're independent concerns. The
# script exits non-zero if ANY step failed, so cron can email or
# alert.
#
# Cron template (paste into crontab -e):
#   23 3 * * * cd /opt/padhai && \
#       PADHAI_DB_PATH=/var/lib/padhai/jobs.db \
#       AUTO_DEMOTE=1 \
#       /opt/padhai/scripts/nightly_ops.sh \
#       >> /var/log/padhai-nightly.log 2>&1
#
# Environment knobs:
#   PADHAI_DB_PATH           sqlite path (default ~/.padhai/jobs.db)
#   PADHAI_BACKUP_DIR        backup destination (default ~/.padhai/backups)
#   PADHAI_BACKUP_KEEP_DAYS  retention (default 14)
#   AUTO_DEMOTE              if "1", demote iframe-broken verified rows
#                            back to channel_seed
#   STATS_DAYS               window for stats output (default 30)
#   SKIP_BACKUP              if "1", skip step 1
#   SKIP_IFRAME              if "1", skip step 2
#   SKIP_STATS               if "1", skip step 3

set -u  # NOT set -e — we want all steps to attempt even if earlier fails

cd "$(dirname "$0")/.."
ROOT="$PWD"

# ANSI colors only if writing to a TTY; cron logs get plain text.
if [ -t 1 ]; then
    R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[34m'; N=$'\033[0m'
else
    R=; G=; Y=; B=; N=
fi

started_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
echo "${B}[nightly_ops]${N} starting at $started_at"
echo "${B}[nightly_ops]${N} repo=$ROOT db=${PADHAI_DB_PATH:-$HOME/.padhai/jobs.db}"

backup_rc=0
iframe_rc=0
stats_rc=0

# ----- 1. SQLite backup -----
if [ "${SKIP_BACKUP:-0}" = "1" ]; then
    echo "${Y}[nightly_ops]${N} step 1: SKIPPED (SKIP_BACKUP=1)"
else
    echo "${B}[nightly_ops]${N} step 1: backup"
    if [ -x scripts/backup_sqlite.sh ]; then
        ./scripts/backup_sqlite.sh
        backup_rc=$?
    else
        echo "${Y}[nightly_ops]${N} backup_sqlite.sh not executable; chmod +x and retrying"
        chmod +x scripts/backup_sqlite.sh 2>/dev/null
        bash scripts/backup_sqlite.sh
        backup_rc=$?
    fi
fi

# ----- 2. Iframe-health check -----
if [ "${SKIP_IFRAME:-0}" = "1" ]; then
    echo "${Y}[nightly_ops]${N} step 2: SKIPPED (SKIP_IFRAME=1)"
else
    echo "${B}[nightly_ops]${N} step 2: iframe-check"
    iframe_args=""
    if [ "${AUTO_DEMOTE:-0}" = "1" ]; then
        iframe_args="--auto-demote"
    fi
    # Discard JSON stdout (logged separately via the script's stderr summary).
    PYTHONPATH=. python scripts/check_verified_iframes.py $iframe_args >/dev/null
    iframe_rc=$?
    # iframe-check exits 1 when any verified row is now blocked — that's
    # important signal for cron, but it's not a "this script failed"
    # error. We treat it as success at the wrapper level UNLESS the
    # caller is in strict mode (STRICT_IFRAME=1).
    if [ "${STRICT_IFRAME:-0}" != "1" ] && [ "$iframe_rc" = "1" ]; then
        echo "${Y}[nightly_ops]${N} iframe-check found blocked rows (rc=1); not failing wrapper"
        iframe_rc=0
    fi
fi

# ----- 3. Curator stats snapshot -----
if [ "${SKIP_STATS:-0}" = "1" ]; then
    echo "${Y}[nightly_ops]${N} step 3: SKIPPED (SKIP_STATS=1)"
else
    echo "${B}[nightly_ops]${N} step 3: stats"
    PYTHONPATH=. python scripts/print_curator_stats.py \
        --days "${STATS_DAYS:-30}" --pretty
    stats_rc=$?
fi

# ----- summary -----
finished_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
total_rc=$(( backup_rc + iframe_rc + stats_rc ))
if [ "$total_rc" = "0" ]; then
    echo "${G}[nightly_ops]${N} all ok | started=$started_at finished=$finished_at"
else
    echo "${R}[nightly_ops]${N} FAILURES | backup=$backup_rc iframe=$iframe_rc stats=$stats_rc"
fi

exit $total_rc
