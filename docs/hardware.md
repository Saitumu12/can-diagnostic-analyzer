# Bench design

This is the design the firmware and analyzer target: what to build, how to wire it, and what to
check before trusting a capture from it.

**This bench has not been built.** No hardware has been assembled, no firmware has been flashed,
and no capture in this repository came from a physical bus. The document is a build guide and a
record of the design decisions behind the pin assignment, the bitrate and the termination, not a
report on something that exists.

## Bill of materials

| Quantity | Item | Notes |
| --- | --- | --- |
| 2 | ESP32-WROOM-32 development board | Classic ESP32 with the TWAI controller |
| 2 | SN65HVD230-compatible 3.3 V CAN transceiver module | Must be a 3.3 V part, not a 5 V MCP2551 |
| 1 | SocketCAN-compatible USB-to-CAN adapter | Appears as `can0` on Linux |
| 2 | 120 Ω resistor | One at each physical end of the bus |
| — | Jumper wire for CAN-H, CAN-L and common ground | Keep stubs short |
| 1 | Linux computer | Runs `can-diag`, `ip`, and optionally `candump` |

## Topology

```text
USB-CAN adapter —— ESP32 Power Monitor —— ESP32 Thermal Controller
        ↑                                           ↑
   120-ohm termination                         120-ohm termination
```

Arrange the three devices as a short linear bus, not a star and not a ring. The two 120 Ω
terminators belong at the two physical endpoints of that line: here, at the USB-CAN adapter and at
the Thermal Controller. The Power Monitor sits in the middle and is **not** terminated. Keep the
stub from each node to the trunk as short as practical.

Many USB-CAN adapters and many SN65HVD230 breakout boards already carry an onboard 120 Ω resistor,
sometimes behind a jumper. Check before adding another one, or the bus ends up over-terminated.

## Wiring table

| From | Pin | To | Pin |
| --- | --- | --- | --- |
| ESP32 (both nodes) | GPIO21 | Transceiver | CTX / D (driver input) |
| ESP32 (both nodes) | GPIO22 | Transceiver | CRX / R (receiver output) |
| ESP32 (both nodes) | 3V3 | Transceiver | VCC |
| ESP32 (both nodes) | GND | Transceiver | GND |
| Transceiver (node A) | CANH | Transceiver (node B) | CANH |
| Transceiver (node A) | CANL | Transceiver (node B) | CANL |
| Transceiver (either node) | CANH | USB-CAN adapter | CAN-H |
| Transceiver (either node) | CANL | USB-CAN adapter | CAN-L |
| Any node | GND | USB-CAN adapter | GND |
| Bus end 1 | CANH–CANL | 120 Ω resistor | — |
| Bus end 2 | CANH–CANL | 120 Ω resistor | — |

All three devices must share a ground reference. CAN is a differential bus, but the transceivers
still need their common-mode voltages to stay within range, and a floating ground is one of the
most common causes of an intermittent hobby bus.

## TWAI controller and transceiver

The ESP32 contains a CAN protocol controller, called TWAI in Espressif documentation. It performs
bit timing, arbitration, framing, CRC, acknowledgement and error counting, and it presents logic
level TX and RX signals on two GPIOs.

It does **not** contain a bus driver. The differential CAN-H and CAN-L levels are produced by the
external SN65HVD230-compatible transceiver. Connecting two ESP32 boards directly by their GPIOs
does not form a CAN bus: there is no differential pair, no dominant/recessive wired-AND behaviour,
and no bus-level arbitration.

The transceiver must be a 3.3 V part. A 5 V transceiver such as the MCP2551 drives its RX pin to
5 V logic levels, which exceeds the ESP32 input rating.

## Pin assignment

| Function | ESP32 GPIO | Configuration symbol |
| --- | --- | --- |
| TWAI TX | GPIO21 | `CONFIG_CAN_BENCH_TWAI_TX_GPIO` |
| TWAI RX | GPIO22 | `CONFIG_CAN_BENCH_TWAI_RX_GPIO` |

Both symbols are declared in `firmware/components/can_protocol/Kconfig` and used through
`CAN_BUS_TWAI_TX_GPIO` and `CAN_BUS_TWAI_RX_GPIO` in `can_bus.h`. Change them once,
in `idf.py menuconfig` under **CAN bench node configuration**, rather than editing the application
sources. GPIO21 and GPIO22 are free on a bare ESP32-WROOM-32 board, are not strapping pins, and are
not input-only.

Avoid GPIO6–GPIO11 (connected to the SPI flash), GPIO34–GPIO39 (input only, so unusable for TX),
and the strapping pins GPIO0, GPIO2, GPIO12 and GPIO15 unless you understand their boot-time
behaviour.

## Bitrate

Every node on a CAN bus must use the same bitrate. There is no negotiation. A node configured at
the wrong bitrate does not simply stay quiet: it misreads bit boundaries, signals form and stuff
errors, drives error frames onto the bus, and can disturb traffic between the nodes that are
configured correctly. This bench uses 500 kbit/s everywhere: both firmware projects through
`TWAI_TIMING_CONFIG_500KBITS()`, and the Linux adapter through `ip link set can0 type can bitrate
500000`.

## Termination check

With the bus **powered off** and everything disconnected from any supply, measure resistance
between CAN-H and CAN-L:

| Measurement | Interpretation |
| --- | --- |
| approximately 60 Ω | Two 120 Ω terminators in parallel. Correct. |
| approximately 120 Ω | Only one terminator is present or connected. |
| several hundred Ω or open | No termination, or a broken connection in the trunk. |
| approximately 40 Ω | Three terminators, often an unnoticed onboard resistor. |

Termination is needed because a CAN trunk is a transmission line. An unterminated end reflects the
edge of each bit back along the cable, and the reflection can still be present when the receivers
sample the bit. That shows up as intermittent form and CRC errors that get worse with longer cable
and higher bitrate, which is exactly the kind of fault that message-level analysis cannot
distinguish from a firmware problem.

## Acknowledgement needs a second node

A CAN transmitter expects at least one other node to drive the acknowledgement slot dominant. With
only one powered node on the bus, every frame goes unacknowledged, the transmitter retries, its
transmit error counter climbs, and it eventually enters error-passive and then bus-off. So a bench
with a single ESP32 and no adapter listening will look broken even when the firmware is correct.
Keep at least two active nodes, and note that a `candump` process in listen-only mode does not
acknowledge.

## Linux interface setup

```bash
scripts/setup_socketcan.sh can0 500000
```

or manually:

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 500000 restart-ms 100
sudo ip link set can0 up
ip -details -statistics link show can0
```

The statistics output reports bus errors, restarts and the current CAN state, which is useful
evidence that message-level decoding cannot provide.

## Verification checklist before trusting a capture

Nothing below has been performed. It is the procedure to follow once the bench exists.

1. Bus powered off: CAN-H to CAN-L measures about 60 Ω.
2. All three devices share a ground.
3. Both ESP32 boards and the adapter are configured for 500 kbit/s.
4. `ip -details link show can0` reports `ERROR-ACTIVE`, not `BUS-OFF`.
5. `candump can0` shows all four identifiers before any analysis is attempted.
