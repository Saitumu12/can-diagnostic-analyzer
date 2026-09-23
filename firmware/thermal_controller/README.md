# Thermal Controller firmware

ESP-IDF application for the `ThermalController` bench node. It transmits `THERMAL_DATA` (`0x280`)
every 100 ms and `THERMAL_HEARTBEAT` (`0x281`) every 1000 ms at 500 kbit/s, and accepts
fault-injection commands on the console UART at 115200 baud.

## Build and flash

```bash
. $IDF_PATH/export.sh
cd firmware/thermal_controller
idf.py set-target esp32
idf.py build
idf.py -p /dev/ttyUSB1 flash monitor
```

## Commands

| Command | Effect |
| --- | --- |
| `normal` | Clear the active injected condition. |
| `range 5` | Transmit raw temperature `0x7FFF` with state `Degraded` and fault code `SensorSaturation` for five seconds. |
| `silent 3` | Suppress `THERMAL_DATA` and `THERMAL_HEARTBEAT` for three seconds, then resume. |
| `skip 4` | Advance the sequence counter by four values before the next transmission. |
| `restart` | Perform a controlled `esp_restart()` and report the reset reason after boot. |

`silent` is application-level fault injection. The node stops calling `twai_transmit`; it does not
create an electrical fault, a bus-off condition, or a wiring failure.

## Serial output

```text
BOOT node=ThermalController reset_reason=3
READY commands=normal|range <s>|silent <s>|skip <n>|restart
CMD ok=silent seconds=3
INJECT mode=none reason=expired
STAT node=ThermalController tx=210 fail=0 recoveries=0 last_err=0 seq=200
```
