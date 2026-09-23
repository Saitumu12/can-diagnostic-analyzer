from __future__ import annotations

import re
from pathlib import Path

import pytest

from can_diag.decoder import DecodeError, NetworkDatabase
from can_diag.scenario import (
    ALL_SCENARIOS,
    RANGE_SECONDS,
    SATURATED_TEMPERATURE_RAW,
    SILENT_SECONDS,
    SKIP_COUNT,
)
from tests.conftest import (
    COMPONENT_DIR,
    FIRMWARE_HEADER,
    POWER_DATA_ID,
    POWER_HEARTBEAT_ID,
    THERMAL_DATA_ID,
    THERMAL_HEARTBEAT_ID,
)

EXPECTED_MESSAGES = {
    POWER_DATA_ID: ("POWER_DATA", "PowerMonitor", 100),
    POWER_HEARTBEAT_ID: ("POWER_HEARTBEAT", "PowerMonitor", 1000),
    THERMAL_DATA_ID: ("THERMAL_DATA", "ThermalController", 100),
    THERMAL_HEARTBEAT_ID: ("THERMAL_HEARTBEAT", "ThermalController", 1000),
}

THERMAL_FIRMWARE = (
    FIRMWARE_HEADER.parents[2].parent / "thermal_controller" / "main" / "thermal_controller_main.c"
)

DEFINE_PATTERN = re.compile(
    r"^#define\s+(?P<name>[A-Z0-9_]+)\s+(?P<value>\(?-?[0-9xA-Fa-f]+\)?)\s*$"
)


def firmware_defines() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in FIRMWARE_HEADER.read_text(encoding="utf-8").splitlines():
        match = DEFINE_PATTERN.match(line.strip())
        if match is None:
            continue
        raw = match.group("value").strip("()")
        try:
            values[match.group("name")] = int(raw, 0)
        except ValueError:
            continue
    return values


def test_database_loads(database: NetworkDatabase) -> None:
    assert len(database.messages()) == 4


def test_missing_database_reports_a_useful_error(tmp_path: Path) -> None:
    with pytest.raises(DecodeError, match="DBC file not found"):
        NetworkDatabase.load(tmp_path / "absent.dbc")


def test_message_identity_length_sender_and_cycle_time(database: NetworkDatabase) -> None:
    by_id = {info.frame_id: info for info in database.messages()}
    assert set(by_id) == set(EXPECTED_MESSAGES)
    for frame_id, (name, sender, cycle_time) in EXPECTED_MESSAGES.items():
        info = by_id[frame_id]
        assert info.name == name
        assert info.sender == sender
        assert info.length == 8
        assert info.cycle_time_ms == cycle_time


def test_heartbeat_classification(database: NetworkDatabase) -> None:
    heartbeats = {info.name for info in database.messages() if info.is_heartbeat}
    assert heartbeats == {"POWER_HEARTBEAT", "THERMAL_HEARTBEAT"}


def test_data_signal_layout_byte_order_and_scaling(database: NetworkDatabase) -> None:
    message = database.database.get_message_by_frame_id(THERMAL_DATA_ID)
    layout = {signal.name: signal for signal in message.signals}

    temperature = layout["ModuleTemperature"]
    assert temperature.start == 0
    assert temperature.length == 16
    assert temperature.byte_order == "little_endian"
    assert temperature.is_signed is True
    assert temperature.scale == pytest.approx(0.1)
    assert temperature.offset == 0
    assert temperature.unit == "degC"
    assert (temperature.minimum, temperature.maximum) == (-40, 150)

    voltage = layout["SupplyVoltage"]
    assert voltage.start == 16
    assert voltage.length == 16
    assert voltage.is_signed is False
    assert voltage.scale == pytest.approx(0.01)
    assert voltage.unit == "V"
    assert (voltage.minimum, voltage.maximum) == (9, 16)

    assert layout["NodeState"].start == 32
    assert layout["SequenceCounter"].start == 40
    assert (layout["SequenceCounter"].minimum, layout["SequenceCounter"].maximum) == (0, 255)
    assert layout["FaultCode"].start == 48
    assert layout["Reserved"].start == 56


def test_heartbeat_signal_layout(database: NetworkDatabase) -> None:
    message = database.database.get_message_by_frame_id(THERMAL_HEARTBEAT_ID)
    layout = {signal.name: signal for signal in message.signals}
    assert layout["Uptime"].start == 0
    assert layout["Uptime"].length == 32
    assert layout["Uptime"].is_signed is False
    assert layout["Uptime"].unit == "s"
    assert layout["NodeState"].start == 32
    assert layout["ResetReason"].start == 40
    assert layout["Reserved"].start == 48


def test_value_enumerations(database: NetworkDatabase) -> None:
    data = database.database.get_message_by_frame_id(THERMAL_DATA_ID)
    node_state = {
        int(key): str(value) for key, value in data.get_signal_by_name("NodeState").choices.items()
    }
    assert node_state == {0: "Booting", 1: "Normal", 2: "Degraded", 3: "Fault"}

    fault_code = {
        int(key): str(value) for key, value in data.get_signal_by_name("FaultCode").choices.items()
    }
    assert fault_code == {0: "None", 1: "SensorSaturation"}

    heartbeat = database.database.get_message_by_frame_id(THERMAL_HEARTBEAT_ID)
    reset_reason = {
        int(key): str(value)
        for key, value in heartbeat.get_signal_by_name("ResetReason").choices.items()
    }
    assert reset_reason == {0: "None", 1: "Watchdog", 2: "PowerOn", 3: "Software"}


def test_nodes_and_message_ownership(database: NetworkDatabase) -> None:
    assert database.nodes() == ["PowerMonitor", "ThermalController"]
    thermal = {info.name for info in database.messages_for_node("ThermalController")}
    assert thermal == {"THERMAL_DATA", "THERMAL_HEARTBEAT"}


def test_firmware_constants_match_the_database(database: NetworkDatabase) -> None:
    defines = firmware_defines()
    by_name = {info.name: info for info in database.messages()}

    for message_name, info in by_name.items():
        macro = f"CAN_ID_{message_name}"
        assert macro in defines, f"firmware header is missing {macro}"
        assert defines[macro] == info.frame_id
        expected_period = (
            defines["CAN_PROTOCOL_HEARTBEAT_PERIOD_MS"]
            if info.is_heartbeat
            else defines["CAN_PROTOCOL_DATA_PERIOD_MS"]
        )
        assert info.cycle_time_ms == expected_period
        assert info.length == defines["CAN_PROTOCOL_DLC"]

    temperature = database.database.get_message_by_frame_id(THERMAL_DATA_ID).get_signal_by_name(
        "ModuleTemperature"
    )
    assert defines["CAN_PROTOCOL_TEMPERATURE_MIN_DECI_DEGC"] == round(
        float(temperature.minimum) / float(temperature.scale)
    )
    assert defines["CAN_PROTOCOL_TEMPERATURE_MAX_DECI_DEGC"] == round(
        float(temperature.maximum) / float(temperature.scale)
    )

    voltage = database.database.get_message_by_frame_id(THERMAL_DATA_ID).get_signal_by_name(
        "SupplyVoltage"
    )
    assert defines["CAN_PROTOCOL_VOLTAGE_MIN_CENTIVOLTS"] == round(
        float(voltage.minimum) / float(voltage.scale)
    )
    assert defines["CAN_PROTOCOL_VOLTAGE_MAX_CENTIVOLTS"] == round(
        float(voltage.maximum) / float(voltage.scale)
    )

    assert defines["CAN_PROTOCOL_BITRATE_BPS"] == 500000
    assert defines["CAN_PROTOCOL_SATURATED_TEMPERATURE_RAW"] == 0x7FFF


def test_scenario_commands_are_implemented_by_the_thermal_firmware() -> None:
    source = THERMAL_FIRMWARE.read_text(encoding="utf-8")
    accepted = set(re.findall(r'strcmp\(verb, "([a-z]+)"\) == 0', source))
    assert accepted == {"normal", "range", "silent", "skip", "restart"}

    for scenario in ALL_SCENARIOS:
        for injection in scenario.injections:
            verb = injection.command.split()[0]
            assert verb in accepted, f"{scenario.name} uses a verb the firmware does not accept"


def test_scenario_constants_stay_inside_the_firmware_limits() -> None:
    source = THERMAL_FIRMWARE.read_text(encoding="utf-8")
    limits = {
        name: int(value)
        for name, value in re.findall(r"#define\s+(THERMAL_MAX_\w+)\s+(\d+)", source)
    }

    assert limits["THERMAL_MAX_COUNTER_SKIP"] >= SKIP_COUNT
    assert limits["THERMAL_MAX_INJECTION_SECONDS"] >= RANGE_SECONDS
    assert limits["THERMAL_MAX_INJECTION_SECONDS"] >= SILENT_SECONDS


def test_saturated_temperature_agrees_across_scenario_firmware_and_database(
    database: NetworkDatabase,
) -> None:
    defines = firmware_defines()
    assert defines["CAN_PROTOCOL_SATURATED_TEMPERATURE_RAW"] == SATURATED_TEMPERATURE_RAW

    temperature = database.database.get_message_by_frame_id(THERMAL_DATA_ID).get_signal_by_name(
        "ModuleTemperature"
    )
    saturated = SATURATED_TEMPERATURE_RAW * float(temperature.scale)
    assert saturated > float(temperature.maximum)


def test_restart_scenario_uses_the_software_reset_enumeration(database: NetworkDatabase) -> None:
    source = THERMAL_FIRMWARE.read_text(encoding="utf-8")
    assert "esp_restart()" in source

    bus_source = (COMPONENT_DIR / "can_bus.c").read_text(encoding="utf-8")
    assert "case ESP_RST_SW:" in bus_source
    assert re.search(r"case ESP_RST_SW:\s*\n\s*return CAN_RESET_REASON_SOFTWARE;", bus_source)

    heartbeat = database.database.get_message_by_frame_id(THERMAL_HEARTBEAT_ID)
    choices = {
        int(key): str(value)
        for key, value in heartbeat.get_signal_by_name("ResetReason").choices.items()
    }
    assert choices[3] == "Software"
