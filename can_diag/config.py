from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

DEFAULT_DATA_STALE_MS: int = 350
DEFAULT_HEARTBEAT_STALE_MS: int = 2500
DEFAULT_RESTART_SUPPRESSION_MS: int = 2000
DEFAULT_MAX_REPORTABLE_GAP: int = 127
HEARTBEAT_NAME_SUFFIX: str = "_HEARTBEAT"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class AnalyzerConfig:
    data_stale_ms: int = DEFAULT_DATA_STALE_MS
    heartbeat_stale_ms: int = DEFAULT_HEARTBEAT_STALE_MS
    message_stale_ms: Mapping[str, int] = field(default_factory=dict)
    restart_suppression_ms: int = DEFAULT_RESTART_SUPPRESSION_MS
    max_reportable_gap: int = DEFAULT_MAX_REPORTABLE_GAP
    check_ranges: bool = True
    check_sequence: bool = True
    report_unknown_ids: bool = True

    def stale_threshold_seconds(self, message_name: str, is_heartbeat: bool) -> float:
        override = self.message_stale_ms.get(message_name)
        if override is not None:
            return override / 1000.0
        if is_heartbeat:
            return self.heartbeat_stale_ms / 1000.0
        return self.data_stale_ms / 1000.0

    def with_overrides(
        self,
        data_stale_ms: int | None = None,
        heartbeat_stale_ms: int | None = None,
        message_stale_ms: Mapping[str, int] | None = None,
    ) -> AnalyzerConfig:
        merged = dict(self.message_stale_ms)
        if message_stale_ms:
            merged.update(message_stale_ms)
        return replace(
            self,
            data_stale_ms=self.data_stale_ms if data_stale_ms is None else data_stale_ms,
            heartbeat_stale_ms=(
                self.heartbeat_stale_ms if heartbeat_stale_ms is None else heartbeat_stale_ms
            ),
            message_stale_ms=merged,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "data_stale_ms": self.data_stale_ms,
            "heartbeat_stale_ms": self.heartbeat_stale_ms,
            "message_stale_ms": dict(self.message_stale_ms),
            "restart_suppression_ms": self.restart_suppression_ms,
            "max_reportable_gap": self.max_reportable_gap,
            "check_ranges": self.check_ranges,
            "check_sequence": self.check_sequence,
            "report_unknown_ids": self.report_unknown_ids,
        }


def _coerce_positive_int(value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"configuration key '{key}' must be an integer number of milliseconds")
    if value <= 0:
        raise ConfigError(f"configuration key '{key}' must be greater than zero")
    return value


def config_from_mapping(raw: Mapping[str, Any]) -> AnalyzerConfig:
    known = {
        "data_stale_ms",
        "heartbeat_stale_ms",
        "message_stale_ms",
        "restart_suppression_ms",
        "max_reportable_gap",
        "check_ranges",
        "check_sequence",
        "report_unknown_ids",
    }
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(f"unknown configuration keys: {', '.join(unknown)}")

    overrides: dict[str, int] = {}
    for name, value in dict(raw.get("message_stale_ms", {})).items():
        overrides[str(name)] = _coerce_positive_int(value, f"message_stale_ms.{name}")

    return AnalyzerConfig(
        data_stale_ms=_coerce_positive_int(
            raw.get("data_stale_ms", DEFAULT_DATA_STALE_MS), "data_stale_ms"
        ),
        heartbeat_stale_ms=_coerce_positive_int(
            raw.get("heartbeat_stale_ms", DEFAULT_HEARTBEAT_STALE_MS), "heartbeat_stale_ms"
        ),
        message_stale_ms=overrides,
        restart_suppression_ms=_coerce_positive_int(
            raw.get("restart_suppression_ms", DEFAULT_RESTART_SUPPRESSION_MS),
            "restart_suppression_ms",
        ),
        max_reportable_gap=_coerce_positive_int(
            raw.get("max_reportable_gap", DEFAULT_MAX_REPORTABLE_GAP), "max_reportable_gap"
        ),
        check_ranges=bool(raw.get("check_ranges", True)),
        check_sequence=bool(raw.get("check_sequence", True)),
        report_unknown_ids=bool(raw.get("report_unknown_ids", True)),
    )


def load_config(path: Path | None) -> AnalyzerConfig:
    if path is None:
        return AnalyzerConfig()
    if not path.is_file():
        raise ConfigError(f"configuration file not found: {path.name}")
    text = path.read_bytes()
    if path.suffix.lower() == ".toml":
        try:
            raw = tomllib.loads(text.decode("utf-8"))
        except tomllib.TOMLDecodeError as error:
            raise ConfigError(f"invalid TOML in {path.name}: {error}") from error
    else:
        try:
            raw = json.loads(text.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise ConfigError(f"invalid JSON in {path.name}: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name} must contain a mapping of configuration keys")
    section = raw.get("analyzer", raw)
    if not isinstance(section, dict):
        raise ConfigError(f"{path.name} 'analyzer' section must be a mapping")
    return config_from_mapping(section)
