#!/bin/sh
# Run from the release root with the protected deployment environment exported.
set -eu
test "${PAWE_DATA_DIR:?}" = /mnt/media/projects/PAWE/data
awk '$2 == "/mnt/media" && $3 == "ext4" {ok=1} END {exit !ok}' /proc/mounts
test -d "$PAWE_DATA_DIR/postgres"
test -d "$PAWE_DATA_DIR/artifacts"
test -f "${PAWE_ENV_FILE:?}"
for image in "${PAWE_POSTGRES_IMAGE:?}" "${PAWE_API_IMAGE:?}" "${PAWE_WORKER_IMAGE:?}" "${PAWE_WEB_IMAGE:?}"; do
    printf '%s\n' "$image" | grep -Eq '^sha256:[a-f0-9]{64}$' || exit 1
    test "$(docker image inspect --format '{{.Architecture}}' "$image")" = amd64
done
docker compose -f deploy/compose.tunnel.yaml config --quiet
docker compose -f deploy/compose.tunnel.yaml up -d
