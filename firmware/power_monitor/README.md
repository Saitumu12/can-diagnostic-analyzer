# Power Monitor firmware

ESP-IDF application for the `PowerMonitor` bench node. It transmits `POWER_DATA` (`0x180`) every
100 ms and `POWER_HEARTBEAT` (`0x181`) every 1000 ms at 500 kbit/s.

This node has no fault-injection commands. It is the reference healthy transmitter used to show
that a fault localized to the other node does not affect unrelated traffic.

## Build and flash

```bash
. $IDF_PATH/export.sh
cd firmware/power_monitor
idf.py set-target esp32
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

## Pin assignment

| Signal | ESP32 GPIO | Transceiver pin |
| --- | --- | --- |
| TWAI TX | GPIO21 | CTX (D) |
| TWAI RX | GPIO22 | CRX (R) |

Both GPIO numbers come from `CONFIG_CAN_BENCH_TWAI_TX_GPIO` and `CONFIG_CAN_BENCH_TWAI_RX_GPIO`
in the shared `can_protocol` component. Change them with `idf.py menuconfig` under
**CAN bench node configuration**.

## Serial output

```text
BOOT node=PowerMonitor reset_reason=2
STAT node=PowerMonitor tx=110 fail=0 recoveries=0 last_err=0 seq=100
```
