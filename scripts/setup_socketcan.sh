#!/usr/bin/env bash
set -euo pipefail

INTERFACE="${1:-can0}"
BITRATE="${2:-500000}"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "SocketCAN requires Linux; this host reports $(uname -s)" >&2
    exit 1
fi

sudo ip link set "${INTERFACE}" down || true
sudo ip link set "${INTERFACE}" type can bitrate "${BITRATE}" restart-ms 100
sudo ip link set "${INTERFACE}" up

ip -details -statistics link show "${INTERFACE}"
echo "ready: ${INTERFACE} at ${BITRATE} bit/s"
