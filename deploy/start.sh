#!/bin/sh
# Start the passive stack only. Scheduler cutover is a separate audited action.
set -eu
: "${PAWE_STORAGE_MOUNT:?Set the verified storage mount point}"
: "${PAWE_DATA_DIR:?Set the application data directory under that mount}"
case "$PAWE_STORAGE_MOUNT" in /|/boot|/rom|/overlay|/tmp|/dev|*[!a-zA-Z0-9_/-]*)
    printf '%s\n' 'Refusing existing system/temporary mount or unsafe mount path' >&2; exit 1;;
esac
case "$PAWE_STORAGE_MOUNT" in /*) ;; *) exit 1;; esac
case "$PAWE_DATA_DIR" in "$PAWE_STORAGE_MOUNT"/*) ;; *) exit 1;; esac
case "$PAWE_DATA_DIR" in *..*|*[!a-zA-Z0-9_/-]*) exit 1;; esac
# A missing mount must not silently redirect writes onto the router overlay.
awk -v target="$PAWE_STORAGE_MOUNT" '$2 == target && $3 == "ext4" {ok=1} END {exit !ok}' /proc/mounts
test -d "$PAWE_DATA_DIR/postgres"
test -d "$PAWE_DATA_DIR/artifacts"
for image in "${PAWE_API_IMAGE:?}" "${PAWE_WORKER_IMAGE:?}" "${PAWE_WEB_IMAGE:?}"; do
    case "$image" in *@sha256:*) ;; *) printf '%s\n' 'Images must be pinned by digest' >&2; exit 1;; esac
done
docker compose -f deploy/compose.yaml config --quiet
docker compose -f deploy/compose.yaml up -d
