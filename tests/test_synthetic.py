from __future__ import annotations

import json
from pathlib import Path

import pytest

from can_diag.decoder import NetworkDatabase
from can_diag.logfile import read_log
from can_diag.models import DecodedFrame, EventKind
from can_diag.monitor import analyze_frames
from can_diag.scenario import (
    ALL_SCENARIOS,
    CAPTURE_EPOCH,
    DATA_PERIOD_S,
    FAULT_SESSION,
    NORMAL_SESSION,
    RANGE_SECONDS,
    RANGE_START_S,
    RESTART_SESSION,
    SILENT_SECONDS,
    SILENT_START_S,
    SKIP_AT_S,
    SKIP_COUNT,
    InjectionKind,
    Scenario,
    scenario_by_name,
)
from can_diag.synthetic import generate_capture
from tests.conftest import (
    POWER_DATA_ID,
    POWER_HEARTBEAT_ID,
    SYNTHETIC_DIR,
    THERMAL_DATA_ID,
    THERMAL_HEARTBEAT_ID,
)


def decoded_stream(
    database: NetworkDatabase, scenario: Scenario, arbitration_id: int
) -> list[tuple[float, dict[str, object]]]:
    capture = generate_capture(database, scenario)
    stream = []
    for frame in capture.frames:
        if frame.arbitration_id != arbitration_id:
            continue
        decoded = database.decode(frame)
        assert isinstance(decoded, DecodedFrame)
        stream.append((round(frame.timestamp - CAPTURE_EPOCH, 6), decoded.signal_map()))
    return stream


def findings(database: NetworkDatabase, scenario: Scenario) -> list[str]:
    capture = generate_capture(database, scenario)
    return [event.kind.value for event in analyze_frames(database, capture.frames).events]


def test_scenario_lookup_is_exhaustive() -> None:
    for scenario in ALL_SCENARIOS:
        assert scenario_by_name(scenario.name) is scenario
    with pytest.raises(KeyError):
        scenario_by_name("no_such_session")


def test_generation_is_deterministic(database: NetworkDatabase) -> None:
    for scenario in ALL_SCENARIOS:
        first = generate_capture(database, scenario)
        second = generate_capture(database, scenario)
        assert [frame.timestamp for frame in first.frames] == [
            frame.timestamp for frame in second.frames
        ]
        assert [frame.data for frame in first.frames] == [frame.data for frame in second.frames]


def test_timestamps_are_monotonic(database: NetworkDatabase) -> None:
    for scenario in ALL_SCENARIOS:
        timestamps = [frame.timestamp for frame in generate_capture(database, scenario).frames]
        assert timestamps == sorted(timestamps)


def test_normal_session_shape(database: NetworkDatabase) -> None:
    capture = generate_capture(database, NORMAL_SESSION)
    counts: dict[int, int] = {}
    for frame in capture.frames:
        counts[frame.arbitration_id] = counts.get(frame.arbitration_id, 0) + 1
    assert counts[POWER_DATA_ID] == 100
    assert counts[THERMAL_DATA_ID] == 100
    assert counts[POWER_HEARTBEAT_ID] == 10
    assert counts[THERMAL_HEARTBEAT_ID] == 10


def test_fault_free_traffic_raises_no_findings(database: NetworkDatabase) -> None:
    assert findings(database, NORMAL_SESSION) == []


def test_range_injection_lasts_exactly_five_seconds(database: NetworkDatabase) -> None:
    stream = decoded_stream(database, FAULT_SESSION, THERMAL_DATA_ID)
    out_of_range = [
        seconds for seconds, signals in stream if float(signals["ModuleTemperature"]) > 150.0
    ]

    assert len(out_of_range) == int(RANGE_SECONDS / DATA_PERIOD_S)
    assert RANGE_START_S <= out_of_range[0] < RANGE_START_S + DATA_PERIOD_S
    assert out_of_range[-1] < RANGE_START_S + RANGE_SECONDS

    for seconds, signals in stream:
        saturated = float(signals["ModuleTemperature"]) > 150.0
        inside = RANGE_START_S <= seconds < RANGE_START_S + RANGE_SECONDS + DATA_PERIOD_S
        if saturated:
            assert inside
            assert str(signals["NodeState"]) == "Degraded"
            assert str(signals["FaultCode"]) == "SensorSaturation"
        elif seconds > RANGE_START_S + RANGE_SECONDS + DATA_PERIOD_S:
            assert str(signals["NodeState"]) == "Normal"
            assert str(signals["FaultCode"]) == "None"


def test_range_injection_does_not_disturb_counter_or_uptime(database: NetworkDatabase) -> None:
    data = decoded_stream(database, FAULT_SESSION, THERMAL_DATA_ID)
    window = [
        int(signals["SequenceCounter"])
        for seconds, signals in data
        if RANGE_START_S - 1.0 < seconds < RANGE_START_S + RANGE_SECONDS + 1.0
    ]
    assert window == [(window[0] + offset) % 256 for offset in range(len(window))]

    heartbeats = decoded_stream(database, FAULT_SESSION, THERMAL_HEARTBEAT_ID)
    uptimes = [
        int(signals["Uptime"])
        for seconds, signals in heartbeats
        if RANGE_START_S - 1.0 < seconds < RANGE_START_S + RANGE_SECONDS + 1.0
    ]
    assert uptimes == sorted(uptimes)
    assert uptimes == [uptimes[0] + offset for offset in range(len(uptimes))]


def test_silent_injection_covers_exactly_three_seconds(database: NetworkDatabase) -> None:
    data = decoded_stream(database, FAULT_SESSION, THERMAL_DATA_ID)
    before = [seconds for seconds, _ in data if seconds < SILENT_START_S]
    after = [seconds for seconds, _ in data if seconds >= SILENT_START_S + SILENT_SECONDS]
    during = [
        seconds
        for seconds, _ in data
        if SILENT_START_S <= seconds < SILENT_START_S + SILENT_SECONDS
    ]

    assert during == []
    assert round(after[0] - before[-1], 1) == pytest.approx(SILENT_SECONDS, abs=0.2)

    heartbeats = [seconds for seconds, _ in decoded_stream(database, FAULT_SESSION, 0x281)]
    assert [
        seconds
        for seconds in heartbeats
        if SILENT_START_S <= seconds < SILENT_START_S + SILENT_SECONDS
    ] == []


def test_silence_resets_neither_counter_nor_uptime(database: NetworkDatabase) -> None:
    data = decoded_stream(database, FAULT_SESSION, THERMAL_DATA_ID)
    last_before = next(signals for seconds, signals in reversed(data) if seconds < SILENT_START_S)
    first_after = next(
        signals for seconds, signals in data if seconds >= SILENT_START_S + SILENT_SECONDS
    )
    assert int(first_after["SequenceCounter"]) == (int(last_before["SequenceCounter"]) + 1) % 256

    heartbeats = decoded_stream(database, FAULT_SESSION, THERMAL_HEARTBEAT_ID)
    before = next(signals for seconds, signals in reversed(heartbeats) if seconds < SILENT_START_S)
    after = next(
        signals for seconds, signals in heartbeats if seconds >= SILENT_START_S + SILENT_SECONDS
    )
    assert int(after["Uptime"]) > int(before["Uptime"])
    assert str(after["ResetReason"]) == str(before["ResetReason"]) == "PowerOn"


def test_skip_injection_creates_one_gap_of_four_values(database: NetworkDatabase) -> None:
    data = decoded_stream(database, FAULT_SESSION, THERMAL_DATA_ID)
    before = next(signals for seconds, signals in reversed(data) if seconds < SKIP_AT_S)
    after = next(signals for seconds, signals in data if seconds >= SKIP_AT_S)

    previous = int(before["SequenceCounter"])
    observed = int(after["SequenceCounter"])
    assert (observed - previous - 1) % 256 == SKIP_COUNT

    missing = [(previous + 1 + offset) % 256 for offset in range(SKIP_COUNT)]
    seen = {int(signals["SequenceCounter"]) for seconds, signals in data if seconds >= SKIP_AT_S}
    assert not set(missing) & seen

    capture = generate_capture(database, FAULT_SESSION)
    gaps = [
        event
        for event in analyze_frames(database, capture.frames).events
        if event.kind is EventKind.SEQUENCE_GAP
    ]
    assert len(gaps) == 1
    assert gaps[0].details["skipped_values"] == SKIP_COUNT
    assert gaps[0].arbitration_id == THERMAL_DATA_ID
    assert gaps[0].node == "ThermalController"


def test_power_monitor_has_no_unexplained_gaps(database: NetworkDatabase) -> None:
    capture = generate_capture(database, FAULT_SESSION)
    power_frames = [frame for frame in capture.frames if frame.arbitration_id == POWER_DATA_ID]
    assert len(power_frames) == int(FAULT_SESSION.duration_s / DATA_PERIOD_S)

    events = analyze_frames(database, capture.frames).events
    assert [event for event in events if event.node == "PowerMonitor"] == []


def test_fault_session_finding_sequence(database: NetworkDatabase) -> None:
    assert findings(database, FAULT_SESSION) == [
        "range_violation",
        "range_recovered",
        "data_message_stale",
        "heartbeat_timeout",
        "node_silent",
        "message_recovered",
        "node_recovered",
        "message_recovered",
        "sequence_gap",
    ]


def test_fault_session_contains_no_restart(database: NetworkDatabase) -> None:
    assert "node_restart" not in findings(database, FAULT_SESSION)
    assert FAULT_SESSION.injection(InjectionKind.RESTART) is None


def test_restart_session_reports_a_software_reset(database: NetworkDatabase) -> None:
    capture = generate_capture(database, RESTART_SESSION)
    restarts = [
        event
        for event in analyze_frames(database, capture.frames).events
        if event.kind is EventKind.NODE_RESTART
    ]
    assert len(restarts) == 1
    assert restarts[0].details["reset_reason"] == "Software"
    assert restarts[0].details["reported_uptime_s"] == 0
    assert restarts[0].details["previous_uptime_s"] > 0
    assert restarts[0].node == "ThermalController"


def test_restart_session_resets_the_counter(database: NetworkDatabase) -> None:
    data = decoded_stream(database, RESTART_SESSION, THERMAL_DATA_ID)
    restart = RESTART_SESSION.injections[0]
    before = next(signals for seconds, signals in reversed(data) if seconds < restart.start_s)
    after = next(signals for seconds, signals in data if seconds >= restart.end_s)
    assert int(before["SequenceCounter"]) > 0
    assert int(after["SequenceCounter"]) == 0


def test_every_finding_names_the_right_node_and_identifier(database: NetworkDatabase) -> None:
    thermal_ids = {THERMAL_DATA_ID, THERMAL_HEARTBEAT_ID, None}
    for scenario in (FAULT_SESSION, RESTART_SESSION):
        capture = generate_capture(database, scenario)
        for event in analyze_frames(database, capture.frames).events:
            assert event.node == "ThermalController"
            assert event.arbitration_id in thermal_ids


def test_ground_truth_matches_the_scenario_definition(database: NetworkDatabase) -> None:
    for scenario in ALL_SCENARIOS:
        document = generate_capture(database, scenario).ground_truth_document()
        assert document["data_origin"] == "synthetic"
        assert document["deterministic"] is True
        assert document["seed"] == scenario.seed
        assert len(document["injected_events"]) == len(scenario.injections)
        for entry, injection in zip(document["injected_events"], scenario.injections, strict=True):
            assert entry["kind"] == injection.kind.value
            assert entry["serial_command"] == injection.command
            assert entry["node"] == injection.node
            assert entry["start_seconds"] == injection.start_s
            assert entry["end_seconds"] == injection.end_s


def test_ground_truth_predicts_the_analyzer_findings(database: NetworkDatabase) -> None:
    for scenario in ALL_SCENARIOS:
        expected: set[str] = set()
        for injection in scenario.injections:
            expected.update(injection.expected_findings)
        assert set(findings(database, scenario)) == expected


def test_committed_captures_match_a_fresh_generation(database: NetworkDatabase) -> None:
    for scenario in ALL_SCENARIOS:
        capture = generate_capture(database, scenario)
        committed = read_log(SYNTHETIC_DIR / f"{scenario.name}.log")
        assert [frame.arbitration_id for frame in committed.frames] == [
            frame.arbitration_id for frame in capture.frames
        ]
        assert [frame.data for frame in committed.frames] == [
            frame.data for frame in capture.frames
        ]
        truth = json.loads(
            Path(SYNTHETIC_DIR / f"{scenario.name}_ground_truth.json").read_text(encoding="utf-8")
        )
        assert truth == capture.ground_truth_document()
