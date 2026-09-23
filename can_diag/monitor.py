from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from can_diag.config import AnalyzerConfig
from can_diag.decoder import MessageInfo, NetworkDatabase
from can_diag.models import (
    CanFrame,
    DecodedFrame,
    DiagnosticEvent,
    EventKind,
    UnknownFrame,
)

SEQUENCE_SIGNAL = "SequenceCounter"
UPTIME_SIGNAL = "Uptime"
RESET_REASON_SIGNAL = "ResetReason"
NODE_STATE_SIGNAL = "NodeState"
COUNTER_MODULUS = 256

LOCALIZATION_LIMITATION = (
    "Message-level data alone cannot distinguish firmware, power, wiring, transceiver, "
    "or bus-controller failure."
)
RANGE_LIMITATION = (
    "The configured range is a project-defined plausibility limit, not a manufacturer "
    "specification, so an excursion indicates an implausible reported value rather than a "
    "proven sensor defect."
)
SEQUENCE_LIMITATION = (
    "A counter gap proves that the expected counter values were never observed. It does not "
    "distinguish a transmitter that advanced its own counter from frames lost on the bus or "
    "dropped by the receiving adapter."
)
UNKNOWN_LIMITATION = (
    "An identifier absent from the database cannot be decoded, so its content and origin "
    "are unverified."
)
RECOVERY_LIMITATION = (
    "Resumed traffic shows that the transmitter is reachable again. It does not identify "
    "what interrupted the earlier frames."
)


@dataclass
class MessageState:
    info: MessageInfo
    last_timestamp: float | None = None
    stale_since: float | None = None
    last_sequence: int | None = None
    last_uptime: int | None = None
    range_violation_since: dict[str, float] = field(default_factory=dict)
    frame_count: int = 0


@dataclass
class NodeState:
    name: str
    silent_since: float | None = None
    last_restart_timestamp: float | None = None
    restart_count: int = 0


class DiagnosticMonitor:
    def __init__(self, database: NetworkDatabase, config: AnalyzerConfig | None = None) -> None:
        self._database = database
        self._config = config or AnalyzerConfig()
        self._messages: dict[str, MessageState] = {
            info.name: MessageState(info=info) for info in database.messages()
        }
        self._nodes: dict[str, NodeState] = {
            node: NodeState(name=node) for node in database.nodes()
        }
        self._unknown_ids: dict[int, int] = {}
        self._events: list[DiagnosticEvent] = []
        self._last_timestamp: float | None = None
        self._first_timestamp: float | None = None
        self._frame_count: int = 0

    @property
    def config(self) -> AnalyzerConfig:
        return self._config

    @property
    def events(self) -> list[DiagnosticEvent]:
        return sorted(self._events, key=lambda event: event.timestamp)

    @property
    def unknown_ids(self) -> dict[int, int]:
        return dict(self._unknown_ids)

    @property
    def first_timestamp(self) -> float | None:
        return self._first_timestamp

    @property
    def last_timestamp(self) -> float | None:
        return self._last_timestamp

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def message_states(self) -> dict[str, MessageState]:
        return dict(self._messages)

    def node_names(self) -> list[str]:
        return sorted(self._nodes)

    def process_frame(self, frame: CanFrame) -> list[DiagnosticEvent]:
        produced: list[DiagnosticEvent] = []
        produced.extend(self._evaluate_timeouts(frame.timestamp))

        if self._first_timestamp is None:
            self._first_timestamp = frame.timestamp
        self._last_timestamp = frame.timestamp
        self._frame_count += 1

        decoded = self._database.decode(frame)
        if isinstance(decoded, UnknownFrame):
            produced.extend(self._handle_unknown(frame))
        else:
            produced.extend(self._handle_decoded(decoded))

        self._events.extend(produced)
        return produced

    def process_frames(self, frames: Iterable[CanFrame]) -> list[DiagnosticEvent]:
        produced: list[DiagnosticEvent] = []
        for frame in frames:
            produced.extend(self.process_frame(frame))
        return produced

    def finalize(self, until: float | None = None) -> list[DiagnosticEvent]:
        end = self._last_timestamp if until is None else until
        if end is None:
            return []
        produced = self._evaluate_timeouts(end)
        self._events.extend(produced)
        return produced

    def _evaluate_timeouts(self, now: float) -> list[DiagnosticEvent]:
        produced: list[DiagnosticEvent] = []
        for state in self._messages.values():
            if state.last_timestamp is None or state.stale_since is not None:
                continue
            threshold = self._config.stale_threshold_seconds(
                state.info.name, state.info.is_heartbeat
            )
            if now - state.last_timestamp <= threshold:
                continue
            state.stale_since = state.last_timestamp + threshold
            produced.append(self._stale_event(state, threshold))
        produced.extend(self._evaluate_node_silence())
        return produced

    def _stale_event(self, state: MessageState, threshold: float) -> DiagnosticEvent:
        info = state.info
        kind = EventKind.HEARTBEAT_TIMEOUT if info.is_heartbeat else EventKind.DATA_MESSAGE_STALE
        role = "heartbeat" if info.is_heartbeat else "cyclic data message"
        period = "unspecified" if info.cycle_time_ms is None else f"{info.cycle_time_ms} ms"
        last_seen = state.last_timestamp if state.last_timestamp is not None else 0.0
        declared_at = state.stale_since if state.stale_since is not None else last_seen
        return DiagnosticEvent(
            timestamp=declared_at,
            kind=kind,
            node=info.sender,
            arbitration_id=info.frame_id,
            message_name=info.name,
            summary=f"{info.name} exceeded its freshness threshold",
            evidence=(
                f"No frame with identifier 0x{info.frame_id:03X} was observed for more than "
                f"{threshold * 1000:.0f} ms after the frame at t={last_seen:.3f} s."
            ),
            expectation=(
                f"{info.name} is a {role} declared with a {period} cycle time and is configured "
                f"to become stale after {threshold * 1000:.0f} ms."
            ),
            limitation=LOCALIZATION_LIMITATION,
            details={
                "threshold_ms": round(threshold * 1000, 3),
                "last_seen_timestamp": round(last_seen, 6),
                "frames_received_before_timeout": state.frame_count,
            },
        )

    def _evaluate_node_silence(self) -> list[DiagnosticEvent]:
        produced: list[DiagnosticEvent] = []
        for node in self._nodes.values():
            states = [
                state
                for state in self._messages.values()
                if state.info.sender == node.name and state.last_timestamp is not None
            ]
            if not states or node.silent_since is not None:
                continue
            stale_points = [state.stale_since for state in states if state.stale_since is not None]
            if len(stale_points) != len(states):
                continue
            silent_since = max(stale_points)
            node.silent_since = silent_since
            identifiers = ", ".join(
                f"{state.info.name} (0x{state.info.frame_id:03X})"
                for state in sorted(states, key=lambda item: item.info.frame_id)
            )
            active_nodes = sorted(
                other.name
                for other in self._nodes.values()
                if other.name != node.name and other.silent_since is None
            )
            active_text = (
                f"Traffic from {', '.join(active_nodes)} remained active."
                if active_nodes
                else "No other node was transmitting at the same time."
            )
            produced.append(
                DiagnosticEvent(
                    timestamp=silent_since,
                    kind=EventKind.NODE_SILENT,
                    node=node.name,
                    arbitration_id=None,
                    message_name="",
                    summary=f"All expected {node.name} message identifiers became stale",
                    evidence=(
                        f"Every expected {node.name} identifier became stale: {identifiers}. "
                        f"{active_text}"
                    ),
                    expectation=(
                        f"{node.name} is expected to transmit {len(states)} cyclic identifiers "
                        "within their configured freshness thresholds."
                    ),
                    limitation=(
                        f"The evidence localizes the communication interruption to {node.name} "
                        f"or its connection. {LOCALIZATION_LIMITATION}"
                    ),
                    details={
                        "message_names": [state.info.name for state in states],
                        "can_ids": [f"0x{state.info.frame_id:03X}" for state in states],
                        "other_active_nodes": active_nodes,
                    },
                )
            )
        return produced

    def _handle_unknown(self, frame: CanFrame) -> list[DiagnosticEvent]:
        count = self._unknown_ids.get(frame.arbitration_id, 0) + 1
        self._unknown_ids[frame.arbitration_id] = count
        if count > 1 or not self._config.report_unknown_ids:
            return []
        return [
            DiagnosticEvent(
                timestamp=frame.timestamp,
                kind=EventKind.UNKNOWN_MESSAGE_ID,
                node="Unknown",
                arbitration_id=frame.arbitration_id,
                message_name="",
                summary=f"Identifier 0x{frame.arbitration_id:03X} is not defined in the database",
                evidence=(
                    f"Frame 0x{frame.arbitration_id:03X} carried payload {frame.hex_data} and has "
                    "no matching database entry."
                ),
                expectation=(
                    "Only identifiers declared in the loaded DBC are expected on this bench bus."
                ),
                limitation=UNKNOWN_LIMITATION,
                details={"payload": frame.hex_data},
            )
        ]

    def _handle_decoded(self, decoded: DecodedFrame) -> list[DiagnosticEvent]:
        state = self._messages[decoded.message_name]
        node = self._nodes.setdefault(decoded.sender, NodeState(name=decoded.sender))
        produced: list[DiagnosticEvent] = []
        timestamp = decoded.frame.timestamp

        if state.stale_since is not None:
            produced.append(self._message_recovered_event(state, timestamp))
            state.stale_since = None
            state.last_sequence = None

        if decoded.is_heartbeat:
            produced.extend(self._check_restart(decoded, state, node))
        elif self._config.check_sequence:
            produced.extend(self._check_sequence(decoded, state, node))

        if self._config.check_ranges:
            produced.extend(self._check_ranges(decoded, state))

        state.last_timestamp = timestamp
        state.frame_count += 1

        if node.silent_since is not None:
            produced.append(self._node_recovered_event(node, timestamp))
            node.silent_since = None

        return produced

    def _message_recovered_event(self, state: MessageState, timestamp: float) -> DiagnosticEvent:
        info = state.info
        stale_since = state.stale_since if state.stale_since is not None else timestamp
        last_seen = state.last_timestamp if state.last_timestamp is not None else timestamp
        alarm_duration = timestamp - stale_since
        outage = timestamp - last_seen
        return DiagnosticEvent(
            timestamp=timestamp,
            kind=EventKind.MESSAGE_RECOVERED,
            node=info.sender,
            arbitration_id=info.frame_id,
            message_name=info.name,
            summary=f"{info.name} resumed after a period of silence",
            evidence=(
                f"Identifier 0x{info.frame_id:03X} was absent for {outage * 1000:.0f} ms and was "
                f"received again at t={timestamp:.3f} s."
            ),
            expectation=(
                f"{info.name} is expected to reappear within its configured freshness threshold."
            ),
            limitation=RECOVERY_LIMITATION,
            recovery_seconds=alarm_duration,
            details={"outage_seconds": round(outage, 6)},
        )

    def _node_recovered_event(self, node: NodeState, timestamp: float) -> DiagnosticEvent:
        silent_since = node.silent_since if node.silent_since is not None else timestamp
        return DiagnosticEvent(
            timestamp=timestamp,
            kind=EventKind.NODE_RECOVERED,
            node=node.name,
            arbitration_id=None,
            message_name="",
            summary=f"{node.name} resumed transmitting",
            evidence=(
                f"{node.name} traffic reappeared at t={timestamp:.3f} s after being classified "
                f"silent from t={silent_since:.3f} s."
            ),
            expectation=f"{node.name} is expected to transmit continuously while powered.",
            limitation=RECOVERY_LIMITATION,
            recovery_seconds=timestamp - silent_since,
            details={"silent_since": round(silent_since, 6)},
        )

    def _check_restart(
        self, decoded: DecodedFrame, state: MessageState, node: NodeState
    ) -> list[DiagnosticEvent]:
        uptime_signal = decoded.signal(UPTIME_SIGNAL)
        if uptime_signal is None:
            return []
        uptime_value = uptime_signal.numeric_value
        if uptime_value is None:
            return []
        uptime = int(uptime_value)
        previous = state.last_uptime
        state.last_uptime = uptime
        if previous is None or uptime >= previous:
            return []

        node.last_restart_timestamp = decoded.frame.timestamp
        node.restart_count += 1
        for other in self._messages.values():
            if other.info.sender == node.name:
                other.last_sequence = None

        reset_signal = decoded.signal(RESET_REASON_SIGNAL)
        reset_reason = "unreported" if reset_signal is None else str(reset_signal.value)
        return [
            DiagnosticEvent(
                timestamp=decoded.frame.timestamp,
                kind=EventKind.NODE_RESTART,
                node=node.name,
                arbitration_id=decoded.frame.arbitration_id,
                message_name=decoded.message_name,
                summary=f"{node.name} reported a restart",
                evidence=(
                    f"Reported uptime fell from {previous} s to {uptime} s and the reset reason "
                    f"signal read {reset_reason}."
                ),
                expectation=(
                    "Uptime is expected to increase monotonically while the node keeps running."
                ),
                limitation=(
                    "The reset reason is self-reported by the node. A restart that prevented the "
                    "node from reporting would not appear here."
                ),
                details={
                    "previous_uptime_s": previous,
                    "reported_uptime_s": uptime,
                    "reset_reason": reset_reason,
                    "restart_count": node.restart_count,
                },
            )
        ]

    def _check_sequence(
        self, decoded: DecodedFrame, state: MessageState, node: NodeState
    ) -> list[DiagnosticEvent]:
        signal = decoded.signal(SEQUENCE_SIGNAL)
        if signal is None:
            return []
        value = signal.numeric_value
        if value is None:
            return []
        current = int(value)
        previous = state.last_sequence
        state.last_sequence = current
        if previous is None:
            return []

        forward_distance = (current - previous - 1) % COUNTER_MODULUS
        if forward_distance == 0:
            return []

        suppression = self._config.restart_suppression_ms / 1000.0
        restart_at = node.last_restart_timestamp
        if restart_at is not None and decoded.frame.timestamp - restart_at <= suppression:
            return []
        if forward_distance > self._config.max_reportable_gap:
            return []

        expected = (previous + 1) % COUNTER_MODULUS
        return [
            DiagnosticEvent(
                timestamp=decoded.frame.timestamp,
                kind=EventKind.SEQUENCE_GAP,
                node=decoded.sender,
                arbitration_id=decoded.frame.arbitration_id,
                message_name=decoded.message_name,
                summary=(
                    f"{decoded.message_name} sequence counter skipped {forward_distance} value(s)"
                ),
                evidence=(
                    f"The counter advanced from {previous} to {current}, skipping "
                    f"{forward_distance} value(s); the next expected value was {expected}."
                ),
                expectation=(
                    "SequenceCounter is expected to increment by one per transmitted frame and "
                    "wrap from 255 to 0."
                ),
                limitation=SEQUENCE_LIMITATION,
                details={
                    "previous_counter": previous,
                    "observed_counter": current,
                    "expected_counter": expected,
                    "skipped_values": forward_distance,
                },
            )
        ]

    def _check_ranges(self, decoded: DecodedFrame, state: MessageState) -> list[DiagnosticEvent]:
        produced: list[DiagnosticEvent] = []
        timestamp = decoded.frame.timestamp
        for signal in decoded.signals:
            value = signal.numeric_value
            if value is None or (signal.minimum is None and signal.maximum is None):
                continue
            below = signal.minimum is not None and value < signal.minimum
            above = signal.maximum is not None and value > signal.maximum
            violating = below or above
            active_since = state.range_violation_since.get(signal.name)
            bounds = (
                f"{signal.name} is declared valid between {signal.minimum} and "
                f"{signal.maximum} {signal.unit}".strip()
            )

            if violating and active_since is None:
                state.range_violation_since[signal.name] = timestamp
                direction = (
                    "below the configured minimum" if below else "above the configured maximum"
                )
                limit = signal.minimum if below else signal.maximum
                node_state = decoded.signal(NODE_STATE_SIGNAL)
                fault_code = decoded.signal("FaultCode")
                context = ""
                if node_state is not None:
                    context += f" Reported node state was {node_state.value}."
                if fault_code is not None:
                    context += f" Reported fault code was {fault_code.value}."
                produced.append(
                    DiagnosticEvent(
                        timestamp=timestamp,
                        kind=EventKind.RANGE_VIOLATION,
                        node=decoded.sender,
                        arbitration_id=decoded.frame.arbitration_id,
                        message_name=decoded.message_name,
                        summary=f"{decoded.message_name}.{signal.name} left its valid range",
                        evidence=(
                            f"Decoded value {signal.format_value()} is {direction} "
                            f"({limit} {signal.unit}).{context} Raw payload was "
                            f"{decoded.frame.hex_data}."
                        ),
                        expectation=bounds,
                        limitation=RANGE_LIMITATION,
                        details={
                            "signal": signal.name,
                            "value": value,
                            "minimum": signal.minimum,
                            "maximum": signal.maximum,
                            "unit": signal.unit,
                            "payload": decoded.frame.hex_data,
                        },
                    )
                )
            elif not violating and active_since is not None:
                del state.range_violation_since[signal.name]
                produced.append(
                    DiagnosticEvent(
                        timestamp=timestamp,
                        kind=EventKind.RANGE_RECOVERED,
                        node=decoded.sender,
                        arbitration_id=decoded.frame.arbitration_id,
                        message_name=decoded.message_name,
                        summary=(
                            f"{decoded.message_name}.{signal.name} returned to its valid range"
                        ),
                        evidence=(
                            f"Decoded value {signal.format_value()} is inside the configured "
                            f"range again at t={timestamp:.3f} s."
                        ),
                        expectation=bounds,
                        limitation=RANGE_LIMITATION,
                        recovery_seconds=timestamp - active_since,
                        details={"signal": signal.name, "value": value},
                    )
                )
        return produced


def analyze_frames(
    database: NetworkDatabase,
    frames: Sequence[CanFrame],
    config: AnalyzerConfig | None = None,
) -> DiagnosticMonitor:
    monitor = DiagnosticMonitor(database, config)
    monitor.process_frames(frames)
    monitor.finalize()
    return monitor
