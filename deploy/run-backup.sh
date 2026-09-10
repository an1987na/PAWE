#!/bin/sh
# Cron entry point for the OpenWrt deployment; contains no credentials.
set -eu
awk '$2 == "/mnt/media" && $3 == "ext4" {ok=1} END {exit !ok}' /proc/mounts
test "$(ls -ld /mnt/media/projects/PAWE/config/runtime.env | awk '{print $1}')" = '-rw-------'
set -a
. /mnt/media/projects/PAWE/config/runtime.env
set +a
cd /mnt/media/projects/PAWE/deployment
export PAWE_COMPOSE_FILE=deploy/compose.tunnel.yaml
sh deploy/backup.sh
