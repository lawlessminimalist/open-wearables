#!/usr/bin/env bash
# Daily PostgreSQL dump with 3-backup rotation.
# Dumps the open-wearables database from the running Podman container,
# keeps the 3 most recent dumps, and removes anything older.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/Users/dhlaw/Documents/source/open-wearables/backups}"
CONTAINER="postgres__open-wearables"
DB_NAME="open-wearables"
DB_USER="open-wearables"
KEEP=3

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DUMP_FILE="$BACKUP_DIR/open-wearables_${TIMESTAMP}.sql.gz"

echo "[$(date)] Starting dump → $DUMP_FILE"

# Stream pg_dump through gzip directly to the host
podman exec "$CONTAINER" \
  pg_dump -U "$DB_USER" -d "$DB_NAME" --no-password \
  | gzip > "$DUMP_FILE"

SIZE=$(du -sh "$DUMP_FILE" | cut -f1)
echo "[$(date)] Dump complete — $SIZE"

# Rotate: delete all but the $KEEP most recent dumps
DUMPS=( $(ls -1t "$BACKUP_DIR"/open-wearables_*.sql.gz 2>/dev/null) )
EXCESS=$(( ${#DUMPS[@]} - KEEP ))

if (( EXCESS > 0 )); then
  for OLD in "${DUMPS[@]: -$EXCESS}"; do
    echo "[$(date)] Removing old backup: $(basename "$OLD")"
    rm -f "$OLD"
  done
fi

echo "[$(date)] Done. Backups kept:"
ls -1 "$BACKUP_DIR"/open-wearables_*.sql.gz | while read f; do
  echo "  $(basename $f)  ($(du -sh $f | cut -f1))"
done
