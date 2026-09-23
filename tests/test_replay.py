from __future__ import annotations

import threading

import pytest

from can_diag.decoder import NetworkDatabase
from can_diag.logfile import relative_timestamps
from can_diag.models import CanFrame
from can_diag.recorder import message_to_frame, open_bus
from can_diag.replay import ReplayError, channel_is_virtual, physical_channel_warning, replay_frames
from can_diag.timing import compare_captures, compute_timing_statistics, percentile
from tests.conftest import POWER_DATA_ID, THERMAL_DATA_ID, data_frame

LOOPBACK_INTERFACE = "virtual"


def short_capture(frame_count: int = 20) -> list[CanFrame]:
    frames: list[CanFrame] = []
    for index in range(frame_count):
        moment = index * 0.01
        frames.append(data_frame(moment, POWER_DATA_ID, index % 256))
        frames.append(data_frame(moment + 0.002, THERMAL_DATA_ID, index % 256))
    return frames


def replay_through_virtual_bus(frames: list[CanFrame], channel: str) -> list[CanFrame]:
    collected: list[CanFrame] = []
    listener_bus = open_bus(channel, LOOPBACK_INTERFACE)

    def listen() -> None:
        while len(collected) < len(frames):
            message = listener_bus.recv(timeout=0.5)
            if message is None:
                break
            collected.append(message_to_frame(message, channel))

    listener = threading.Thread(target=listen, daemon=True)
    listener.start()
    replay_frames(frames, channel=channel, interface=LOOPBACK_INTERFACE)
    listener.join(timeout=5.0)
    listener_bus.shutdown()
    return collected


def test_channel_classification() -> None:
    assert channel_is_virtual("vcan0") is True
    assert channel_is_virtual("vcan-loopback") is True
    assert channel_is_virtual("can0") is False
    assert "can0" in physical_channel_warning("can0")


def test_replay_requires_frames() -> None:
    with pytest.raises(ReplayError, match="no frames"):
        replay_frames([], channel="vcan0", interface=LOOPBACK_INTERFACE)


def test_replay_requires_a_channel() -> None:
    with pytest.raises(ReplayError, match="explicit output channel"):
        replay_frames(short_capture(2), channel="", interface=LOOPBACK_INTERFACE)


def test_replay_rejects_a_non_positive_speed_factor() -> None:
    with pytest.raises(ReplayError, match="speed factor"):
        replay_frames(
            short_capture(2),
            channel="vcan0",
            interface=LOOPBACK_INTERFACE,
            speed_factor=0.0,
        )


def test_replay_preserves_identifier_order_and_payloads(database: NetworkDatabase) -> None:
    original = short_capture()
    replayed = replay_through_virtual_bus(original, "vcan-order-test")

    assert len(replayed) == len(original)
    assert [item.arbitration_id for item in replayed] == [item.arbitration_id for item in original]
    assert [item.data for item in replayed] == [item.data for item in original]

    comparison = compare_captures(database, original, replayed)
    assert comparison.identifier_order_matches is True
    assert comparison.payloads_match is True
    assert comparison.exact_match_percent == pytest.approx(100.0)
    assert comparison.sequence_continuity_matches is True
    assert comparison.event_classifications_match is True
    assert comparison.passed is True
    assert comparison.timing.sample_count == len(original)
    assert comparison.timing.max_abs_error_ms >= comparison.timing.median_abs_error_ms


def test_replay_preserves_relative_timing_within_a_tolerant_bound() -> None:
    original = short_capture()
    replayed = replay_through_virtual_bus(original, "vcan-timing-test")
    statistics = compute_timing_statistics(
        relative_timestamps(original), relative_timestamps(replayed)
    )
    assert statistics.sample_count == len(original)
    assert statistics.median_abs_error_ms < 100.0


def test_comparison_detects_a_reordered_capture(database: NetworkDatabase) -> None:
    original = short_capture(4)
    swapped = list(original)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    comparison = compare_captures(database, original, swapped)
    assert comparison.identifier_order_matches is False
    assert comparison.passed is False
    assert comparison.first_mismatch_index == 0
    assert comparison.exact_match_percent < 100.0


def test_comparison_detects_a_truncated_capture(database: NetworkDatabase) -> None:
    original = short_capture(6)
    comparison = compare_captures(database, original, original[:-2])
    assert comparison.original_frame_count != comparison.replayed_frame_count
    assert comparison.passed is False
    assert comparison.exact_match_percent < 100.0


def test_percentile_interpolation() -> None:
    values = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert percentile(values, 0.0) == pytest.approx(0.0)
    assert percentile(values, 0.5) == pytest.approx(2.0)
    assert percentile(values, 1.0) == pytest.approx(4.0)
    assert percentile([], 0.95) == pytest.approx(0.0)
    assert percentile([7.5], 0.95) == pytest.approx(7.5)


def test_timing_statistics_from_known_offsets() -> None:
    original = [0.0, 0.1, 0.2, 0.3]
    replayed = [0.0, 0.101, 0.202, 0.310]
    statistics = compute_timing_statistics(original, replayed)
    assert statistics.sample_count == 4
    assert statistics.max_abs_error_ms == pytest.approx(10.0, abs=1e-6)
    assert statistics.median_abs_error_ms == pytest.approx(1.5, abs=1e-6)
    assert statistics.mean_abs_error_ms == pytest.approx(3.25, abs=1e-6)


def test_timing_statistics_for_empty_input() -> None:
    statistics = compute_timing_statistics([], [])
    assert statistics.sample_count == 0
    assert statistics.max_abs_error_ms == 0.0
