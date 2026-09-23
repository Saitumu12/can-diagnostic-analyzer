from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

from can_diag.decoder import DecodeError, NetworkDatabase
from can_diag.logfile import read_log
from can_diag.models import CanFrame
from can_diag.recorder import message_to_frame, open_bus
from can_diag.replay import replay_frames
from can_diag.timing import compare_captures

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LOOPBACK_INTERFACE = "virtual"


def _capture_thread(
    bus: object, channel: str, expected: int, collected: list[CanFrame], stop: threading.Event
) -> None:
    while not stop.is_set() and len(collected) < expected:
        message = bus.recv(timeout=0.5)
        if message is None:
            continue
        collected.append(message_to_frame(message, channel))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay a capture through the python-can virtual backend, record what arrives, and "
            "measure order, payload and relative timing fidelity. This exercises the replay and "
            "verify-replay code paths on any operating system. It is not a SocketCAN vcan0 or "
            "hardware measurement."
        )
    )
    parser.add_argument("--dbc", type=Path, default=REPOSITORY_ROOT / "dbc" / "hobby_network.dbc")
    parser.add_argument(
        "--input", type=Path, default=REPOSITORY_ROOT / "data" / "synthetic" / "fault_session.log"
    )
    parser.add_argument("--channel", default="vcan-loopback")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--json-output", type=Path)
    arguments = parser.parse_args(argv)

    try:
        database = NetworkDatabase.load(arguments.dbc)
    except DecodeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    original = read_log(arguments.input)
    if not original.frames:
        print(f"error: {arguments.input.name} contains no frames", file=sys.stderr)
        return 1

    collected: list[CanFrame] = []
    stop = threading.Event()
    listener_bus = open_bus(arguments.channel, LOOPBACK_INTERFACE)
    listener = threading.Thread(
        target=_capture_thread,
        args=(listener_bus, arguments.channel, len(original.frames), collected, stop),
        daemon=True,
    )
    listener.start()

    result = replay_frames(
        frames=original.frames,
        channel=arguments.channel,
        interface=LOOPBACK_INTERFACE,
        speed_factor=arguments.speed,
    )
    listener.join(timeout=5.0)
    stop.set()
    listener_bus.shutdown()

    replayed = [
        CanFrame(
            timestamp=frame.timestamp * arguments.speed,
            arbitration_id=frame.arbitration_id,
            data=frame.data,
            channel=frame.channel,
        )
        for frame in collected
    ]
    comparison = compare_captures(database, original.frames, replayed)

    summary = {
        "original_capture": arguments.input.name,
        "backend": LOOPBACK_INTERFACE,
        "channel": arguments.channel,
        "speed_factor": arguments.speed,
        "frames_sent": result.frames_sent,
        "measurement_scope": (
            "python-can virtual backend on the development host; not SocketCAN vcan0 and not "
            "physical hardware"
        ),
        **comparison.to_dict(),
    }
    print(json.dumps(summary, indent=2))

    if arguments.json_output is not None:
        arguments.json_output.parent.mkdir(parents=True, exist_ok=True)
        arguments.json_output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    return 0 if comparison.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
