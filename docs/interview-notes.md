# Interview notes

Short answers to the questions this project naturally invites, with the reasoning behind the
design decisions in the repository.

## CAN controller versus CAN transceiver

The controller implements the protocol: bit timing and sampling, framing, stuffing, CRC,
arbitration, acknowledgement, retransmission and the error counters that drive error-active,
error-passive and bus-off. On the ESP32 that controller is the TWAI peripheral, and it exposes two
logic-level pins, TX and RX.

The transceiver implements the physical layer: it converts those logic levels into the
differential CAN-H and CAN-L pair and back, provides the dominant/recessive drive behaviour that
makes wired-AND arbitration work, and provides common-mode range and fault tolerance.

An ESP32 cannot join a CAN bus without an external transceiver, and two ESP32 boards wired GPIO to
GPIO do not form a CAN bus. This bench uses SN65HVD230-compatible 3.3 V parts because a 5 V
transceiver would drive the ESP32 RX pin above its rating.

## Why termination is required

A CAN trunk behaves as a transmission line. Without a matched load at each end, the edge of every
bit reflects back along the cable and can still be present when receivers sample that bit, which
produces intermittent form, stuff and CRC errors. The characteristic impedance is about 120 Ω, so
both physical ends carry a 120 Ω resistor between CAN-H and CAN-L, giving roughly 60 Ω measured
across a powered-off bus. Only the two endpoints are terminated; a node in the middle is not.
Over-termination, often from an unnoticed resistor on an adapter or breakout board, loads the bus
and reduces differential amplitude.

## Why two active nodes are required for normal acknowledgement

A CAN transmitter sends the acknowledgement slot recessive and expects some other node to drive it
dominant. With only one powered node, every frame is unacknowledged, the transmitter retries, its
transmit error counter climbs by eight per failure, and it passes into error-passive and then
bus-off. A single ESP32 alone on a bench therefore looks broken even with correct firmware. Note
that a receiver in listen-only or monitor mode does not acknowledge either.

## Standard versus extended identifiers

Standard identifiers are 11 bits, giving 2048 values and a shorter arbitration field. Extended
identifiers are 29 bits, giving far more address space at the cost of a longer frame and slightly
lower priority than any standard frame with the same leading bits. This bench uses standard 11-bit
identifiers: four messages need no more, and the shorter frame keeps the 10 Hz schedule
comfortably inside the 500 kbit/s bandwidth. The log format distinguishes them by field width,
three hex digits for standard and eight for extended, and `can_diag/logfile.py` rejects a
three-digit identifier above `0x7FF`.

## Classical CAN versus CAN FD

Classical CAN carries at most 8 data bytes and transmits the whole frame at one bitrate. CAN FD
allows up to 64 data bytes and can switch to a faster bitrate for the data phase, with a stronger
CRC. CAN FD needs FD-capable controllers, transceivers and adapters throughout; a classical
controller on an FD bus reports errors on the frames it cannot parse. The ESP32 TWAI peripheral is
classical CAN only, which is why this project is classical CAN at 500 kbit/s with 8-byte payloads.

## DBC scaling, offset, signedness and byte order

A DBC signal definition such as `SG_ ModuleTemperature : 0|16@1- (0.1,0) [-40|150] "degC"` states
the start bit, the length, the byte order (`@1` Intel/little-endian, `@0` Motorola/big-endian), the
signedness (`-` signed, `+` unsigned), the factor and offset, the declared range and the unit.
Physical value equals raw value times factor plus offset.

Getting any of these wrong produces a plausible-looking but wrong number, which is why the project
asserts a golden vector: `FA00D80401070000` on `0x280` must decode to 25.0 degC, 12.40 V,
`Normal`, counter 7, fault `None`. Reading those first two bytes as big-endian instead gives
-153.6 degC; reading them as unsigned hides negative temperatures entirely.

The ranges in this DBC are project-defined plausibility limits for the bench, not manufacturer
specifications, and the reports say so.

## Heartbeat timeout logic

Each node sends a 1 Hz heartbeat carrying uptime, node state and reset reason. The analyzer tracks
the last arrival of each identifier and declares it stale when the elapsed time exceeds a
threshold; 350 ms for a 100 ms data message and 2500 ms for a 1000 ms heartbeat by default. The
thresholds are several periods wide so that ordinary scheduling jitter and one lost frame do not
raise an alert, and they are configuration values rather than constants in the detection code.

The stale event is dated at `last_seen + threshold`, not at the moment the next frame happened to
arrive, so the reported time does not depend on when the analyzer next looked.

When every identifier a node has ever sent is stale at once, that is reported as one node-level
event listing all affected identifiers and naming the nodes that are still transmitting, rather
than as several unrelated alerts.

## Sequence wraparound

The counter is 8-bit and wraps 255 to 0. Comparing values arithmetically would report a gap of
-255 at every wrap, so the analyzer computes the forward distance modulo 256: `(current - previous
- 1) mod 256`. Zero means continuous, including across the wrap.

Two guards prevent false reports. After a message recovers from a detected outage the baseline is
dropped rather than compared, and within the restart suppression window after a reported restart
counter discontinuities are not reported. Both cover the case where a transmitter legitimately
restarted its counter.

The finding says the counter skipped N values. It deliberately does not say that N frames were
transmitted and lost, because the analyzer cannot tell the difference between a transmitter that
advanced its own counter, which the `skip` command does on purpose, and frames that went missing
on the bus.

## Why a missing identifier does not prove ECU failure

Loss of expected traffic is evidence that frames stopped reaching the analyzer. It localizes the
interruption but not its cause. Any of these produce identical message-level evidence: firmware
stopped calling the transmit function, the application crashed or is stuck in a watchdog loop, the
node lost power, the transceiver failed, a CAN-H or CAN-L wire came loose, the ground reference
opened, the controller entered bus-off, the adapter dropped frames, or the capture process was
descheduled.

The analyzer therefore states what stopped, when, what was still working at the same time, and
that message-level data alone cannot distinguish firmware, power, wiring, transceiver or
bus-controller failure. Narrowing further requires SocketCAN error counters and bus state, a scope
on the differential pair, a resistance measurement across the powered-off bus, or a supply-current
measurement.

## What replay does and does not reproduce

Replay reproduces application frames and approximate relative timing, so it reproduces sequence
counters, freshness behaviour and the resulting diagnostic classifications. It does not reproduce
electrical behaviour, arbitration between real transmitters, acknowledgement, error frames or
bus-off transitions, and its inter-frame timing carries host scheduling error.

That error is measured rather than assumed: `can-diag verify-replay` reports frame count, exact
identifier and payload match percentage, and the median, 95th-percentile and maximum absolute
relative timing error, all computed from the two captures at the moment it runs. See
[replay-validation.md](replay-validation.md) for how to run it.

## Why the synthetic dataset exists

Hardware is not always available, and a bench capture is not reproducible. The generator produces
byte-identical captures from a fixed seed, ships a machine-readable ground-truth file listing every
injected condition, and lets the whole diagnostic chain run in CI. The captures live under `data/synthetic/` and are labelled synthetic in the ground-truth file
beside each one and in every report generated from them. A report labels its source by where the
capture came from, so a bench recording is never presented as generated data.
