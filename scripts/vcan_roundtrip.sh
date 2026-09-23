#!/usr/bin/env bash
set -euo pipefail

# Replays a generated capture onto a Linux virtual CAN interface, records it back with candump,
# and checks that nothing was lost and that the analyzer reaches the same findings.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

INTERFACE="${1:-vcan0}"
SCENARIO="${2:-fault_session}"
SOURCE="data/synthetic/${SCENARIO}.log"
WORKDIR="$(mktemp -d)"
RECORDED="${WORKDIR}/${SCENARIO}_vcan.log"
trap 'rm -rf "${WORKDIR}"' EXIT

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "this check needs Linux SocketCAN; this host reports $(uname -s)" >&2
    exit 1
fi
if ! command -v candump >/dev/null 2>&1; then
    echo "candump not found; install can-utils" >&2
    exit 1
fi
if [[ ! -f "${SOURCE}" ]]; then
    echo "missing ${SOURCE}; run python scripts/regenerate.py first" >&2
    exit 1
fi

sudo modprobe vcan
if ! ip link show "${INTERFACE}" >/dev/null 2>&1; then
    sudo ip link add dev "${INTERFACE}" type vcan
fi
sudo ip link set up "${INTERFACE}"
ip -details link show "${INTERFACE}"

candump -L "${INTERFACE}" > "${RECORDED}" &
CANDUMP_PID=$!
sleep 1

can-diag replay --input "${SOURCE}" --channel "${INTERFACE}"

sleep 1
kill "${CANDUMP_PID}"
wait "${CANDUMP_PID}" 2>/dev/null || true

echo
echo "recorded $(wc -l < "${RECORDED}") lines from ${INTERFACE} with candump"

can-diag verify-replay \
    --dbc dbc/hobby_network.dbc \
    --original "${SOURCE}" \
    --replayed "${RECORDED}" \
    --json-output "${WORKDIR}/verification.json"

can-diag report \
    --dbc dbc/hobby_network.dbc \
    --input "${RECORDED}" \
    --json-output "${WORKDIR}/report.json" \
    --markdown-output "${WORKDIR}/report.md"

python scripts/check_findings.py --report "${WORKDIR}/report.json" --scenario "${SCENARIO}"

echo "vcan round trip passed on ${INTERFACE}"
