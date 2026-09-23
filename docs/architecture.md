# Architecture

## Why the pieces are split this way

The DBC is the single source of truth for identifiers, layout, scaling, senders and cycle times.
Nothing in the Python package hard-codes a CAN identifier or a bit position. The firmware needs the
same constants in C, so they live in one shared ESP-IDF component, and `tests/test_c_protocol.py`
compiles that component for the host and compares its output against the DBC on every run.

The decoder turns raw frames into named signals with units and declared bounds. It never decides
whether something is wrong.

The monitor holds all diagnostic judgement. It is a state machine over a timestamped frame stream,
which is why the same code serves live monitoring and offline analysis: `can-diag monitor` feeds it
frames from a bus, `can-diag report` feeds it frames from a capture, and both produce identical
event objects.

Reporting renders those event objects. It adds no analysis, so the JSON and the Markdown cannot
disagree with each other.

The scenario module holds every injected time and duration in one place. The generator, the ground
truth written next to each capture, and the tests all read the same constants, so a change to the
scenario cannot leave one of them behind.

## Modules

| Module | Responsibility |
| --- | --- |
| `can_diag/models.py` | Frames, decoded signals, diagnostic events, enumerations |
| `can_diag/config.py` | Every threshold, with JSON/TOML loading and CLI overrides |
| `can_diag/scenario.py` | Injected conditions, their times, durations and expected findings |
| `can_diag/logfile.py` | candump-compatible capture parsing and writing |
| `can_diag/decoder.py` | DBC loading, message metadata, frame decoding |
| `can_diag/monitor.py` | Freshness, ranges, counters, restart and recovery detection |
| `can_diag/recorder.py` | Opening a bus and writing a replayable capture |
| `can_diag/replay.py` | Time-faithful transmission of a capture onto an explicit channel |
| `can_diag/timing.py` | Replay comparison and timing statistics |
| `can_diag/reporting.py` | JSON and Markdown report rendering |
| `can_diag/synthetic.py` | Node models that mirror the firmware loop, and the ground-truth document |
| `can_diag/cli.py` | `can-diag` subcommands, exit codes and error messages |

## How the generator stays honest

`can_diag/synthetic.py` does not paint conditions onto a finished waveform. It runs a small model
of each node with the same structure as the firmware: a 100 ms loop that transmits a data frame and
increments the counter, a heartbeat every tenth tick, and a boot heartbeat before the loop starts.
The injected conditions act on that model exactly as the serial commands act on the firmware.

That is what keeps the data consistent with the firmware. A silent node does not transmit, so it
does not increment its counter and its uptime keeps climbing; when it comes back the counter
resumes where it stopped. A restarting node starts a new boot, so its uptime and counter return to
zero and its reset reason becomes `Software`. Neither behaviour is written down twice.

## Diagnostic state machine

Per message the monitor tracks the last timestamp, whether the message is currently stale, the last
sequence counter, the last reported uptime, and any open range violation. Per node it tracks
whether every observed message is stale and when a restart was last reported.

On each incoming frame:

1. Evaluate freshness for every message seen so far, using the new frame's timestamp as "now". A
   message past its threshold is declared stale and dated at `last_seen + threshold`, not at the
   moment of detection, so the reported time does not depend on when the analyzer next looked.
2. If every observed message of a node is stale, raise one node-level finding naming all affected
   identifiers and listing which other nodes are still transmitting.
3. Decode the frame. An unknown identifier is reported once and does not stop processing.
4. If the message was stale, record a recovery with its outage duration and drop the sequence
   baseline, so a transmitter that restarted its counter is not read as a burst of loss.
5. For a heartbeat, compare reported uptime with the previous value; a decrease is a restart.
6. For a data message, check the sequence counter.
7. Check every numeric signal against the bounds declared in the DBC, opening a violation on the
   first offending frame and closing it with a recovery when the value returns.

Findings are raised once per episode, not once per frame, so a five second saturation at 10 Hz
produces one violation and one recovery rather than fifty alerts.

## Sequence counter rules

The counter is 8-bit and wraps. The monitor computes the forward distance
`(current - previous - 1) mod 256`. Zero means continuous, including across the 255-to-0 wrap.

A non-zero distance is reported as a gap unless the baseline was just dropped by a recovery, or a
restart was reported for that node within the restart suppression window. The finding says the
counter skipped N values; it does not assert that N frames were transmitted and lost, because the
analyzer cannot tell a transmitter that advanced its own counter from frames that went missing on
the bus. The `skip 4` command produces exactly that evidence, and so would four genuinely lost
frames.

## Findings

| Classification | Severity | Meaning |
| --- | --- | --- |
| `node_silent` | critical | Every observed identifier from one node is stale |
| `heartbeat_timeout` | critical | A 1000 ms heartbeat exceeded its threshold |
| `data_message_stale` | major | A 100 ms data message exceeded its threshold |
| `range_violation` | major | A decoded signal left its declared plausibility range |
| `node_restart` | major | Reported uptime decreased |
| `sequence_gap` | minor | The counter advanced by more than one |
| `unknown_message_id` | minor | An identifier absent from the DBC was observed |
| `message_recovered` | info | A stale identifier resumed |
| `node_recovered` | info | A silent node resumed |
| `range_recovered` | info | A signal returned inside its range |

Every finding carries a timestamp, node, CAN ID, message name, classification, observed evidence,
the configured expectation it was judged against, a recovery time where one applies, and a
limitation statement.

## Thresholds

All thresholds live in `AnalyzerConfig`.

| Setting | Default | Meaning |
| --- | --- | --- |
| `data_stale_ms` | 350 | Freshness limit for a 100 ms data message |
| `heartbeat_stale_ms` | 2500 | Freshness limit for a 1000 ms heartbeat |
| `message_stale_ms` | `{}` | Per-message overrides by name |
| `restart_suppression_ms` | 2000 | Window after a reported restart in which counter gaps are not reported |
| `max_reportable_gap` | 127 | Largest forward counter distance reported as a gap |
| `check_ranges` | `true` | Enable range checking |
| `check_sequence` | `true` | Enable counter checking |
| `report_unknown_ids` | `true` | Report identifiers absent from the DBC |

Override them with `--data-stale-ms` and `--heartbeat-stale-ms`, or with `--config file.json` or
`--config file.toml` containing an `analyzer` table.
