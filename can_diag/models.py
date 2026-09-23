from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any


class EventKind(StrEnum):
    RANGE_VIOLATION = "range_violation"
    RANGE_RECOVERED = "range_recovered"
    DATA_MESSAGE_STALE = "data_message_stale"
    HEARTBEAT_TIMEOUT = "heartbeat_timeout"
    MESSAGE_RECOVERED = "message_recovered"
    NODE_SILENT = "node_silent"
    NODE_RECOVERED = "node_recovered"
    NODE_RESTART = "node_restart"
    SEQUENCE_GAP = "sequence_gap"
    UNKNOWN_MESSAGE_ID = "unknown_message_id"


class NodeState(IntEnum):
    BOOTING = 0
    NORMAL = 1
    DEGRADED = 2
    FAULT = 3


class ResetReason(IntEnum):
    NONE = 0
    WATCHDOG = 1
    POWER_ON = 2
    SOFTWARE = 3


class FaultCode(IntEnum):
    NONE = 0
    SENSOR_SATURATION = 1


@dataclass(frozen=True)
class CanFrame:
    timestamp: float
    arbitration_id: int
    data: bytes
    channel: str = ""
    is_extended_id: bool = False

    @property
    def hex_data(self) -> str:
        return self.data.hex().upper()


@dataclass(frozen=True)
class DecodedSignal:
    name: str
    value: float | int | str
    unit: str
    minimum: float | None
    maximum: float | None

    @property
    def numeric_value(self) -> float | None:
        if isinstance(self.value, bool):
            return None
        if isinstance(self.value, (int, float)):
            return float(self.value)
        return None

    def format_value(self) -> str:
        if isinstance(self.value, float):
            text = f"{self.value:.3f}".rstrip("0")
            if text.endswith("."):
                text += "0"
        else:
            text = str(self.value)
        return f"{text} {self.unit}".strip()


@dataclass(frozen=True)
class DecodedFrame:
    frame: CanFrame
    message_name: str
    sender: str
    signals: tuple[DecodedSignal, ...]
    is_heartbeat: bool
    cycle_time_ms: int | None

    def signal(self, name: str) -> DecodedSignal | None:
        for item in self.signals:
            if item.name == name:
                return item
        return None

    def signal_map(self) -> dict[str, float | int | str]:
        return {item.name: item.value for item in self.signals}


@dataclass(frozen=True)
class UnknownFrame:
    frame: CanFrame


@dataclass
class DiagnosticEvent:
    timestamp: float
    kind: EventKind
    node: str
    arbitration_id: int | None
    message_name: str
    summary: str
    evidence: str
    expectation: str
    limitation: str
    recovery_seconds: float | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": round(self.timestamp, 6),
            "classification": self.kind.value,
            "node": self.node,
            "can_id": None if self.arbitration_id is None else f"0x{self.arbitration_id:03X}",
            "message_name": self.message_name,
            "summary": self.summary,
            "observed_evidence": self.evidence,
            "configured_expectation": self.expectation,
            "recovery_seconds": (
                None if self.recovery_seconds is None else round(self.recovery_seconds, 6)
            ),
            "limitation": self.limitation,
            "details": dict(self.details),
        }
