from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from can_diag.decoder import NetworkDatabase
from can_diag.models import CanFrame, DecodedFrame
from can_diag.scenario import SATURATED_TEMPERATURE_RAW
from tests.conftest import COMPONENT_DIR

STRICT_FLAGS = [
    "-std=c11",
    "-O2",
    "-Wall",
    "-Wextra",
    "-Werror",
    "-Wconversion",
    "-Wshadow",
    "-Wpedantic",
]
CANDIDATE_COMPILERS = ("gcc", "cc", "clang", "x86_64-w64-mingw32-gcc")

TEMPERATURE_SCALE = 0.1
VOLTAGE_SCALE = 0.01
NODE_STATE_NAMES = {0: "Booting", 1: "Normal", 2: "Degraded", 3: "Fault"}
FAULT_CODE_NAMES = {0: "None", 1: "SensorSaturation"}
RESET_REASON_NAMES = {0: "None", 1: "Watchdog", 2: "PowerOn", 3: "Software"}


def find_compiler() -> str | None:
    configured = os.environ.get("CC")
    if configured and shutil.which(configured):
        return configured
    for name in CANDIDATE_COMPILERS:
        found = shutil.which(name)
        if found:
            return found
    return None


@pytest.fixture(scope="session")
def c_vectors(tmp_path_factory: pytest.TempPathFactory) -> list[dict[str, str]]:
    compiler = find_compiler()
    if compiler is None:
        pytest.skip("no C compiler on PATH; set CC to run the shared protocol cross-check")

    build_dir = tmp_path_factory.mktemp("can_protocol")
    binary = build_dir / ("protocol_vectors.exe" if os.name == "nt" else "protocol_vectors")
    sources = [
        str(COMPONENT_DIR / "can_protocol.c"),
        str(COMPONENT_DIR / "test" / "protocol_vectors.c"),
    ]
    command = [
        compiler,
        *STRICT_FLAGS,
        f"-I{COMPONENT_DIR / 'include'}",
        *sources,
        "-o",
        str(binary),
    ]
    environment = dict(os.environ)
    environment["PATH"] = os.pathsep.join(
        [str(Path(compiler).resolve().parent), environment.get("PATH", "")]
    )

    build = subprocess.run(command, capture_output=True, text=True, env=environment)
    assert build.returncode == 0, f"strict C build failed:\n{build.stderr}"
    assert build.stderr.strip() == "", f"strict C build emitted warnings:\n{build.stderr}"

    run = subprocess.run([str(binary)], capture_output=True, text=True, env=environment)
    assert run.returncode == 0, f"C self check failed:\n{run.stderr}"

    records: list[dict[str, str]] = []
    for line in run.stdout.splitlines():
        fields = line.split()
        if not fields:
            continue
        record = {"record": fields[0]}
        for field in fields[1:]:
            key, _, value = field.partition("=")
            record[key] = value
        records.append(record)
    return records


def records_of(vectors: list[dict[str, str]], kind: str) -> list[dict[str, str]]:
    return [record for record in vectors if record["record"] == kind]


def decode(database: NetworkDatabase, identifier: int, payload: str) -> DecodedFrame:
    decoded = database.decode(CanFrame(0.0, identifier, bytes.fromhex(payload), "vcan0"))
    assert isinstance(decoded, DecodedFrame)
    return decoded


def test_c_sources_build_with_strict_warnings(c_vectors: list[dict[str, str]]) -> None:
    assert c_vectors


def test_c_limits_match_the_database(
    c_vectors: list[dict[str, str]], database: NetworkDatabase
) -> None:
    limits: dict[str, str] = {}
    for record in records_of(c_vectors, "limits"):
        limits.update({key: value for key, value in record.items() if key != "record"})

    assert int(limits["bitrate_bps"]) == 500000
    assert int(limits["saturated_raw"]) == SATURATED_TEMPERATURE_RAW
    assert int(limits["counter_modulus"]) == 256

    for info in database.messages():
        assert info.length == int(limits["dlc"])
        expected = "heartbeat_period_ms" if info.is_heartbeat else "data_period_ms"
        assert info.cycle_time_ms == int(limits[expected])

    message = database.database.get_message_by_frame_id(0x280)
    temperature = message.get_signal_by_name("ModuleTemperature")
    voltage = message.get_signal_by_name("SupplyVoltage")
    assert int(limits["temperature_min"]) * TEMPERATURE_SCALE == pytest.approx(
        float(temperature.minimum)
    )
    assert int(limits["temperature_max"]) * TEMPERATURE_SCALE == pytest.approx(
        float(temperature.maximum)
    )
    assert int(limits["voltage_min"]) * VOLTAGE_SCALE == pytest.approx(float(voltage.minimum))
    assert int(limits["voltage_max"]) * VOLTAGE_SCALE == pytest.approx(float(voltage.maximum))


def test_c_data_frames_decode_to_the_same_signals(
    c_vectors: list[dict[str, str]], database: NetworkDatabase
) -> None:
    records = records_of(c_vectors, "data")
    assert len(records) >= 8

    for record in records:
        identifier = int(record["id"], 16)
        decoded = decode(database, identifier, record["payload"])
        signals = decoded.signal_map()

        assert len(bytes.fromhex(record["payload"])) == int(record["dlc"])
        assert float(signals["ModuleTemperature"]) == pytest.approx(
            int(record["temperature_deci_degc"]) * TEMPERATURE_SCALE
        )
        assert float(signals["SupplyVoltage"]) == pytest.approx(
            int(record["voltage_centivolts"]) * VOLTAGE_SCALE
        )
        assert str(signals["NodeState"]) == NODE_STATE_NAMES[int(record["state"])]
        assert int(signals["SequenceCounter"]) == int(record["counter"])
        assert str(signals["FaultCode"]) == FAULT_CODE_NAMES[int(record["fault"])]
        assert int(signals["Reserved"]) == 0


def test_c_heartbeat_frames_decode_to_the_same_signals(
    c_vectors: list[dict[str, str]], database: NetworkDatabase
) -> None:
    records = records_of(c_vectors, "heartbeat")
    assert len(records) >= 5

    for record in records:
        identifier = int(record["id"], 16)
        decoded = decode(database, identifier, record["payload"])
        signals = decoded.signal_map()

        assert int(signals["Uptime"]) == int(record["uptime_seconds"])
        assert str(signals["NodeState"]) == NODE_STATE_NAMES[int(record["state"])]
        assert str(signals["ResetReason"]) == RESET_REASON_NAMES[int(record["reset_reason"])]
        assert int(signals["Reserved"]) == 0


def test_python_encoding_reproduces_the_c_payloads(
    c_vectors: list[dict[str, str]], database: NetworkDatabase
) -> None:
    for record in records_of(c_vectors, "data"):
        message = database.database.get_message_by_frame_id(int(record["id"], 16))
        payload = message.encode(
            {
                "ModuleTemperature": int(record["temperature_deci_degc"]) * TEMPERATURE_SCALE,
                "SupplyVoltage": int(record["voltage_centivolts"]) * VOLTAGE_SCALE,
                "NodeState": int(record["state"]),
                "SequenceCounter": int(record["counter"]),
                "FaultCode": int(record["fault"]),
                "Reserved": 0,
            },
            strict=False,
        )
        assert bytes(payload).hex().upper() == record["payload"]

    for record in records_of(c_vectors, "heartbeat"):
        message = database.database.get_message_by_frame_id(int(record["id"], 16))
        payload = message.encode(
            {
                "Uptime": int(record["uptime_seconds"]),
                "NodeState": int(record["state"]),
                "ResetReason": int(record["reset_reason"]),
                "Reserved": 0,
            },
            strict=False,
        )
        assert bytes(payload).hex().upper() == record["payload"]


def test_c_counter_arithmetic_matches_the_analyzer(c_vectors: list[dict[str, str]]) -> None:
    counters = records_of(c_vectors, "counters")[0]
    assert int(counters["wrap_255"]) == 0
    assert int(counters["skip_250_by_4"]) == (250 + 4) % 256
    assert int(counters["skip_254_by_4"]) == (254 + 4) % 256


def test_golden_thermal_vector_is_present(
    c_vectors: list[dict[str, str]], database: NetworkDatabase
) -> None:
    golden = next(
        record for record in records_of(c_vectors, "data") if record["name"] == "thermal_golden"
    )
    assert golden["payload"] == "FA00D80401070000"

    signals = decode(database, 0x280, golden["payload"]).signal_map()
    assert float(signals["ModuleTemperature"]) == pytest.approx(25.0)
    assert float(signals["SupplyVoltage"]) == pytest.approx(12.40)
    assert str(signals["NodeState"]) == "Normal"
    assert int(signals["SequenceCounter"]) == 7
    assert str(signals["FaultCode"]) == "None"
