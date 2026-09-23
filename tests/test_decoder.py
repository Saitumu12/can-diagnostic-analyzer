from __future__ import annotations

import pytest

from can_diag.decoder import NetworkDatabase
from can_diag.models import DecodedFrame, UnknownFrame
from tests.conftest import (
    GOLDEN_THERMAL_PAYLOAD,
    POWER_DATA_ID,
    THERMAL_DATA_ID,
    THERMAL_HEARTBEAT_ID,
    data_frame,
    data_payload,
    frame,
    heartbeat_frame,
)


def decode(database: NetworkDatabase, can_frame) -> DecodedFrame:
    decoded = database.decode(can_frame)
    assert isinstance(decoded, DecodedFrame)
    return decoded


def test_golden_vector(database: NetworkDatabase) -> None:
    decoded = decode(database, frame(0.0, THERMAL_DATA_ID, bytes.fromhex(GOLDEN_THERMAL_PAYLOAD)))
    assert decoded.message_name == "THERMAL_DATA"
    assert decoded.sender == "ThermalController"
    values = decoded.signal_map()
    assert values["ModuleTemperature"] == pytest.approx(25.0)
    assert values["SupplyVoltage"] == pytest.approx(12.40)
    assert str(values["NodeState"]) == "Normal"
    assert values["SequenceCounter"] == 7
    assert str(values["FaultCode"]) == "None"


def test_signed_little_endian_temperature(database: NetworkDatabase) -> None:
    decoded = decode(database, data_frame(0.0, THERMAL_DATA_ID, 0, temperature_deci_degc=-155))
    assert decoded.signal_map()["ModuleTemperature"] == pytest.approx(-15.5)


def test_maximum_signed_raw_value_decodes_as_positive(database: NetworkDatabase) -> None:
    decoded = decode(database, frame(0.0, THERMAL_DATA_ID, data_payload(0x7FFF, 1240, 2, 3, 1)))
    assert decoded.signal_map()["ModuleTemperature"] == pytest.approx(3276.7)
    assert str(decoded.signal_map()["NodeState"]) == "Degraded"
    assert str(decoded.signal_map()["FaultCode"]) == "SensorSaturation"


def test_byte_order_is_little_endian(database: NetworkDatabase) -> None:
    decoded = decode(database, frame(0.0, THERMAL_DATA_ID, bytes.fromhex("0001000100000000")))
    assert decoded.signal_map()["ModuleTemperature"] == pytest.approx(25.6)
    assert decoded.signal_map()["SupplyVoltage"] == pytest.approx(2.56)


def test_voltage_scaling(database: NetworkDatabase) -> None:
    decoded = decode(database, data_frame(0.0, POWER_DATA_ID, 0, voltage_centivolts=1603))
    assert decoded.signal_map()["SupplyVoltage"] == pytest.approx(16.03)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, "Booting"), (1, "Normal"), (2, "Degraded"), (3, "Fault")],
)
def test_node_state_enumeration(database: NetworkDatabase, raw: int, expected: str) -> None:
    decoded = decode(database, data_frame(0.0, POWER_DATA_ID, 0, node_state=raw))
    assert str(decoded.signal_map()["NodeState"]) == expected


@pytest.mark.parametrize(("raw", "expected"), [(0, "None"), (1, "SensorSaturation")])
def test_fault_code_enumeration(database: NetworkDatabase, raw: int, expected: str) -> None:
    decoded = decode(database, data_frame(0.0, POWER_DATA_ID, 0, fault_code=raw))
    assert str(decoded.signal_map()["FaultCode"]) == expected


@pytest.mark.parametrize(
    ("raw", "expected"), [(0, "None"), (1, "Watchdog"), (2, "PowerOn"), (3, "Software")]
)
def test_reset_reason_enumeration(database: NetworkDatabase, raw: int, expected: str) -> None:
    decoded = decode(database, heartbeat_frame(0.0, THERMAL_HEARTBEAT_ID, 42, reset_reason=raw))
    assert str(decoded.signal_map()["ResetReason"]) == expected
    assert decoded.signal_map()["Uptime"] == 42
    assert decoded.is_heartbeat is True


def test_sender_association(database: NetworkDatabase) -> None:
    assert decode(database, data_frame(0.0, POWER_DATA_ID, 0)).sender == "PowerMonitor"
    assert decode(database, data_frame(0.0, THERMAL_DATA_ID, 0)).sender == "ThermalController"


def test_unknown_identifier_does_not_raise(database: NetworkDatabase) -> None:
    decoded = database.decode(frame(0.0, 0x7FF, bytes.fromhex("0102030405060708")))
    assert isinstance(decoded, UnknownFrame)
    assert decoded.frame.hex_data == "0102030405060708"


def test_signal_units_and_bounds_are_exposed(database: NetworkDatabase) -> None:
    decoded = decode(database, data_frame(0.0, THERMAL_DATA_ID, 0))
    temperature = decoded.signal("ModuleTemperature")
    assert temperature is not None
    assert temperature.unit == "degC"
    assert (temperature.minimum, temperature.maximum) == (-40.0, 150.0)
    assert temperature.format_value() == "25.0 degC"
