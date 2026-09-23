# Fault injection

Fault injection here is deliberate application-level behaviour. The Thermal Controller firmware
changes what it transmits, or stops calling `twai_transmit`, on command. Nothing in this repository
creates an electrical fault or a physical-layer error on purpose.

That distinction drives every conclusion the analyzer draws. A node told to stop transmitting looks,
at the message level, exactly like a node whose transceiver failed, whose power was lost, or whose
CAN-H wire fell off. The analyzer therefore reports where the traffic stopped and says explicitly
that it cannot identify why.

## Commands

Connect to the Thermal Controller console at 115200 baud, one command per line.

| Command | Injected behaviour |
| --- | --- |
| `normal` | Clear the active condition |
| `range <s>` | Transmit raw temperature `0x7FFF` with state `Degraded` and fault code `SensorSaturation` for `<s>` seconds |
| `silent <s>` | Stop transmitting `0x280` and `0x281` for `<s>` seconds, then resume |
| `skip <n>` | Advance the sequence counter by `<n>` extra values before the next transmission |
| `restart` | Controlled `esp_restart()`, then report the resulting reset reason after boot |

Console output is short and machine readable:

```text
CMD ok=silent seconds=3
INJECT mode=none reason=expired
CMD ok=skip steps=4
INJECT mode=skip applied=4 next_seq=112
STAT node=ThermalController tx=210 fail=0 recoveries=0 last_err=0 seq=200
```

The Power Monitor has no injection commands. It stays healthy throughout, which is what makes the
localization argument possible: if both Thermal Controller identifiers stop while both Power
Monitor identifiers keep arriving on the same bus through the same adapter, the interruption is
attributable to the Thermal Controller branch rather than to the bus as a whole or to the capture
tooling.

## What each command does to node state

This matters, because it is where a scenario can quietly become incoherent.

| Command | Sequence counter | Uptime | Reset reason |
| --- | --- | --- | --- |
| `range <s>` | keeps incrementing | keeps climbing | unchanged |
| `silent <s>` | stops, then resumes where it stopped | keeps climbing | unchanged |
| `skip <n>` | jumps forward by `<n>` | keeps climbing | unchanged |
| `restart` | returns to 0 | returns to 0 | becomes `Software` |

`silent` does not reset anything. The node is still running; it simply is not transmitting. Only
`restart` resets state, and because it is `esp_restart()` the reset reason is `Software`, never
`Watchdog`.

## Reproducing the conditions without hardware

The synthetic generator models each node as the firmware loop runs, so the same commands produce
the same observable behaviour in the captures:

```bash
python scripts/regenerate.py
can-diag report --dbc dbc/hobby_network.dbc --input data/synthetic/fault_session.log \
    --json-output reports/fault_session.json \
    --markdown-output reports/fault_session.md
```

`data/synthetic/fault_session.log` carries `range 5` at 15 s, `silent 3` at 30 s and `skip 4` at
45 s. `data/synthetic/restart_session.log` carries a single `restart` at 10 s. The injected
conditions and the findings each is expected to produce are listed in the ground-truth file beside
each capture, and `scripts/check_findings.py` compares a report against it.

Every capture under `data/synthetic/` is generated, not recorded.

## Reproducing the conditions on a bench

```bash
scripts/setup_socketcan.sh can0 500000
can-diag monitor --dbc dbc/hobby_network.dbc --channel can0
```

In a second terminal, attach to the Thermal Controller console and issue `range 5`, then `normal`,
then `silent 3`, then `skip 4`, letting the bus settle between commands. To keep the evidence,
record instead of monitoring, then analyze the recording:

```bash
can-diag record --channel can0 --output bench_session.log --duration 120
can-diag report --dbc dbc/hobby_network.dbc --input bench_session.log \
    --json-output bench_session.json --markdown-output bench_session.md
```

Reports generated from a capture outside `data/synthetic/` are labelled as such, so a bench
recording is never presented as generated data or the other way round.

## What the analyzer concludes, and what it does not

For the silence, the report states that both expected Thermal Controller identifiers became stale
while Power Monitor traffic remained active, that this localizes the interruption to the Thermal
Controller or its connection, and that message-level data alone cannot distinguish firmware, power,
wiring, transceiver or bus-controller failure.

Confirming a physical cause needs evidence the frames do not carry: SocketCAN error counters and
bus state from `ip -details -statistics link show`, a scope on CAN-H and CAN-L, a resistance
measurement across the powered-off bus, or a supply-current measurement at the node.
