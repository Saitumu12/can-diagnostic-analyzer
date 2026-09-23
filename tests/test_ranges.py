from __future__ import annotations

import pytest

from can_diag.config import AnalyzerConfig
from can_diag.decoder import NetworkDatabase
from can_diag.models import EventKind
from can_diag.monitor import analyze_frames
from tests.conftest import (
    POWER_DATA_ID,
    THERMAL_DATA_ID,
    data_frame,
    data_payload,
    frame,
    healthy_stream,
    kinds,
)


def test_values_inside_the_declared_range_raise_no_events(database: NetworkDatabase) -> None:
    frames = [
        data_frame(0.0, THERMAL_DATA_ID, 0, temperature_deci_degc=-400, voltage_centivolts=900),
        data_frame(0.1, THERMAL_DATA_ID, 1, temperature_deci_degc=1500, voltage_centivolts=1600),
    ]
    assert analyze_frames(database, frames).events == []


def test_saturated_temperature_is_reported_once_per_episode(database: NetworkDatabase) -> None:
    frames = [
        data_frame(0.0, THERMAL_DATA_ID, 0),
        frame(0.1, THERMAL_DATA_ID, data_payload(0x7FFF, 1240, 2, 1, 1)),
        frame(0.2, THERMAL_DATA_ID, data_payload(0x7FFF, 1240, 2, 2, 1)),
        frame(0.3, THERMAL_DATA_ID, data_payload(0x7FFF, 1240, 2, 3, 1)),
    ]
    events = analyze_frames(database, frames).events
    assert kinds(events) == ["range_violation"]

    violation = events[0]
    assert violation.node == "ThermalController"
    assert violation.arbitration_id == THERMAL_DATA_ID
    assert violation.details["signal"] == "ModuleTemperature"
    assert violation.details["value"] == pytest.approx(3276.7)
    assert violation.details["maximum"] == 150.0
    assert "Degraded" in violation.evidence
    assert "SensorSaturation" in violation.evidence
    assert "plausibility" in violation.limitation


def test_returning_to_range_records_a_recovery_with_duration(database: NetworkDatabase) -> None:
    frames = [
        frame(0.0, THERMAL_DATA_ID, data_payload(0x7FFF, 1240, 2, 0, 1)),
        frame(0.1, THERMAL_DATA_ID, data_payload(0x7FFF, 1240, 2, 1, 1)),
        data_frame(0.2, THERMAL_DATA_ID, 2),
    ]
    events = [
        event
        for event in analyze_frames(database, frames).events
        if event.kind in {EventKind.RANGE_VIOLATION, EventKind.RANGE_RECOVERED}
    ]
    assert kinds(events) == ["range_violation", "range_recovered"]
    assert events[1].recovery_seconds == pytest.approx(0.2)
    assert events[1].arbitration_id == THERMAL_DATA_ID


def test_voltage_below_the_declared_minimum_is_detected(database: NetworkDatabase) -> None:
    frames = [
        data_frame(0.0, POWER_DATA_ID, 0),
        data_frame(0.1, POWER_DATA_ID, 1, voltage_centivolts=810),
    ]
    events = analyze_frames(database, frames).events
    assert kinds(events) == ["range_violation"]
    assert events[0].details["signal"] == "SupplyVoltage"
    assert events[0].details["value"] == pytest.approx(8.1)
    assert events[0].details["minimum"] == 9.0


def test_range_checking_can_be_disabled(database: NetworkDatabase) -> None:
    frames = [frame(0.0, THERMAL_DATA_ID, data_payload(0x7FFF, 1240, 2, 0, 1))]
    disabled = AnalyzerConfig(check_ranges=False)
    assert analyze_frames(database, frames, disabled).events == []


def test_healthy_traffic_produces_no_range_events(database: NetworkDatabase) -> None:
    events = analyze_frames(database, healthy_stream(5.0)).events
    assert [event for event in events if event.kind is EventKind.RANGE_VIOLATION] == []
