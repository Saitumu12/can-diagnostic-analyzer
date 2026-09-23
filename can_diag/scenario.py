from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

CAPTURE_EPOCH = 1700000000.0
DATA_PERIOD_S = 0.1
HEARTBEAT_PERIOD_S = 1.0
TICKS_PER_HEARTBEAT = int(round(HEARTBEAT_PERIOD_S / DATA_PERIOD_S))

SCHEDULE_JITTER_S = 0.004
TEMPERATURE_NOISE_C = 0.08
VOLTAGE_NOISE_V = 0.008

PHASE_OFFSET_S = {
    "POWER_DATA": 0.000,
    "THERMAL_DATA": 0.020,
    "POWER_HEARTBEAT": 0.050,
    "THERMAL_HEARTBEAT": 0.060,
}

POWER_MONITOR = "PowerMonitor"
THERMAL_CONTROLLER = "ThermalController"

RANGE_START_S = 15.0
RANGE_SECONDS = 5
SATURATED_TEMPERATURE_RAW = 0x7FFF

SILENT_START_S = 30.0
SILENT_SECONDS = 3

SKIP_AT_S = 45.0
SKIP_COUNT = 4

RESTART_AT_S = 10.0
RESTART_DOWNTIME_S = 2.0

NORMAL_DURATION_S = 10.0
FAULT_DURATION_S = 60.0
RESTART_DURATION_S = 20.0

NORMAL_SEED = 20240517
FAULT_SEED = 20240518
RESTART_SEED = 20240519


class InjectionKind(StrEnum):
    RANGE = "range"
    SILENT = "silent"
    SKIP = "skip"
    RESTART = "restart"


@dataclass(frozen=True)
class Injection:
    kind: InjectionKind
    node: str
    command: str
    start_s: float
    end_s: float | None
    description: str
    expected_findings: tuple[str, ...]

    def covers(self, seconds: float) -> bool:
        if self.end_s is None:
            return seconds == self.start_s
        return self.start_s <= seconds < self.end_s

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "node": self.node,
            "serial_command": self.command,
            "start_seconds": self.start_s,
            "end_seconds": self.end_s,
            "interval": "half-open [start, end)" if self.end_s is not None else "instantaneous",
            "description": self.description,
            "expected_findings": list(self.expected_findings),
        }


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    duration_s: float
    seed: int
    injections: tuple[Injection, ...] = ()

    def injection(self, kind: InjectionKind) -> Injection | None:
        for item in self.injections:
            if item.kind is kind:
                return item
        return None

    def active(self, kind: InjectionKind, seconds: float) -> bool:
        item = self.injection(kind)
        return item is not None and item.covers(seconds)


RANGE_INJECTION = Injection(
    kind=InjectionKind.RANGE,
    node=THERMAL_CONTROLLER,
    command=f"range {RANGE_SECONDS}",
    start_s=RANGE_START_S,
    end_s=RANGE_START_S + RANGE_SECONDS,
    description=(
        f"ThermalController reports a saturated raw temperature of 0x7FFF with node state "
        f"Degraded and fault code SensorSaturation for {RANGE_SECONDS} seconds. Uptime and the "
        "sequence counter keep running."
    ),
    expected_findings=("range_violation", "range_recovered"),
)

SILENT_INJECTION = Injection(
    kind=InjectionKind.SILENT,
    node=THERMAL_CONTROLLER,
    command=f"silent {SILENT_SECONDS}",
    start_s=SILENT_START_S,
    end_s=SILENT_START_S + SILENT_SECONDS,
    description=(
        f"ThermalController transmits neither THERMAL_DATA nor THERMAL_HEARTBEAT for "
        f"{SILENT_SECONDS} seconds. The node keeps running, so uptime continues and the sequence "
        "counter resumes where it stopped. PowerMonitor traffic is unaffected."
    ),
    expected_findings=(
        "data_message_stale",
        "heartbeat_timeout",
        "node_silent",
        "message_recovered",
        "node_recovered",
    ),
)

SKIP_INJECTION = Injection(
    kind=InjectionKind.SKIP,
    node=THERMAL_CONTROLLER,
    command=f"skip {SKIP_COUNT}",
    start_s=SKIP_AT_S,
    end_s=None,
    description=(
        f"ThermalController advances its sequence counter by {SKIP_COUNT} extra values before the "
        f"next transmission, leaving {SKIP_COUNT} counter values unobserved."
    ),
    expected_findings=("sequence_gap",),
)

RESTART_INJECTION = Injection(
    kind=InjectionKind.RESTART,
    node=THERMAL_CONTROLLER,
    command="restart",
    start_s=RESTART_AT_S,
    end_s=RESTART_AT_S + RESTART_DOWNTIME_S,
    description=(
        f"ThermalController performs a controlled software restart, stays off the bus for "
        f"{RESTART_DOWNTIME_S:g} seconds, and comes back with uptime zero, sequence counter zero "
        "and reset reason Software."
    ),
    expected_findings=(
        "data_message_stale",
        "heartbeat_timeout",
        "node_silent",
        "message_recovered",
        "node_recovered",
        "node_restart",
    ),
)

NORMAL_SESSION = Scenario(
    name="normal_session",
    description=(
        "Both nodes running normally for ten seconds with small scheduling jitter and no injected "
        "conditions."
    ),
    duration_s=NORMAL_DURATION_S,
    seed=NORMAL_SEED,
)

FAULT_SESSION = Scenario(
    name="fault_session",
    description=(
        "Sixty seconds carrying the three ThermalController fault-injection commands that the "
        "firmware implements: a five second out-of-range reading, a three second transmission "
        "silence, and a four value sequence-counter skip."
    ),
    duration_s=FAULT_DURATION_S,
    seed=FAULT_SEED,
    injections=(RANGE_INJECTION, SILENT_INJECTION, SKIP_INJECTION),
)

RESTART_SESSION = Scenario(
    name="restart_session",
    description=(
        "Twenty seconds containing a single controlled ThermalController software restart, kept "
        "separate from the main fault session so the restart evidence can be examined on its own."
    ),
    duration_s=RESTART_DURATION_S,
    seed=RESTART_SEED,
    injections=(RESTART_INJECTION,),
)

ALL_SCENARIOS = (NORMAL_SESSION, FAULT_SESSION, RESTART_SESSION)


def scenario_by_name(name: str) -> Scenario:
    for item in ALL_SCENARIOS:
        if item.name == name:
            return item
    known = ", ".join(item.name for item in ALL_SCENARIOS)
    raise KeyError(f"unknown scenario '{name}'; known scenarios are {known}")
