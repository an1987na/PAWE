#!/bin/sh
# Read-only host inventory. No packages, partitions, firewall or services are changed.
set -eu
uname -srm
if [ -r /etc/openwrt_release ]; then
    sed -n '/^DISTRIB_DESCRIPTION=/p' /etc/openwrt_release
fi
if [ -r /proc/meminfo ]; then
    awk '/^(MemTotal|MemAvailable|SwapTotal):/ {print}' /proc/meminfo
fi
df -h /
if command -v lsblk >/dev/null 2>&1; then
    lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT
fi
if command -v docker >/dev/null 2>&1; then
    docker version --format '{{.Server.Version}}'
    docker info --format 'root={{.DockerRootDir}} driver={{.Driver}} cpus={{.NCPU}} memory={{.MemTotal}}'
    docker compose version
else
    printf '%s\n' 'DOCKER_NOT_INSTALLED'
fi
printf '%s\n' 'Preflight only; memory, storage and gateway isolation require review before deployment.'
