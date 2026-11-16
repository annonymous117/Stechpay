#!/usr/bin/env bash
# Environment supplied by the systemd unit or explicitly sourced by the operator.
set -euo pipefail
BACKUP_DIR=${STECHPAY_BACKUP_DIR:?Set the private backup directory}
test -d "$BACKUP_DIR" && test -w "$BACKUP_DIR"
umask 077
backup_file="$BACKUP_DIR/stechpay-scheduled-$(date -u +%Y%m%dT%H%M%SZ).dump"
export PGPASSWORD=${POSTGRES_PASSWORD:?Database password is required}
pg_dump --host "${POSTGRES_HOST:-localhost}" --port "${POSTGRES_PORT:-5432}" \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format=custom \
    --file "$backup_file.partial"
pg_restore --list "$backup_file.partial" > /dev/null
mv "$backup_file.partial" "$backup_file"
echo "PostgreSQL backup completed: $backup_file"
