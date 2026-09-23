from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from can_diag.models import CanFrame

LOG_LINE_PATTERN = re.compile(
    r"^\((?P<timestamp>\d+(?:\.\d+)?)\)\s+(?P<channel>\S+)\s+"
    r"(?P<can_id>[0-9A-Fa-f]{3,8})#(?P<payload>[0-9A-Fa-f]*)\s*$"
)
MAX_PAYLOAD_BYTES = 8


class LogFormatError(Exception):
    pass


@dataclass(frozen=True)
class MalformedLine:
    line_number: int
    text: str
    reason: str


@dataclass
class LogReadResult:
    frames: list[CanFrame] = field(default_factory=list)
    malformed: list[MalformedLine] = field(default_factory=list)

    @property
    def frame_count(self) -> int:
        return len(self.frames)


def parse_log_line(line: str) -> CanFrame:
    match = LOG_LINE_PATTERN.match(line.strip())
    if match is None:
        raise LogFormatError("line does not match '(timestamp) channel ID#PAYLOAD'")
    payload = match.group("payload")
    if len(payload) % 2 != 0:
        raise LogFormatError("payload must contain an even number of hex digits")
    data = bytes.fromhex(payload)
    if len(data) > MAX_PAYLOAD_BYTES:
        raise LogFormatError(f"payload longer than {MAX_PAYLOAD_BYTES} bytes")
    identifier_text = match.group("can_id")
    is_extended = len(identifier_text) > 3
    arbitration_id = int(identifier_text, 16)
    if not is_extended and arbitration_id > 0x7FF:
        raise LogFormatError("standard identifier exceeds 11 bits")
    return CanFrame(
        timestamp=float(match.group("timestamp")),
        arbitration_id=arbitration_id,
        data=data,
        channel=match.group("channel"),
        is_extended_id=is_extended,
    )


def format_log_line(frame: CanFrame) -> str:
    width = 8 if frame.is_extended_id else 3
    channel = frame.channel or "can0"
    return f"({frame.timestamp:.6f}) {channel} {frame.arbitration_id:0{width}X}#{frame.hex_data}"


def iter_log_stream(stream: TextIO) -> Iterator[tuple[int, CanFrame | MalformedLine]]:
    for line_number, raw_line in enumerate(stream, start=1):
        if not raw_line.strip():
            continue
        try:
            yield line_number, parse_log_line(raw_line)
        except (LogFormatError, ValueError) as error:
            yield line_number, MalformedLine(line_number, raw_line.rstrip("\n"), str(error))


def read_log(path: Path) -> LogReadResult:
    if not path.is_file():
        raise FileNotFoundError(f"capture file not found: {path.name}")
    result = LogReadResult()
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for _, item in iter_log_stream(stream):
            if isinstance(item, CanFrame):
                result.frames.append(item)
            else:
                result.malformed.append(item)
    return result


def write_log(path: Path, frames: Iterable[CanFrame]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for frame in frames:
            stream.write(format_log_line(frame) + "\n")
            written += 1
    return written


def relative_timestamps(frames: Sequence[CanFrame]) -> list[float]:
    if not frames:
        return []
    origin = frames[0].timestamp
    return [frame.timestamp - origin for frame in frames]
