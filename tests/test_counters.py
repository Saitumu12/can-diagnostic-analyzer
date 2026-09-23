from __future__ import annotations

from can_diag.config import AnalyzerConfig
from can_diag.decoder import NetworkDatabase
from can_diag.models import EventKind
from can_diag.monitor import analyze_frames
from tests.conftest import (
    POWER_DATA_ID,
    THERMAL_DATA_ID,
    THERMAL_HEARTBEAT_ID,
    data_frame,
    heartbeat_frame,
)


def sequence_gaps(database: NetworkDatabase, frames, config: AnalyzerConfig | None = None):
    return [
        event
        for event in analyze_frames(database, frames, config).events
        if event.kind is EventKind.SEQUENCE_GAP
    ]


def continuous(count: int, start_counter: int = 0, step: float = 0.1) -> list:
    return [
        data_frame(index * step, POWER_DATA_ID, (start_counter + index) % 256)
        for index in range(count)
    ]


def test_continuous_counter_raises_no_event(database: NetworkDatabase) -> None:
    assert sequence_gaps(database, continuous(20)) == []


def test_one_omitted_frame_produces_exactly_one_gap(database: NetworkDatabase) -> None:
    frames = [
        data_frame(0.0, POWER_DATA_ID, 10),
        data_frame(0.1, POWER_DATA_ID, 11),
        data_frame(0.3, POWER_DATA_ID, 13),
        data_frame(0.4, POWER_DATA_ID, 14),
        data_frame(0.5, POWER_DATA_ID, 15),
    ]
    gaps = sequence_gaps(database, frames)
    assert len(gaps) == 1
    assert gaps[0].details["previous_counter"] == 11
    assert gaps[0].details["observed_counter"] == 13
    assert gaps[0].details["expected_counter"] == 12
    assert gaps[0].details["skipped_values"] == 1
    assert gaps[0].node == "PowerMonitor"
    assert gaps[0].arbitration_id == POWER_DATA_ID
    assert "does not" in gaps[0].limitation


def test_wraparound_from_255_to_0_is_not_an_event(database: NetworkDatabase) -> None:
    frames = [
        data_frame(0.0, POWER_DATA_ID, 253),
        data_frame(0.1, POWER_DATA_ID, 254),
        data_frame(0.2, POWER_DATA_ID, 255),
        data_frame(0.3, POWER_DATA_ID, 0),
        data_frame(0.4, POWER_DATA_ID, 1),
    ]
    assert sequence_gaps(database, frames) == []


def test_gap_across_the_wrap_boundary_is_measured_forward(database: NetworkDatabase) -> None:
    frames = [
        data_frame(0.0, POWER_DATA_ID, 254),
        data_frame(0.1, POWER_DATA_ID, 1),
    ]
    gaps = sequence_gaps(database, frames)
    assert len(gaps) == 1
    assert gaps[0].details["skipped_values"] == 2


def test_counters_are_tracked_per_message(database: NetworkDatabase) -> None:
    frames = [
        data_frame(0.0, POWER_DATA_ID, 0),
        data_frame(0.02, THERMAL_DATA_ID, 200),
        data_frame(0.1, POWER_DATA_ID, 1),
        data_frame(0.12, THERMAL_DATA_ID, 201),
    ]
    assert sequence_gaps(database, frames) == []


def test_restart_does_not_produce_a_large_false_gap(database: NetworkDatabase) -> None:
    frames = [
        data_frame(0.0, THERMAL_DATA_ID, 198),
        heartbeat_frame(0.05, THERMAL_HEARTBEAT_ID, 20),
        data_frame(0.1, THERMAL_DATA_ID, 199),
        heartbeat_frame(0.15, THERMAL_HEARTBEAT_ID, 0, reset_reason=1),
        data_frame(0.2, THERMAL_DATA_ID, 0),
        data_frame(0.3, THERMAL_DATA_ID, 1),
    ]
    events = analyze_frames(database, frames).events
    assert sequence_gaps(database, frames) == []

    restarts = [event for event in events if event.kind is EventKind.NODE_RESTART]
    assert len(restarts) == 1
    assert restarts[0].details["previous_uptime_s"] == 20
    assert restarts[0].details["reported_uptime_s"] == 0
    assert restarts[0].details["reset_reason"] == "Watchdog"


def test_gap_after_a_recovered_outage_is_suppressed(database: NetworkDatabase) -> None:
    frames = [
        data_frame(0.0, THERMAL_DATA_ID, 100),
        data_frame(0.1, THERMAL_DATA_ID, 101),
        data_frame(5.0, THERMAL_DATA_ID, 0),
        data_frame(5.1, THERMAL_DATA_ID, 1),
    ]
    assert sequence_gaps(database, frames) == []


def test_gap_detection_can_be_disabled(database: NetworkDatabase) -> None:
    frames = [data_frame(0.0, POWER_DATA_ID, 10), data_frame(0.1, POWER_DATA_ID, 20)]
    assert sequence_gaps(database, frames, AnalyzerConfig(check_sequence=False)) == []


def test_backwards_counter_jump_is_measured_as_a_forward_distance(
    database: NetworkDatabase,
) -> None:
    frames = [data_frame(0.0, POWER_DATA_ID, 200), data_frame(0.1, POWER_DATA_ID, 5)]
    gaps = sequence_gaps(database, frames)
    assert len(gaps) == 1
    assert gaps[0].details["skipped_values"] == 60
    assert "skipping 60 value(s)" in gaps[0].evidence


def test_a_four_value_skip_within_one_cycle_is_still_reported(
    database: NetworkDatabase,
) -> None:
    frames = [
        data_frame(0.0, THERMAL_DATA_ID, 10),
        data_frame(0.1, THERMAL_DATA_ID, 11),
        data_frame(0.2, THERMAL_DATA_ID, 16),
    ]
    gaps = sequence_gaps(database, frames)
    assert len(gaps) == 1
    assert gaps[0].details["skipped_values"] == 4
    assert gaps[0].details["expected_counter"] == 12
