from __future__ import annotations

import pytest

from can_diag.config import AnalyzerConfig
from can_diag.decoder import NetworkDatabase
from can_diag.models import EventKind
from can_diag.monitor import DiagnosticMonitor, analyze_frames
from tests.conftest import (
    POWER_DATA_ID,
    POWER_HEARTBEAT_ID,
    THERMAL_DATA_ID,
    THERMAL_HEARTBEAT_ID,
    data_frame,
    healthy_stream,
    heartbeat_frame,
)


def outage_stream(outage_start: float, outage_end: float, total: float) -> list:
    frames = []
    ticks = int(round(total / 0.1))
    for index in range(ticks):
        moment = index * 0.1
        frames.append(data_frame(moment, POWER_DATA_ID, index % 256))
        if index % 10 == 0:
            frames.append(heartbeat_frame(moment + 0.05, POWER_HEARTBEAT_ID, int(moment)))
        if not (outage_start <= moment < outage_end):
            frames.append(data_frame(moment + 0.02, THERMAL_DATA_ID, index % 256))
            if index % 10 == 0:
                frames.append(heartbeat_frame(moment + 0.06, THERMAL_HEARTBEAT_ID, int(moment)))
    return sorted(frames, key=lambda item: item.timestamp)


def test_healthy_traffic_raises_no_freshness_events(database: NetworkDatabase) -> None:
    assert analyze_frames(database, healthy_stream(10.0)).events == []


def test_missing_data_message_is_detected_at_the_configured_threshold(
    database: NetworkDatabase,
) -> None:
    frames = [
        data_frame(0.0, POWER_DATA_ID, 0),
        data_frame(0.1, POWER_DATA_ID, 1),
        data_frame(1.5, POWER_DATA_ID, 2),
    ]
    events = analyze_frames(database, frames).events
    stale = [event for event in events if event.kind is EventKind.DATA_MESSAGE_STALE]
    assert len(stale) == 1
    assert stale[0].message_name == "POWER_DATA"
    assert stale[0].arbitration_id == POWER_DATA_ID
    assert stale[0].node == "PowerMonitor"
    assert stale[0].timestamp == pytest.approx(0.45)
    assert stale[0].details["threshold_ms"] == 350.0
    assert "cannot distinguish" in stale[0].limitation


def test_missing_heartbeat_is_detected(database: NetworkDatabase) -> None:
    frames = [
        heartbeat_frame(0.0, POWER_HEARTBEAT_ID, 0),
        heartbeat_frame(1.0, POWER_HEARTBEAT_ID, 1),
        heartbeat_frame(6.0, POWER_HEARTBEAT_ID, 6),
    ]
    events = analyze_frames(database, frames).events
    timeouts = [event for event in events if event.kind is EventKind.HEARTBEAT_TIMEOUT]
    assert len(timeouts) == 1
    assert timeouts[0].message_name == "POWER_HEARTBEAT"
    assert timeouts[0].timestamp == pytest.approx(3.5)
    assert timeouts[0].details["threshold_ms"] == 2500.0


def test_thresholds_are_configurable(database: NetworkDatabase) -> None:
    frames = [data_frame(0.0, POWER_DATA_ID, 0), data_frame(0.3, POWER_DATA_ID, 1)]
    assert analyze_frames(database, frames).events == []

    strict = AnalyzerConfig(data_stale_ms=150)
    events = analyze_frames(database, frames, strict).events
    stale = [event for event in events if event.kind is EventKind.DATA_MESSAGE_STALE]
    assert len(stale) == 1
    assert stale[0].timestamp == pytest.approx(0.15)


def test_per_message_threshold_override(database: NetworkDatabase) -> None:
    frames = [data_frame(0.0, THERMAL_DATA_ID, 0), data_frame(0.3, THERMAL_DATA_ID, 1)]
    config = AnalyzerConfig(message_stale_ms={"THERMAL_DATA": 100})
    events = analyze_frames(database, frames, config).events
    stale = [event for event in events if event.kind is EventKind.DATA_MESSAGE_STALE]
    assert len(stale) == 1
    assert stale[0].message_name == "THERMAL_DATA"


def test_node_outage_reports_both_identifiers_and_one_node_event(
    database: NetworkDatabase,
) -> None:
    monitor = analyze_frames(database, outage_stream(30.0, 33.0, 40.0))
    events = monitor.events

    stale_ids = {
        event.arbitration_id
        for event in events
        if event.kind in {EventKind.DATA_MESSAGE_STALE, EventKind.HEARTBEAT_TIMEOUT}
    }
    assert stale_ids == {THERMAL_DATA_ID, THERMAL_HEARTBEAT_ID}

    silent = [event for event in events if event.kind is EventKind.NODE_SILENT]
    assert len(silent) == 1
    assert silent[0].node == "ThermalController"
    assert set(silent[0].details["message_names"]) == {"THERMAL_DATA", "THERMAL_HEARTBEAT"}
    assert silent[0].details["other_active_nodes"] == ["PowerMonitor"]
    assert "localizes the communication interruption to ThermalController" in silent[0].limitation
    assert "PowerMonitor" in silent[0].evidence


def test_other_node_stays_healthy_during_the_outage(database: NetworkDatabase) -> None:
    events = analyze_frames(database, outage_stream(30.0, 33.0, 40.0)).events
    power_events = [event for event in events if event.node == "PowerMonitor"]
    assert power_events == []


def test_recovery_is_recorded_with_a_duration(database: NetworkDatabase) -> None:
    events = analyze_frames(database, outage_stream(30.0, 33.0, 40.0)).events
    recovered = [event for event in events if event.kind is EventKind.MESSAGE_RECOVERED]
    assert {event.message_name for event in recovered} == {
        "THERMAL_DATA",
        "THERMAL_HEARTBEAT",
    }
    assert all(event.recovery_seconds is not None for event in recovered)

    node_recovered = [event for event in events if event.kind is EventKind.NODE_RECOVERED]
    assert len(node_recovered) == 1
    assert node_recovered[0].node == "ThermalController"
    assert node_recovered[0].recovery_seconds is not None


def test_timeouts_are_not_raised_before_the_first_frame(database: NetworkDatabase) -> None:
    monitor = DiagnosticMonitor(database)
    assert monitor.finalize(until=100.0) == []


def test_unknown_identifier_is_reported_once(database: NetworkDatabase) -> None:
    frames = [
        data_frame(0.0, POWER_DATA_ID, 0),
        data_frame(0.05, 0x400, 0),
        data_frame(0.15, 0x400, 1),
    ]
    events = analyze_frames(database, frames).events
    unknown = [event for event in events if event.kind is EventKind.UNKNOWN_MESSAGE_ID]
    assert len(unknown) == 1
    assert unknown[0].arbitration_id == 0x400
