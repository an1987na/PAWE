#!/bin/sh
# Run from this checkout with the same protected Compose environment as deployment.
set -eu
umask 077
: "${PAWE_BACKUP_DIR:?Set an absolute backup directory on a verified backup volume}"
case "$PAWE_BACKUP_DIR" in /*) ;; *) printf '%s\n' 'Backup directory must be absolute' >&2; exit 1;; esac
test -d "$PAWE_BACKUP_DIR"
backup_file="$PAWE_BACKUP_DIR/pawe-$(date -u +%Y%m%dT%H%M%SZ).dump"
# noclobber prevents accidental overwrite; failed partial files remain .partial for inspection.
set -C
docker compose -f deploy/compose.yaml exec -T postgres pg_dump -U pawe -d pawe -Fc > "$backup_file.partial"
docker compose -f deploy/compose.yaml exec -T postgres pg_restore --list < "$backup_file.partial" >/dev/null
test ! -e "$backup_file"
mv "$backup_file.partial" "$backup_file"
printf 'Backup created: %s\n' "$backup_file"
