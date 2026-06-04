#!/usr/bin/env bash
# Online SQLite backup for single-server deploys. Designed to be
# safe under concurrent writes — uses sqlite3's `.backup` API which
# acquires a shared lock rather than copying mid-write.
#
# Usage:
#   ./scripts/backup_sqlite.sh                       # use PADHAI_DB_PATH or default
#   ./scripts/backup_sqlite.sh /custom/path.db       # back up that file specifically
#   PADHAI_DB_PATH=/var/lib/padhai/jobs.db ./scripts/backup_sqlite.sh
#
# Cron template (paste into `crontab -e`):
#   17 * * * * /opt/padhai/scripts/backup_sqlite.sh >> /var/log/padhai-backup.log 2>&1
#
# What it does:
#   1. Resolve source DB path (arg > $PADHAI_DB_PATH > ~/.padhai/jobs.db)
#   2. Sanity-check the source exists + is openable by sqlite3
#   3. Run `.backup` into a timestamped copy under
#      $PADHAI_BACKUP_DIR (default ~/.padhai/backups/)
#   4. gzip -9 the copy
#   5. Prune anything older than $PADHAI_BACKUP_KEEP_DAYS (default 14)
#
# Exits non-zero on any step failing so a cron job mail-trap sees
# the error.

set -euo pipefail

SRC="${1:-${PADHAI_DB_PATH:-$HOME/.padhai/jobs.db}}"
BACKUP_DIR="${PADHAI_BACKUP_DIR:-$HOME/.padhai/backups}"
KEEP_DAYS="${PADHAI_BACKUP_KEEP_DAYS:-14}"

log() { echo "[backup_sqlite] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

# 1 + 2 — verify the source exists + is a real SQLite DB
if [ ! -f "$SRC" ]; then
    log "FATAL: source DB not found at $SRC"
    exit 1
fi
if ! sqlite3 "$SRC" "SELECT 1" > /dev/null 2>&1; then
    log "FATAL: cannot open $SRC with sqlite3 — corrupt or locked"
    exit 2
fi

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
TARGET="$BACKUP_DIR/$(basename "$SRC" .db)_${STAMP}.db"

# 3 — online backup. Uses the C API's sqlite3_backup_step which
# yields to writers; no application-level downtime.
log "backing up $SRC -> $TARGET"
sqlite3 "$SRC" ".backup '$TARGET'"

# 4 — compress. -9 is slow but disk space + bandwidth matter more
# than CPU during the off-hours window most cron jobs run in.
log "compressing"
gzip -9 "$TARGET"
TARGET_GZ="${TARGET}.gz"
log "wrote $TARGET_GZ ($(du -h "$TARGET_GZ" | cut -f1))"

# 5 — prune. -mtime +N deletes files older than N days; +0 deletes
# everything older than today's UTC midnight (sufficient for daily
# snapshots).
if [ "$KEEP_DAYS" -gt 0 ]; then
    PRUNED=$(find "$BACKUP_DIR" -name '*.db.gz' -mtime +"$KEEP_DAYS" -print -delete | wc -l)
    log "pruned $PRUNED snapshots older than $KEEP_DAYS days"
fi

log "OK"
