# Replay validation

Replay exists so a capture can be re-run through the analyzer as often as needed. Validation
answers a narrow question: does replaying a capture and recording the result reproduce the same
frames, in the same order, close enough in time that the analyzer reaches the same conclusions?

## What replay reproduces

- Every application frame: identifier, DLC, payload bytes and their order.
- Approximate relative timing between frames.
- Therefore the sequence counters, freshness behaviour and findings derived from those frames.

## What replay does not reproduce

- Electrical behaviour: bit timing, edges, reflections, common-mode noise, ground offsets.
- Bus-level behaviour: arbitration between real transmitters, acknowledgement, error frames, error
  counters, error-passive and bus-off transitions.
- Exact inter-frame timing. The replayer schedules on the host clock, so scheduling error is
  expected; it is measured rather than assumed.
- The original transmitters. A replayed capture is one process sending frames.

A capture that replays perfectly therefore says nothing about whether the original bus was
electrically healthy.

## Checks performed

| Check | Method |
| --- | --- |
| Frame count | Compare the number of parsed frames in each capture |
| Identifier order | Compare the identifier sequences positionally |
| Payload equality | Compare payload bytes positionally |
| Exact match percentage | Frames matching on identifier and payload, over the larger frame count |
| Sequence continuity | Compare the `(identifier, SequenceCounter)` series from both captures |
| Finding equality | Run the full state machine over both captures and compare the ordered classifications |
| Relative timing | Median, 95th percentile and maximum absolute error of per-frame relative timestamps |

`verify-replay` exits non-zero if frame counts differ, identifier order changes, any payload
differs, sequence continuity differs, or the two captures produce different findings.

Every timing figure is computed from the two captures at the moment the command runs. No timing
number is stored in this repository, in prose or in a committed file, because the value depends
entirely on the machine and the scheduler that produced it. Run the commands below and read your
own numbers.

## Linux SocketCAN

This is the real thing: frames leave the process, cross the kernel's virtual CAN interface, and are
recorded by `candump` from `can-utils`, which is independent of this project's own code.

```bash
scripts/vcan_roundtrip.sh vcan0 fault_session
```

The script loads the `vcan` module, brings up `vcan0`, starts `candump -L`, replays the capture
with `can-diag replay`, then runs `verify-replay` on the original and the recording and checks the
findings against the scenario ground truth with `scripts/check_findings.py`. It fails if a frame is
lost or a payload differs.

Equivalently, by hand:

```bash
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan && sudo ip link set up vcan0

candump -L vcan0 > /tmp/fault_session_vcan.log &
can-diag replay --input data/synthetic/fault_session.log --channel vcan0
kill %1

can-diag verify-replay --dbc dbc/hobby_network.dbc \
    --original data/synthetic/fault_session.log \
    --replayed /tmp/fault_session_vcan.log
```

CI runs this job on an Ubuntu runner. If the runner cannot load `vcan`, the job fails rather than
being skipped quietly, so a green pipeline means the SocketCAN path really ran.

## Portable check

`scripts/replay_check.py` exercises the same replay, reception and comparison code through the
python-can in-process `virtual` backend, so it runs on any operating system with no kernel support.

```bash
python scripts/replay_check.py
```

It is a code-path check, not a substitute for the SocketCAN job: a virtual backend inside one
process does not exercise the kernel, the socket layer, or `candump`.

## Replaying onto real hardware

`replay` refuses a channel whose name does not start with `vcan` unless `--allow-physical` is
passed. Replaying onto a live bench injects frames that the real nodes did not send, which can
confuse anything else listening, so the refusal is deliberate.
