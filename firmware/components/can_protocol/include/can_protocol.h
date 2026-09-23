#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CAN_PROTOCOL_BITRATE_BPS 500000
#define CAN_PROTOCOL_DLC 8

#define CAN_ID_POWER_DATA 0x180
#define CAN_ID_POWER_HEARTBEAT 0x181
#define CAN_ID_THERMAL_DATA 0x280
#define CAN_ID_THERMAL_HEARTBEAT 0x281

#define CAN_PROTOCOL_DATA_PERIOD_MS 100
#define CAN_PROTOCOL_HEARTBEAT_PERIOD_MS 1000

#define CAN_PROTOCOL_TEMPERATURE_MIN_DECI_DEGC (-400)
#define CAN_PROTOCOL_TEMPERATURE_MAX_DECI_DEGC 1500
#define CAN_PROTOCOL_VOLTAGE_MIN_CENTIVOLTS 900
#define CAN_PROTOCOL_VOLTAGE_MAX_CENTIVOLTS 1600
#define CAN_PROTOCOL_SATURATED_TEMPERATURE_RAW 0x7FFF

#define CAN_PROTOCOL_COUNTER_MODULUS 256

typedef enum {
    CAN_NODE_STATE_BOOTING = 0,
    CAN_NODE_STATE_NORMAL = 1,
    CAN_NODE_STATE_DEGRADED = 2,
    CAN_NODE_STATE_FAULT = 3,
} can_node_state_t;

typedef enum {
    CAN_FAULT_CODE_NONE = 0,
    CAN_FAULT_CODE_SENSOR_SATURATION = 1,
} can_fault_code_t;

typedef enum {
    CAN_RESET_REASON_NONE = 0,
    CAN_RESET_REASON_WATCHDOG = 1,
    CAN_RESET_REASON_POWER_ON = 2,
    CAN_RESET_REASON_SOFTWARE = 3,
} can_reset_reason_t;

typedef struct {
    int16_t module_temperature_deci_degc;
    uint16_t supply_voltage_centivolts;
    can_node_state_t node_state;
    uint8_t sequence_counter;
    can_fault_code_t fault_code;
} can_data_payload_t;

typedef struct {
    uint32_t uptime_seconds;
    can_node_state_t node_state;
    can_reset_reason_t reset_reason;
} can_heartbeat_payload_t;

void can_protocol_encode_data(const can_data_payload_t *payload, uint8_t out[CAN_PROTOCOL_DLC]);

void can_protocol_decode_data(const uint8_t frame[CAN_PROTOCOL_DLC], can_data_payload_t *out);

void can_protocol_encode_heartbeat(const can_heartbeat_payload_t *payload,
                                   uint8_t out[CAN_PROTOCOL_DLC]);

void can_protocol_decode_heartbeat(const uint8_t frame[CAN_PROTOCOL_DLC],
                                   can_heartbeat_payload_t *out);

uint8_t can_protocol_next_counter(uint8_t counter);

uint8_t can_protocol_advance_counter(uint8_t counter, uint8_t steps);

int16_t can_protocol_clamp_temperature(int32_t deci_degc);

uint16_t can_protocol_clamp_voltage(int32_t centivolts);

bool can_protocol_temperature_in_range(int16_t deci_degc);

bool can_protocol_voltage_in_range(uint16_t centivolts);

#ifdef __cplusplus
}
#endif
