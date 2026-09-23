from __future__ import annotations

import struct
from collections.abc import Sequence
from pathlib import Path

import pytest

from can_diag.config import AnalyzerConfig
from can_diag.decoder import NetworkDatabase
from can_diag.models import CanFrame

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DBC_PATH = REPOSITORY_ROOT / "dbc" / "hobby_network.dbc"
SYNTHETIC_DIR = REPOSITORY_ROOT / "data" / "synthetic"
COMPONENT_DIR = REPOSITORY_ROOT / "firmware" / "components" / "can_protocol"
FIRMWARE_HEADER = COMPONENT_DIR / "include" / "can_protocol.h"

POWER_DATA_ID = 0x180
POWER_HEARTBEAT_ID = 0x181
THERMAL_DATA_ID = 0x280
THERMAL_HEARTBEAT_ID = 0x281

GOLDEN_THERMAL_PAYLOAD = "FA00D80401070000"


@pytest.fixture(scope="session")
def repository_root() -> Path:
    return REPOSITORY_ROOT


@pytest.fixture(scope="session")
def dbc_path() -> Path:
    return DBC_PATH


@pytest.fixture(scope="session")
def database() -> NetworkDatabase:
    return NetworkDatabase.load(DBC_PATH)


@pytest.fixture()
def config() -> AnalyzerConfig:
    return AnalyzerConfig()


def data_payload(
    temperature_deci_degc: int,
    voltage_centivolts: int,
    node_state: int,
    counter: int,
    fault_code: int = 0,
) -> bytes:
    return struct.pack(
        "<hHBBBB", temperature_deci_degc, voltage_centivolts, node_state, counter, fault_code, 0
    )


def heartbeat_payload(uptime_seconds: int, node_state: int, reset_reason: int) -> bytes:
    return struct.pack("<IBBH", uptime_seconds, node_state, reset_reason, 0)


def frame(timestamp: float, arbitration_id: int, payload: bytes) -> CanFrame:
    return CanFrame(
        timestamp=timestamp, arbitration_id=arbitration_id, data=payload, channel="vcan0"
    )


def data_frame(
    timestamp: float,
    arbitration_id: int,
    counter: int,
    temperature_deci_degc: int = 250,
    voltage_centivolts: int = 1240,
    node_state: int = 1,
    fault_code: int = 0,
) -> CanFrame:
    return frame(
        timestamp,
        arbitration_id,
        data_payload(temperature_deci_degc, voltage_centivolts, node_state, counter, fault_code),
    )


def heartbeat_frame(
    timestamp: float,
    arbitration_id: int,
    uptime_seconds: int,
    node_state: int = 1,
    reset_reason: int = 2,
) -> CanFrame:
    return frame(
        timestamp, arbitration_id, heartbeat_payload(uptime_seconds, node_state, reset_reason)
    )


def healthy_stream(
    duration_seconds: float, start: float = 0.0, step: float = 0.1
) -> list[CanFrame]:
    frames: list[CanFrame] = []
    ticks = int(round(duration_seconds / step))
    for index in range(ticks):
        moment = start + index * step
        frames.append(data_frame(moment, POWER_DATA_ID, index % 256))
        frames.append(data_frame(moment + 0.02, THERMAL_DATA_ID, index % 256))
        if index % 10 == 0:
            second = int(index * step)
            frames.append(heartbeat_frame(moment + 0.05, POWER_HEARTBEAT_ID, second))
            frames.append(heartbeat_frame(moment + 0.06, THERMAL_HEARTBEAT_ID, second))
    return frames


def kinds(events: Sequence[object]) -> list[str]:
    return [event.kind.value for event in events]
