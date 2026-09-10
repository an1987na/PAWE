#!/bin/sh
# Run from this checkout with the same protected Compose environment as deployment.
set -eu
umask 077
compose_file="${PAWE_COMPOSE_FILE:-deploy/compose.yaml}"
case "$compose_file" in deploy/compose.yaml|deploy/compose.tunnel.yaml) ;; *) exit 1;; esac
: "${PAWE_BACKUP_DIR:?Set an absolute backup directory on a verified backup volume}"
case "$PAWE_BACKUP_DIR" in /*) ;; *) printf '%s\n' 'Backup directory must be absolute' >&2; exit 1;; esac
test -d "$PAWE_BACKUP_DIR"
backup_file="$PAWE_BACKUP_DIR/pawe-$(date -u +%Y%m%dT%H%M%SZ).dump"
# noclobber prevents accidental overwrite; failed partial files remain .partial for inspection.
set -C
docker compose -f "$compose_file" exec -T postgres pg_dump -U pawe -d pawe -Fc > "$backup_file.partial"
docker compose -f "$compose_file" exec -T postgres pg_restore --list < "$backup_file.partial" >/dev/null
test ! -e "$backup_file"
mv "$backup_file.partial" "$backup_file"
printf 'Backup created: %s\n' "$backup_file"
