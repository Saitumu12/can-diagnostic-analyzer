from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import can

from can_diag.logfile import format_log_line, write_log
from can_diag.models import CanFrame

DEFAULT_BITRATE = 500000
DEFAULT_INTERFACE = "socketcan"


class BusError(Exception):
    pass


@dataclass(frozen=True)
class RecordingResult:
    output_path: Path
    frame_count: int
    duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": self.output_path.name,
            "frame_count": self.frame_count,
            "duration_seconds": round(self.duration_seconds, 6),
        }


def open_bus(channel: str, interface: str, bitrate: int = DEFAULT_BITRATE) -> can.BusABC:
    kwargs: dict[str, Any] = {"channel": channel, "interface": interface}
    if interface not in {"virtual", "socketcan"}:
        kwargs["bitrate"] = bitrate
    try:
        return can.Bus(**kwargs)
    except Exception as error:
        raise BusError(
            f"could not open CAN interface '{channel}' using backend '{interface}': {error}"
        ) from error


def message_to_frame(message: can.Message, channel: str) -> CanFrame:
    return CanFrame(
        timestamp=float(message.timestamp),
        arbitration_id=int(message.arbitration_id),
        data=bytes(message.data or b""),
        channel=message.channel if isinstance(message.channel, str) else channel,
        is_extended_id=bool(message.is_extended_id),
    )


def iter_bus_frames(
    bus: can.BusABC,
    channel: str,
    duration_seconds: float | None,
    poll_timeout: float = 0.2,
) -> Iterator[CanFrame]:
    started = time.monotonic()
    while True:
        if duration_seconds is not None and time.monotonic() - started >= duration_seconds:
            return
        message = bus.recv(timeout=poll_timeout)
        if message is None:
            continue
        if message.is_error_frame or message.is_remote_frame:
            continue
        yield message_to_frame(message, channel)


def record_to_file(
    channel: str,
    output_path: Path,
    interface: str = DEFAULT_INTERFACE,
    bitrate: int = DEFAULT_BITRATE,
    duration_seconds: float | None = None,
    on_frame: Callable[[CanFrame], None] | None = None,
) -> RecordingResult:
    bus = open_bus(channel, interface, bitrate)
    frames: list[CanFrame] = []
    started = time.monotonic()
    try:
        for frame in iter_bus_frames(bus, channel, duration_seconds):
            frames.append(frame)
            if on_frame is not None:
                on_frame(frame)
    except KeyboardInterrupt:
        pass
    finally:
        bus.shutdown()
    elapsed = time.monotonic() - started
    write_log(output_path, frames)
    return RecordingResult(
        output_path=output_path, frame_count=len(frames), duration_seconds=elapsed
    )


def render_frame_line(frame: CanFrame) -> str:
    return format_log_line(frame)
