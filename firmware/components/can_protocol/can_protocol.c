#include "can_protocol.h"

#include <string.h>

static void write_u16_le(uint8_t *out, uint16_t value)
{
    out[0] = (uint8_t)(value & 0xFFU);
    out[1] = (uint8_t)((value >> 8) & 0xFFU);
}

static uint16_t read_u16_le(const uint8_t *in)
{
    return (uint16_t)((uint16_t)in[0] | ((uint16_t)in[1] << 8));
}

static void write_u32_le(uint8_t *out, uint32_t value)
{
    out[0] = (uint8_t)(value & 0xFFU);
    out[1] = (uint8_t)((value >> 8) & 0xFFU);
    out[2] = (uint8_t)((value >> 16) & 0xFFU);
    out[3] = (uint8_t)((value >> 24) & 0xFFU);
}

static uint32_t read_u32_le(const uint8_t *in)
{
    return (uint32_t)in[0] | ((uint32_t)in[1] << 8) | ((uint32_t)in[2] << 16) |
           ((uint32_t)in[3] << 24);
}

void can_protocol_encode_data(const can_data_payload_t *payload, uint8_t out[CAN_PROTOCOL_DLC])
{
    memset(out, 0, CAN_PROTOCOL_DLC);
    write_u16_le(&out[0], (uint16_t)payload->module_temperature_deci_degc);
    write_u16_le(&out[2], payload->supply_voltage_centivolts);
    out[4] = (uint8_t)payload->node_state;
    out[5] = payload->sequence_counter;
    out[6] = (uint8_t)payload->fault_code;
    out[7] = 0U;
}

void can_protocol_decode_data(const uint8_t frame[CAN_PROTOCOL_DLC], can_data_payload_t *out)
{
    out->module_temperature_deci_degc = (int16_t)read_u16_le(&frame[0]);
    out->supply_voltage_centivolts = read_u16_le(&frame[2]);
    out->node_state = (can_node_state_t)frame[4];
    out->sequence_counter = frame[5];
    out->fault_code = (can_fault_code_t)frame[6];
}

void can_protocol_encode_heartbeat(const can_heartbeat_payload_t *payload,
                                   uint8_t out[CAN_PROTOCOL_DLC])
{
    memset(out, 0, CAN_PROTOCOL_DLC);
    write_u32_le(&out[0], payload->uptime_seconds);
    out[4] = (uint8_t)payload->node_state;
    out[5] = (uint8_t)payload->reset_reason;
    out[6] = 0U;
    out[7] = 0U;
}

void can_protocol_decode_heartbeat(const uint8_t frame[CAN_PROTOCOL_DLC],
                                   can_heartbeat_payload_t *out)
{
    out->uptime_seconds = read_u32_le(&frame[0]);
    out->node_state = (can_node_state_t)frame[4];
    out->reset_reason = (can_reset_reason_t)frame[5];
}

uint8_t can_protocol_next_counter(uint8_t counter)
{
    return (uint8_t)((counter + 1U) % CAN_PROTOCOL_COUNTER_MODULUS);
}

uint8_t can_protocol_advance_counter(uint8_t counter, uint8_t steps)
{
    return (uint8_t)((counter + steps) % CAN_PROTOCOL_COUNTER_MODULUS);
}

int16_t can_protocol_clamp_temperature(int32_t deci_degc)
{
    if (deci_degc < CAN_PROTOCOL_TEMPERATURE_MIN_DECI_DEGC) {
        return (int16_t)CAN_PROTOCOL_TEMPERATURE_MIN_DECI_DEGC;
    }
    if (deci_degc > CAN_PROTOCOL_TEMPERATURE_MAX_DECI_DEGC) {
        return (int16_t)CAN_PROTOCOL_TEMPERATURE_MAX_DECI_DEGC;
    }
    return (int16_t)deci_degc;
}

uint16_t can_protocol_clamp_voltage(int32_t centivolts)
{
    if (centivolts < CAN_PROTOCOL_VOLTAGE_MIN_CENTIVOLTS) {
        return (uint16_t)CAN_PROTOCOL_VOLTAGE_MIN_CENTIVOLTS;
    }
    if (centivolts > CAN_PROTOCOL_VOLTAGE_MAX_CENTIVOLTS) {
        return (uint16_t)CAN_PROTOCOL_VOLTAGE_MAX_CENTIVOLTS;
    }
    return (uint16_t)centivolts;
}

bool can_protocol_temperature_in_range(int16_t deci_degc)
{
    return deci_degc >= CAN_PROTOCOL_TEMPERATURE_MIN_DECI_DEGC &&
           deci_degc <= CAN_PROTOCOL_TEMPERATURE_MAX_DECI_DEGC;
}

bool can_protocol_voltage_in_range(uint16_t centivolts)
{
    return centivolts >= CAN_PROTOCOL_VOLTAGE_MIN_CENTIVOLTS &&
           centivolts <= CAN_PROTOCOL_VOLTAGE_MAX_CENTIVOLTS;
}
