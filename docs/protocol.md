# Bench CAN protocol

The bench network carries four cyclic messages on classical CAN at 500 kbit/s using standard
11-bit identifiers and eight-byte payloads. The authoritative machine-readable definition is
[`dbc/hobby_network.dbc`](../dbc/hobby_network.dbc); the firmware mirror of the same constants is
[`firmware/components/can_protocol/include/can_protocol.h`](../firmware/components/can_protocol/include/can_protocol.h).
`tests/test_dbc.py::test_firmware_constants_match_the_database` fails if the two drift apart.

## Message set

| CAN ID | Message | Sender | DLC | Cycle time |
| --- | --- | --- | --- | --- |
| `0x180` | `POWER_DATA` | `PowerMonitor` | 8 | 100 ms |
| `0x181` | `POWER_HEARTBEAT` | `PowerMonitor` | 8 | 1000 ms |
| `0x280` | `THERMAL_DATA` | `ThermalController` | 8 | 100 ms |
| `0x281` | `THERMAL_HEARTBEAT` | `ThermalController` | 8 | 1000 ms |

Lower identifiers win arbitration, so the `PowerMonitor` frames have priority over the
`ThermalController` frames. At 500 kbit/s the whole schedule occupies well under one percent of
the available bandwidth, so arbitration delay is not a practical concern on this bench.

## Data payload (`0x180`, `0x280`)

| Bytes | Signal | Type | Factor | Offset | Unit | Range |
| --- | --- | --- | --- | --- | --- | --- |
| 0–1 | `ModuleTemperature` | signed 16-bit little-endian | 0.1 | 0 | degC | -40.0 … 150.0 |
| 2–3 | `SupplyVoltage` | unsigned 16-bit little-endian | 0.01 | 0 | V | 9.00 … 16.00 |
| 4 | `NodeState` | unsigned 8-bit enumeration | 1 | 0 | — | 0 … 3 |
| 5 | `SequenceCounter` | unsigned 8-bit | 1 | 0 | — | 0 … 255 |
| 6 | `FaultCode` | unsigned 8-bit enumeration | 1 | 0 | — | 0 … 1 |
| 7 | `Reserved` | unsigned 8-bit | 1 | 0 | — | always 0 |

## Heartbeat payload (`0x181`, `0x281`)

| Bytes | Signal | Type | Factor | Unit |
| --- | --- | --- | --- | --- |
| 0–3 | `Uptime` | unsigned 32-bit little-endian | 1 | s |
| 4 | `NodeState` | unsigned 8-bit enumeration | 1 | — |
| 5 | `ResetReason` | unsigned 8-bit enumeration | 1 | — |
| 6–7 | `Reserved` | unsigned 16-bit | 1 | always 0 |

## Enumerations

| Signal | Value | Meaning |
| --- | --- | --- |
| `NodeState` | 0 | `Booting` |
| `NodeState` | 1 | `Normal` |
| `NodeState` | 2 | `Degraded` |
| `NodeState` | 3 | `Fault` |
| `FaultCode` | 0 | `None` |
| `FaultCode` | 1 | `SensorSaturation` |
| `ResetReason` | 0 | `None` |
| `ResetReason` | 1 | `Watchdog` |
| `ResetReason` | 2 | `PowerOn` |
| `ResetReason` | 3 | `Software` |

## Byte order and signedness worked example

The DBC declares `ModuleTemperature` as `0|16@1-`: start bit 0, sixteen bits wide, `@1` for
Intel (little-endian) byte order, `-` for two's-complement signed.

Payload `FA 00 D8 04 01 07 00 00` on `0x280` decodes as:

| Bytes | Raw | Scaling | Decoded |
| --- | --- | --- | --- |
| `FA 00` | `0x00FA` = 250 | 250 × 0.1 | `25.0 degC` |
| `D8 04` | `0x04D8` = 1240 | 1240 × 0.01 | `12.40 V` |
| `01` | 1 | enumeration | `Normal` |
| `07` | 7 | — | `7` |
| `00` | 0 | enumeration | `None` |
| `00` | 0 | — | reserved |

A byte-order mistake is easy to spot with this vector: reading the first two bytes as big-endian
gives `0xFA00`, which as a signed value is -1536 raw, or -153.6 degC. This is the golden vector
asserted by `tests/test_decoder.py::test_golden_vector`.

Saturation uses raw `0x7FFF` = 32767, which scales to 3276.7 degC. That is far outside the
declared plausibility range and is what the analyzer reports as a range violation.

## Sequence counter

Each node increments an eight-bit counter once per transmitted data frame and wraps from 255 to 0.
The analyzer computes the forward distance modulo 256 and reports a gap only when:

- the forward distance is non-zero,
- the message was not just recovering from a detected outage,
- no node restart was reported within the restart suppression window, and
- the number of implied missing frames is consistent with the elapsed time and the declared cycle
  time.

Those guards mean that `255 → 0` is silent, a genuine single omitted frame reports exactly one
gap of one frame, and a counter that restarts at zero after a reboot does not produce a spurious
report of dozens of lost frames.

## Valid ranges

The temperature and voltage ranges in the DBC are project-defined plausibility limits chosen for
this bench. They are not manufacturer specifications. A value outside them means the reported
number is not physically plausible for this setup, not that a specific component has been proven
faulty.
