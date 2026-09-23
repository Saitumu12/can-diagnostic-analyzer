from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import cantools
from cantools.database.can.database import Database
from cantools.database.can.message import Message

from can_diag.config import HEARTBEAT_NAME_SUFFIX
from can_diag.models import CanFrame, DecodedFrame, DecodedSignal, UnknownFrame


class DecodeError(Exception):
    pass


@dataclass(frozen=True)
class MessageInfo:
    frame_id: int
    name: str
    sender: str
    length: int
    cycle_time_ms: int | None
    is_heartbeat: bool


class NetworkDatabase:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._by_id: dict[int, Message] = {
            message.frame_id: message for message in database.messages
        }

    @classmethod
    def load(cls, path: Path) -> NetworkDatabase:
        if not path.is_file():
            raise DecodeError(f"DBC file not found: {path.name}")
        try:
            database = cantools.database.load_file(str(path))
        except Exception as error:
            raise DecodeError(f"failed to load DBC {path.name}: {error}") from error
        if not isinstance(database, Database):
            raise DecodeError(f"{path.name} is not a CAN database")
        if not database.messages:
            raise DecodeError(f"{path.name} defines no messages")
        return cls(database)

    @property
    def database(self) -> Database:
        return self._database

    def messages(self) -> list[MessageInfo]:
        return [self._describe(message) for message in self._database.messages]

    def message_info(self, frame_id: int) -> MessageInfo | None:
        message = self._by_id.get(frame_id)
        return None if message is None else self._describe(message)

    def info_by_name(self, name: str) -> MessageInfo | None:
        for message in self._database.messages:
            if message.name == name:
                return self._describe(message)
        return None

    def nodes(self) -> list[str]:
        names = {self._sender(message) for message in self._database.messages}
        return sorted(names)

    def messages_for_node(self, node: str) -> list[MessageInfo]:
        return [
            self._describe(message)
            for message in self._database.messages
            if self._sender(message) == node
        ]

    def decode(self, frame: CanFrame) -> DecodedFrame | UnknownFrame:
        message = self._by_id.get(frame.arbitration_id)
        if message is None:
            return UnknownFrame(frame)
        try:
            values = message.decode(frame.data, allow_truncated=True)
        except Exception as error:
            raise DecodeError(
                f"failed to decode {message.name} (0x{frame.arbitration_id:03X}): {error}"
            ) from error
        info = self._describe(message)
        signals = tuple(
            DecodedSignal(
                name=signal.name,
                value=self._scalar(values.get(signal.name)),
                unit=signal.unit or "",
                minimum=None if signal.minimum is None else float(signal.minimum),
                maximum=None if signal.maximum is None else float(signal.maximum),
            )
            for signal in message.signals
            if signal.name in values
        )
        return DecodedFrame(
            frame=frame,
            message_name=info.name,
            sender=info.sender,
            signals=signals,
            is_heartbeat=info.is_heartbeat,
            cycle_time_ms=info.cycle_time_ms,
        )

    def decode_all(self, frames: Iterable[CanFrame]) -> Iterator[DecodedFrame | UnknownFrame]:
        for frame in frames:
            yield self.decode(frame)

    def _describe(self, message: Message) -> MessageInfo:
        return MessageInfo(
            frame_id=message.frame_id,
            name=message.name,
            sender=self._sender(message),
            length=message.length,
            cycle_time_ms=message.cycle_time,
            is_heartbeat=message.name.upper().endswith(HEARTBEAT_NAME_SUFFIX),
        )

    @staticmethod
    def _sender(message: Message) -> str:
        if message.senders:
            return message.senders[0]
        return "Unknown"

    @staticmethod
    def _scalar(value: object) -> float | int | str:
        if value is None:
            return 0
        if isinstance(value, (int, float, str)):
            return value
        return str(value)
