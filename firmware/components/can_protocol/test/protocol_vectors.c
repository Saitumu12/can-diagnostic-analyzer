#include <stdio.h>
#include <string.h>

#include "can_protocol.h"

struct data_vector {
    const char *name;
    uint32_t identifier;
    int16_t temperature_deci_degc;
    uint16_t voltage_centivolts;
    can_node_state_t node_state;
    uint8_t sequence_counter;
    can_fault_code_t fault_code;
};

struct heartbeat_vector {
    const char *name;
    uint32_t identifier;
    uint32_t uptime_seconds;
    can_node_state_t node_state;
    can_reset_reason_t reset_reason;
};

static const struct data_vector DATA_VECTORS[] = {
    {"thermal_golden", CAN_ID_THERMAL_DATA, 250, 1240, CAN_NODE_STATE_NORMAL, 7,
     CAN_FAULT_CODE_NONE},
    {"thermal_negative_temperature", CAN_ID_THERMAL_DATA, -155, 1240, CAN_NODE_STATE_NORMAL, 0,
     CAN_FAULT_CODE_NONE},
    {"thermal_temperature_minimum", CAN_ID_THERMAL_DATA, CAN_PROTOCOL_TEMPERATURE_MIN_DECI_DEGC,
     CAN_PROTOCOL_VOLTAGE_MIN_CENTIVOLTS, CAN_NODE_STATE_BOOTING, 1, CAN_FAULT_CODE_NONE},
    {"thermal_temperature_maximum", CAN_ID_THERMAL_DATA, CAN_PROTOCOL_TEMPERATURE_MAX_DECI_DEGC,
     CAN_PROTOCOL_VOLTAGE_MAX_CENTIVOLTS, CAN_NODE_STATE_FAULT, 254, CAN_FAULT_CODE_NONE},
    {"thermal_saturated", CAN_ID_THERMAL_DATA, (int16_t)CAN_PROTOCOL_SATURATED_TEMPERATURE_RAW,
     1240, CAN_NODE_STATE_DEGRADED, 3, CAN_FAULT_CODE_SENSOR_SATURATION},
    {"thermal_counter_wrap", CAN_ID_THERMAL_DATA, 0, 1200, CAN_NODE_STATE_NORMAL, 255,
     CAN_FAULT_CODE_NONE},
    {"power_golden", CAN_ID_POWER_DATA, 310, 1242, CAN_NODE_STATE_NORMAL, 42,
     CAN_FAULT_CODE_NONE},
    {"power_zero", CAN_ID_POWER_DATA, 0, 900, CAN_NODE_STATE_BOOTING, 0, CAN_FAULT_CODE_NONE},
};

static const struct heartbeat_vector HEARTBEAT_VECTORS[] = {
    {"thermal_heartbeat_boot", CAN_ID_THERMAL_HEARTBEAT, 0, CAN_NODE_STATE_BOOTING,
     CAN_RESET_REASON_POWER_ON},
    {"thermal_heartbeat_software_reset", CAN_ID_THERMAL_HEARTBEAT, 42, CAN_NODE_STATE_NORMAL,
     CAN_RESET_REASON_SOFTWARE},
    {"thermal_heartbeat_watchdog", CAN_ID_THERMAL_HEARTBEAT, 7, CAN_NODE_STATE_DEGRADED,
     CAN_RESET_REASON_WATCHDOG},
    {"power_heartbeat_none", CAN_ID_POWER_HEARTBEAT, 1, CAN_NODE_STATE_NORMAL,
     CAN_RESET_REASON_NONE},
    {"power_heartbeat_large_uptime", CAN_ID_POWER_HEARTBEAT, 4294967295U, CAN_NODE_STATE_FAULT,
     CAN_RESET_REASON_POWER_ON},
};

static void print_hex(const uint8_t *bytes, size_t length)
{
    for (size_t index = 0; index < length; index++) {
        printf("%02X", bytes[index]);
    }
}

static int emit_data_vectors(void)
{
    uint8_t frame[CAN_PROTOCOL_DLC];
    can_data_payload_t decoded;
    int failures = 0;

    for (size_t index = 0; index < sizeof(DATA_VECTORS) / sizeof(DATA_VECTORS[0]); index++) {
        const struct data_vector *vector = &DATA_VECTORS[index];
        const can_data_payload_t payload = {
            .module_temperature_deci_degc = vector->temperature_deci_degc,
            .supply_voltage_centivolts = vector->voltage_centivolts,
            .node_state = vector->node_state,
            .sequence_counter = vector->sequence_counter,
            .fault_code = vector->fault_code,
        };

        can_protocol_encode_data(&payload, frame);
        memset(&decoded, 0, sizeof(decoded));
        can_protocol_decode_data(frame, &decoded);

        if (memcmp(&decoded, &payload, sizeof(payload)) != 0) {
            fprintf(stderr, "round trip failed for %s\n", vector->name);
            failures++;
        }

        printf("data name=%s id=0x%03X dlc=%d payload=", vector->name,
               (unsigned int)vector->identifier, CAN_PROTOCOL_DLC);
        print_hex(frame, CAN_PROTOCOL_DLC);
        printf(" temperature_deci_degc=%d voltage_centivolts=%u state=%d counter=%u fault=%d\n",
               (int)decoded.module_temperature_deci_degc,
               (unsigned int)decoded.supply_voltage_centivolts, (int)decoded.node_state,
               (unsigned int)decoded.sequence_counter, (int)decoded.fault_code);
    }
    return failures;
}

static int emit_heartbeat_vectors(void)
{
    uint8_t frame[CAN_PROTOCOL_DLC];
    can_heartbeat_payload_t decoded;
    int failures = 0;

    for (size_t index = 0; index < sizeof(HEARTBEAT_VECTORS) / sizeof(HEARTBEAT_VECTORS[0]);
         index++) {
        const struct heartbeat_vector *vector = &HEARTBEAT_VECTORS[index];
        const can_heartbeat_payload_t payload = {
            .uptime_seconds = vector->uptime_seconds,
            .node_state = vector->node_state,
            .reset_reason = vector->reset_reason,
        };

        can_protocol_encode_heartbeat(&payload, frame);
        memset(&decoded, 0, sizeof(decoded));
        can_protocol_decode_heartbeat(frame, &decoded);

        if (memcmp(&decoded, &payload, sizeof(payload)) != 0) {
            fprintf(stderr, "round trip failed for %s\n", vector->name);
            failures++;
        }

        printf("heartbeat name=%s id=0x%03X dlc=%d payload=", vector->name,
               (unsigned int)vector->identifier, CAN_PROTOCOL_DLC);
        print_hex(frame, CAN_PROTOCOL_DLC);
        printf(" uptime_seconds=%u state=%d reset_reason=%d\n",
               (unsigned int)decoded.uptime_seconds, (int)decoded.node_state,
               (int)decoded.reset_reason);
    }
    return failures;
}

static int emit_limits(void)
{
    printf("limits bitrate_bps=%d dlc=%d data_period_ms=%d heartbeat_period_ms=%d\n",
           CAN_PROTOCOL_BITRATE_BPS, CAN_PROTOCOL_DLC, CAN_PROTOCOL_DATA_PERIOD_MS,
           CAN_PROTOCOL_HEARTBEAT_PERIOD_MS);
    printf("limits temperature_min=%d temperature_max=%d voltage_min=%d voltage_max=%d\n",
           CAN_PROTOCOL_TEMPERATURE_MIN_DECI_DEGC, CAN_PROTOCOL_TEMPERATURE_MAX_DECI_DEGC,
           CAN_PROTOCOL_VOLTAGE_MIN_CENTIVOLTS, CAN_PROTOCOL_VOLTAGE_MAX_CENTIVOLTS);
    printf("limits saturated_raw=%d counter_modulus=%d\n",
           CAN_PROTOCOL_SATURATED_TEMPERATURE_RAW, CAN_PROTOCOL_COUNTER_MODULUS);
    return 0;
}

static int check_counter_behaviour(void)
{
    int failures = 0;

    if (can_protocol_next_counter(255) != 0) {
        fprintf(stderr, "counter did not wrap from 255 to 0\n");
        failures++;
    }
    if (can_protocol_advance_counter(250, 4) != 254) {
        fprintf(stderr, "counter skip produced the wrong value\n");
        failures++;
    }
    if (can_protocol_advance_counter(254, 4) != 2) {
        fprintf(stderr, "counter skip did not wrap\n");
        failures++;
    }
    if (can_protocol_clamp_temperature(99999) != CAN_PROTOCOL_TEMPERATURE_MAX_DECI_DEGC ||
        can_protocol_clamp_temperature(-99999) != CAN_PROTOCOL_TEMPERATURE_MIN_DECI_DEGC) {
        fprintf(stderr, "temperature clamp failed\n");
        failures++;
    }
    if (can_protocol_clamp_voltage(99999) != CAN_PROTOCOL_VOLTAGE_MAX_CENTIVOLTS ||
        can_protocol_clamp_voltage(0) != CAN_PROTOCOL_VOLTAGE_MIN_CENTIVOLTS) {
        fprintf(stderr, "voltage clamp failed\n");
        failures++;
    }
    if (can_protocol_temperature_in_range((int16_t)CAN_PROTOCOL_SATURATED_TEMPERATURE_RAW)) {
        fprintf(stderr, "saturated temperature was accepted as in range\n");
        failures++;
    }

    printf("counters wrap_255=%u skip_250_by_4=%u skip_254_by_4=%u\n",
           (unsigned int)can_protocol_next_counter(255),
           (unsigned int)can_protocol_advance_counter(250, 4),
           (unsigned int)can_protocol_advance_counter(254, 4));
    return failures;
}

int main(void)
{
    int failures = 0;

    failures += emit_limits();
    failures += emit_data_vectors();
    failures += emit_heartbeat_vectors();
    failures += check_counter_behaviour();

    if (failures != 0) {
        fprintf(stderr, "%d self check(s) failed\n", failures);
        return 1;
    }
    return 0;
}
