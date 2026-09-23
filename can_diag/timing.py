from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from can_diag.config import AnalyzerConfig
from can_diag.decoder import NetworkDatabase
from can_diag.logfile import relative_timestamps
from can_diag.models import CanFrame
from can_diag.monitor import analyze_frames


@dataclass(frozen=True)
class TimingStatistics:
    sample_count: int
    median_abs_error_ms: float
    p95_abs_error_ms: float
    max_abs_error_ms: float
    mean_abs_error_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "median_abs_error_ms": self.median_abs_error_ms,
            "p95_abs_error_ms": self.p95_abs_error_ms,
            "max_abs_error_ms": self.max_abs_error_ms,
            "mean_abs_error_ms": self.mean_abs_error_ms,
        }


@dataclass(frozen=True)
class ReplayComparison:
    original_frame_count: int
    replayed_frame_count: int
    compared_frame_count: int
    identifier_order_matches: bool
    payloads_match: bool
    exact_match_percent: float
    sequence_continuity_matches: bool
    event_classifications_match: bool
    original_event_classifications: tuple[str, ...]
    replayed_event_classifications: tuple[str, ...]
    timing: TimingStatistics
    first_mismatch_index: int | None

    @property
    def passed(self) -> bool:
        return (
            self.original_frame_count == self.replayed_frame_count
            and self.identifier_order_matches
            and self.payloads_match
            and self.sequence_continuity_matches
            and self.event_classifications_match
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_frame_count": self.original_frame_count,
            "replayed_frame_count": self.replayed_frame_count,
            "compared_frame_count": self.compared_frame_count,
            "identifier_order_matches": self.identifier_order_matches,
            "payloads_match": self.payloads_match,
            "exact_id_payload_match_percent": self.exact_match_percent,
            "sequence_continuity_matches": self.sequence_continuity_matches,
            "event_classifications_match": self.event_classifications_match,
            "original_event_classifications": list(self.original_event_classifications),
            "replayed_event_classifications": list(self.replayed_event_classifications),
            "first_mismatch_index": self.first_mismatch_index,
            "timing": self.timing.to_dict(),
            "passed": self.passed,
        }


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = fraction * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def compute_timing_statistics(
    original: Sequence[float], replayed: Sequence[float]
) -> TimingStatistics:
    count = min(len(original), len(replayed))
    if count == 0:
        return TimingStatistics(0, 0.0, 0.0, 0.0, 0.0)
    errors = [abs(replayed[index] - original[index]) * 1000.0 for index in range(count)]
    return TimingStatistics(
        sample_count=count,
        median_abs_error_ms=round(statistics.median(errors), 6),
        p95_abs_error_ms=round(percentile(errors, 0.95), 6),
        max_abs_error_ms=round(max(errors), 6),
        mean_abs_error_ms=round(statistics.fmean(errors), 6),
    )


def sequence_series(database: NetworkDatabase, frames: Sequence[CanFrame]) -> list[tuple[int, int]]:
    series: list[tuple[int, int]] = []
    for frame in frames:
        decoded = database.decode(frame)
        signals = getattr(decoded, "signals", None)
        if signals is None:
            continue
        signal = decoded.signal("SequenceCounter")
        if signal is None:
            continue
        value = signal.numeric_value
        if value is not None:
            series.append((frame.arbitration_id, int(value)))
    return series


def compare_captures(
    database: NetworkDatabase,
    original: Sequence[CanFrame],
    replayed: Sequence[CanFrame],
    config: AnalyzerConfig | None = None,
) -> ReplayComparison:
    compared = min(len(original), len(replayed))
    identifier_order = [frame.arbitration_id for frame in original[:compared]] == [
        frame.arbitration_id for frame in replayed[:compared]
    ]
    payload_equality = [frame.data for frame in original[:compared]] == [
        frame.data for frame in replayed[:compared]
    ]

    exact_matches = 0
    first_mismatch: int | None = None
    for index in range(compared):
        same = (
            original[index].arbitration_id == replayed[index].arbitration_id
            and original[index].data == replayed[index].data
        )
        if same:
            exact_matches += 1
        elif first_mismatch is None:
            first_mismatch = index

    total = max(len(original), len(replayed))
    match_percent = 0.0 if total == 0 else round(100.0 * exact_matches / total, 6)

    original_events = analyze_frames(database, original, config).events
    replayed_events = analyze_frames(database, replayed, config).events
    original_kinds = tuple(event.kind.value for event in original_events)
    replayed_kinds = tuple(event.kind.value for event in replayed_events)

    return ReplayComparison(
        original_frame_count=len(original),
        replayed_frame_count=len(replayed),
        compared_frame_count=compared,
        identifier_order_matches=identifier_order,
        payloads_match=payload_equality,
        exact_match_percent=match_percent,
        sequence_continuity_matches=(
            sequence_series(database, original) == sequence_series(database, replayed)
        ),
        event_classifications_match=original_kinds == replayed_kinds,
        original_event_classifications=original_kinds,
        replayed_event_classifications=replayed_kinds,
        timing=compute_timing_statistics(
            relative_timestamps(original), relative_timestamps(replayed)
        ),
        first_mismatch_index=first_mismatch,
    )
