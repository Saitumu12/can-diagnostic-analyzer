from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from can_diag.decoder import NetworkDatabase
from can_diag.logfile import write_log
from can_diag.models import CanFrame, FaultCode, NodeState, ResetReason
from can_diag.scenario import (
    ALL_SCENARIOS,
    CAPTURE_EPOCH,
    DATA_PERIOD_S,
    PHASE_OFFSET_S,
    POWER_MONITOR,
    SATURATED_TEMPERATURE_RAW,
    SCHEDULE_JITTER_S,
    SKIP_COUNT,
    TEMPERATURE_NOISE_C,
    THERMAL_CONTROLLER,
    TICKS_PER_HEARTBEAT,
    VOLTAGE_NOISE_V,
    InjectionKind,
    Scenario,
)

COUNTER_MODULUS = 256
TEMPERATURE_SCALE = 0.1
VOLTAGE_SCALE = 0.01


@dataclass
class SyntheticCapture:
    scenario: Scenario
    frames: list[CanFrame] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.scenario.name

    def ground_truth_document(self) -> dict[str, Any]:
        first = self.frames[0].timestamp if self.frames else CAPTURE_EPOCH
        last = self.frames[-1].timestamp if self.frames else CAPTURE_EPOCH
        return {
            "capture": f"{self.name}.log",
            "data_origin": "synthetic",
            "generator": "can_diag.synthetic",
            "seed": self.scenario.seed,
            "deterministic": True,
            "duration_seconds": self.scenario.duration_s,
            "frame_count": len(self.frames),
            "first_timestamp": round(first, 6),
            "last_timestamp": round(last, 6),
            "description": self.scenario.description,
            "injected_events": [item.to_dict() for item in self.scenario.injections],
        }


def _temperature_curve(node: str, seconds: float) -> float:
    if node == POWER_MONITOR:
        return 31.0 + 4.0 * math.sin(2.0 * math.pi * seconds / 23.0)
    return 24.5 + 6.0 * math.sin(2.0 * math.pi * seconds / 17.0)


def _voltage_curve(node: str, seconds: float) -> float:
    if node == POWER_MONITOR:
        return 12.42 + 0.18 * math.sin(2.0 * math.pi * seconds / 11.0)
    return 12.36 + 0.14 * math.sin(2.0 * math.pi * seconds / 13.0)


def _quantize(value: float, scale: float) -> float:
    return round(round(value / scale) * scale, 6)


class _NodeModel:
    def __init__(
        self,
        database: NetworkDatabase,
        node: str,
        data_message: str,
        heartbeat_message: str,
        scenario: Scenario,
        rng: random.Random,
        channel: str,
    ) -> None:
        self._database = database
        self._node = node
        self._data_message = data_message
        self._heartbeat_message = heartbeat_message
        self._scenario = scenario
        self._rng = rng
        self._channel = channel
        self._counter = 0
        self._boot_tick = 0
        self._reset_reason = ResetReason.POWER_ON
        self._booted = False

    def _active(self, kind: InjectionKind, seconds: float) -> bool:
        item = self._scenario.injection(kind)
        return item is not None and item.node == self._node and item.covers(seconds)

    def tick(self, tick_index: int) -> list[CanFrame]:
        nominal = round(tick_index * DATA_PERIOD_S, 6)
        if self._active(InjectionKind.RESTART, nominal):
            self._booted = False
            return []

        frames: list[CanFrame] = []
        if not self._booted:
            frames.append(self._boot(tick_index, nominal))

        if self._active(InjectionKind.SILENT, nominal):
            return frames

        local_tick = tick_index - self._boot_tick
        frames.append(self._data_frame(nominal, local_tick))
        if local_tick != 0 and local_tick % TICKS_PER_HEARTBEAT == 0:
            frames.append(self._heartbeat_frame(nominal, local_tick))
        return frames

    def _boot(self, tick_index: int, nominal: float) -> CanFrame:
        if tick_index != 0:
            self._reset_reason = ResetReason.SOFTWARE
            self._counter = 0
        self._boot_tick = tick_index
        self._booted = True
        return self._heartbeat_frame(nominal, 0)

    def _data_frame(self, nominal: float, local_tick: int) -> CanFrame:
        if self._active(InjectionKind.SKIP, nominal):
            self._counter = (self._counter + SKIP_COUNT) % COUNTER_MODULUS

        if self._active(InjectionKind.RANGE, nominal):
            temperature = SATURATED_TEMPERATURE_RAW * TEMPERATURE_SCALE
            state = NodeState.DEGRADED
            fault = FaultCode.SENSOR_SATURATION
        else:
            temperature = _quantize(
                _temperature_curve(self._node, nominal)
                + self._rng.uniform(-TEMPERATURE_NOISE_C, TEMPERATURE_NOISE_C),
                TEMPERATURE_SCALE,
            )
            state = NodeState.NORMAL
            fault = FaultCode.NONE

        voltage = _quantize(
            _voltage_curve(self._node, nominal)
            + self._rng.uniform(-VOLTAGE_NOISE_V, VOLTAGE_NOISE_V),
            VOLTAGE_SCALE,
        )
        signals = {
            "ModuleTemperature": temperature,
            "SupplyVoltage": voltage,
            "NodeState": int(state),
            "SequenceCounter": self._counter,
            "FaultCode": int(fault),
            "Reserved": 0,
        }
        self._counter = (self._counter + 1) % COUNTER_MODULUS
        return self._encode(self._data_message, nominal, signals)

    def _heartbeat_frame(self, nominal: float, local_tick: int) -> CanFrame:
        degraded = self._active(InjectionKind.RANGE, nominal)
        signals = {
            "Uptime": int(local_tick * DATA_PERIOD_S),
            "NodeState": int(NodeState.DEGRADED if degraded else NodeState.NORMAL),
            "ResetReason": int(self._reset_reason),
            "Reserved": 0,
        }
        return self._encode(self._heartbeat_message, nominal, signals)

    def _encode(self, message_name: str, nominal: float, signals: dict[str, Any]) -> CanFrame:
        message = self._database.database.get_message_by_name(message_name)
        offset = PHASE_OFFSET_S[message_name]
        jitter = round(self._rng.uniform(-SCHEDULE_JITTER_S, SCHEDULE_JITTER_S), 6)
        return CanFrame(
            timestamp=round(CAPTURE_EPOCH + nominal + offset + jitter, 6),
            arbitration_id=message.frame_id,
            data=bytes(message.encode(signals, strict=False)),
            channel=self._channel,
        )


def generate_capture(
    database: NetworkDatabase, scenario: Scenario, channel: str = "vcan0"
) -> SyntheticCapture:
    nodes = [
        _NodeModel(
            database,
            POWER_MONITOR,
            "POWER_DATA",
            "POWER_HEARTBEAT",
            scenario,
            random.Random(f"{scenario.seed}:{POWER_MONITOR}"),
            channel,
        ),
        _NodeModel(
            database,
            THERMAL_CONTROLLER,
            "THERMAL_DATA",
            "THERMAL_HEARTBEAT",
            scenario,
            random.Random(f"{scenario.seed}:{THERMAL_CONTROLLER}"),
            channel,
        ),
    ]

    frames: list[CanFrame] = []
    for tick_index in range(int(round(scenario.duration_s / DATA_PERIOD_S))):
        for node in nodes:
            frames.extend(node.tick(tick_index))

    frames.sort(key=lambda frame: frame.timestamp)
    return SyntheticCapture(scenario=scenario, frames=frames)


def write_capture(capture: SyntheticCapture, directory: Path) -> tuple[Path, Path]:
    log_path = directory / f"{capture.name}.log"
    truth_path = directory / f"{capture.name}_ground_truth.json"
    write_log(log_path, capture.frames)
    truth_path.parent.mkdir(parents=True, exist_ok=True)
    truth_path.write_text(
        json.dumps(capture.ground_truth_document(), indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return log_path, truth_path


def generate_all(database: NetworkDatabase, directory: Path) -> list[SyntheticCapture]:
    captures = [generate_capture(database, scenario) for scenario in ALL_SCENARIOS]
    for capture in captures:
        write_capture(capture, directory)
    return captures
