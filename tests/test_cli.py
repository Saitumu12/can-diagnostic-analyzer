from __future__ import annotations

import json
from pathlib import Path

import pytest

from can_diag.cli import main
from tests.conftest import DBC_PATH, SYNTHETIC_DIR

NORMAL_CAPTURE = SYNTHETIC_DIR / "normal_session.log"
FAULT_CAPTURE = SYNTHETIC_DIR / "fault_session.log"


def run(*arguments: str) -> int:
    return main([str(argument) for argument in arguments])


def test_version_flag_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run("--version")
    assert exit_info.value.code == 0
    assert "can-diag" in capsys.readouterr().out


def test_missing_subcommand_is_rejected() -> None:
    with pytest.raises(SystemExit) as exit_info:
        run()
    assert exit_info.value.code != 0


def test_decode_prints_named_signals(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("decode", "--dbc", DBC_PATH, "--input", NORMAL_CAPTURE, "--limit", "4") == 0
    output = capsys.readouterr().out
    assert "POWER_DATA" in output
    assert "ThermalController" in output
    assert "ModuleTemperature=" in output
    assert "SupplyVoltage=" in output
    assert "frames displayed: 4 of 220" in output


def test_decode_can_filter_by_identifier(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("decode", "--dbc", DBC_PATH, "--input", NORMAL_CAPTURE, "--can-id", "0x181") == 0
    output = capsys.readouterr().out
    assert "POWER_HEARTBEAT" in output
    assert "THERMAL_DATA" not in output


def test_decode_reports_unknown_identifiers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capture = tmp_path / "unknown.log"
    capture.write_text(
        "(1700000000.000000) vcan0 180#FA00D80401000000\n"
        "(1700000000.010000) vcan0 555#0102030405060708\n",
        encoding="utf-8",
    )
    assert run("decode", "--dbc", DBC_PATH, "--input", capture) == 0
    output = capsys.readouterr().out
    assert "<unknown>" in output
    assert "0x555" in output


def test_decode_skips_malformed_lines(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    capture = tmp_path / "malformed.log"
    capture.write_text(
        "(1700000000.000000) vcan0 180#FA00D80401000000\n"
        "this line is not a candump record\n"
        "(1700000000.010000) vcan0 280#XYZ\n"
        "(1700000000.020000) vcan0 180#FA00D80401010000\n",
        encoding="utf-8",
    )
    assert run("decode", "--dbc", DBC_PATH, "--input", capture) == 0
    captured = capsys.readouterr()
    assert "malformed lines skipped: 2" in captured.out
    assert "warning: skipping malformed line 2" in captured.err


def test_decode_of_an_empty_capture_fails_unless_allowed(tmp_path: Path) -> None:
    capture = tmp_path / "empty.log"
    capture.write_text("", encoding="utf-8")
    assert run("decode", "--dbc", DBC_PATH, "--input", capture) == 1
    assert run("decode", "--dbc", DBC_PATH, "--input", capture, "--allow-empty") == 0


def test_decode_of_a_missing_file_returns_a_failure_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("decode", "--dbc", DBC_PATH, "--input", tmp_path / "absent.log") == 1
    assert "error:" in capsys.readouterr().err


def test_decode_with_a_missing_database_returns_a_failure_code(tmp_path: Path) -> None:
    assert run("decode", "--dbc", tmp_path / "absent.dbc", "--input", NORMAL_CAPTURE) == 1


def test_report_writes_json_and_markdown(tmp_path: Path) -> None:
    json_path = tmp_path / "fault.json"
    markdown_path = tmp_path / "fault.md"
    assert (
        run(
            "report",
            "--dbc",
            DBC_PATH,
            "--input",
            FAULT_CAPTURE,
            "--json-output",
            json_path,
            "--markdown-output",
            markdown_path,
        )
        == 0
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["capture"]["name"] == "fault_session.log"
    assert payload["capture"]["data_origin"].startswith("synthetic")
    assert payload["event_counts"]["node_silent"] == 1
    assert payload["event_counts"]["sequence_gap"] == 1
    assert markdown_path.read_text(encoding="utf-8").startswith("# CAN diagnostic report")


def test_report_of_healthy_traffic_lists_no_events(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        run(
            "report",
            "--dbc",
            DBC_PATH,
            "--input",
            NORMAL_CAPTURE,
            "--json-output",
            tmp_path / "n.json",
            "--markdown-output",
            tmp_path / "n.md",
        )
        == 0
    )
    assert "diagnostic events: 0" in capsys.readouterr().out


def test_report_can_fail_the_build_on_alerts(tmp_path: Path) -> None:
    assert (
        run(
            "report",
            "--dbc",
            DBC_PATH,
            "--input",
            FAULT_CAPTURE,
            "--json-output",
            tmp_path / "f.json",
            "--markdown-output",
            tmp_path / "f.md",
            "--fail-on-alert",
        )
        == 1
    )


def test_report_honours_threshold_overrides(tmp_path: Path) -> None:
    json_path = tmp_path / "strict.json"
    assert (
        run(
            "report",
            "--dbc",
            DBC_PATH,
            "--input",
            NORMAL_CAPTURE,
            "--json-output",
            json_path,
            "--markdown-output",
            tmp_path / "strict.md",
            "--data-stale-ms",
            "50",
        )
        == 0
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["configuration"]["data_stale_ms"] == 50
    assert payload["event_counts"]


def test_report_accepts_a_configuration_file(tmp_path: Path) -> None:
    config_path = tmp_path / "analyzer.json"
    config_path.write_text(json.dumps({"analyzer": {"data_stale_ms": 60}}), encoding="utf-8")
    json_path = tmp_path / "configured.json"
    assert (
        run(
            "report",
            "--dbc",
            DBC_PATH,
            "--input",
            NORMAL_CAPTURE,
            "--json-output",
            json_path,
            "--markdown-output",
            tmp_path / "configured.md",
            "--config",
            config_path,
        )
        == 0
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["configuration"]["data_stale_ms"] == 60


def test_report_rejects_an_invalid_configuration_file(tmp_path: Path) -> None:
    config_path = tmp_path / "analyzer.json"
    config_path.write_text(json.dumps({"analyzer": {"data_stale_ms": -5}}), encoding="utf-8")
    assert (
        run(
            "report",
            "--dbc",
            DBC_PATH,
            "--input",
            NORMAL_CAPTURE,
            "--json-output",
            tmp_path / "x.json",
            "--markdown-output",
            tmp_path / "x.md",
            "--config",
            config_path,
        )
        == 1
    )


def test_replay_refuses_a_non_virtual_channel_by_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run("replay", "--input", NORMAL_CAPTURE, "--channel", "can0") == 1
    captured = capsys.readouterr()
    assert "does not begin with 'vcan'" in captured.err
    assert "--allow-physical" in captured.err


def test_verify_replay_of_a_capture_against_itself(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    json_path = tmp_path / "verify.json"
    assert (
        run(
            "verify-replay",
            "--dbc",
            DBC_PATH,
            "--original",
            FAULT_CAPTURE,
            "--replayed",
            FAULT_CAPTURE,
            "--json-output",
            json_path,
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "exact id and payload match: 100.000 %" in output
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["timing"]["median_abs_error_ms"] == 0.0


def test_verify_replay_detects_a_truncated_capture(tmp_path: Path) -> None:
    truncated = tmp_path / "short.log"
    lines = FAULT_CAPTURE.read_text(encoding="utf-8").splitlines()[:-20]
    truncated.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert (
        run(
            "verify-replay",
            "--dbc",
            DBC_PATH,
            "--original",
            FAULT_CAPTURE,
            "--replayed",
            truncated,
        )
        == 1
    )


def test_record_refuses_to_overwrite_an_existing_capture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    existing = tmp_path / "session.log"
    existing.write_text("", encoding="utf-8")
    assert run("record", "--channel", "vcan0", "--output", existing) == 1
    assert "--overwrite" in capsys.readouterr().err


def test_record_reports_an_unavailable_interface(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        run(
            "record",
            "--channel",
            "can-does-not-exist",
            "--interface",
            "socketcan",
            "--output",
            tmp_path / "hw.log",
            "--duration",
            "0.1",
        )
        == 1
    )
    assert "error:" in capsys.readouterr().err
    assert not (tmp_path / "hw.log").exists()
