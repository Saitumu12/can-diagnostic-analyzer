from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import can

from can_diag.models import CanFrame
from can_diag.recorder import DEFAULT_BITRATE, BusError, open_bus

VIRTUAL_CHANNEL_PREFIX = "vcan"
PHYSICAL_CHANNEL_WARNING = (
    "channel '{channel}' does not begin with '{prefix}'. Replaying onto a physical bus injects "
    "frames into real hardware. Continue only if that is intended."
)


class ReplayError(Exception):
    pass


@dataclass(frozen=True)
class ReplayResult:
    channel: str
    interface: str
    frames_sent: int
    wall_clock_seconds: float
    capture_span_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "interface": self.interface,
            "frames_sent": self.frames_sent,
            "wall_clock_seconds": round(self.wall_clock_seconds, 6),
            "capture_span_seconds": round(self.capture_span_seconds, 6),
        }


def channel_is_virtual(channel: str) -> bool:
    return channel.lower().startswith(VIRTUAL_CHANNEL_PREFIX)


def physical_channel_warning(channel: str) -> str:
    return PHYSICAL_CHANNEL_WARNING.format(channel=channel, prefix=VIRTUAL_CHANNEL_PREFIX)


def frame_to_message(frame: CanFrame) -> can.Message:
    return can.Message(
        arbitration_id=frame.arbitration_id,
        data=frame.data,
        is_extended_id=frame.is_extended_id,
        timestamp=frame.timestamp,
    )


def replay_frames(
    frames: Sequence[CanFrame],
    channel: str,
    interface: str = "socketcan",
    bitrate: int = DEFAULT_BITRATE,
    speed_factor: float = 1.0,
    send_timeout: float = 1.0,
) -> ReplayResult:
    if not channel:
        raise ReplayError("an explicit output channel is required")
    if speed_factor <= 0:
        raise ReplayError("speed factor must be greater than zero")
    if not frames:
        raise ReplayError("capture contains no frames to replay")

    origin = frames[0].timestamp
    span = frames[-1].timestamp - origin
    try:
        bus = open_bus(channel, interface, bitrate)
    except BusError as error:
        raise ReplayError(str(error)) from error

    sent = 0
    started = time.perf_counter()
    try:
        for frame in frames:
            target = (frame.timestamp - origin) / speed_factor
            remaining = target - (time.perf_counter() - started)
            if remaining > 0:
                time.sleep(remaining)
            try:
                bus.send(frame_to_message(frame), timeout=send_timeout)
            except can.CanError as error:
                raise ReplayError(
                    f"transmission failed after {sent} frames on '{channel}': {error}"
                ) from error
            sent += 1
    finally:
        bus.shutdown()

    return ReplayResult(
        channel=channel,
        interface=interface,
        frames_sent=sent,
        wall_clock_seconds=time.perf_counter() - started,
        capture_span_seconds=span,
    )
