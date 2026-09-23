#include <math.h>
#include <stdio.h>

#include "can_bus.h"
#include "can_protocol.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define POWER_TX_TIMEOUT_MS 20
#define POWER_TICKS_PER_HEARTBEAT (CAN_PROTOCOL_HEARTBEAT_PERIOD_MS / CAN_PROTOCOL_DATA_PERIOD_MS)
#define POWER_TICKS_PER_REPORT 100

static const char *TAG = "power_monitor";

static uint8_t s_frame[CAN_PROTOCOL_DLC];
static uint8_t s_sequence_counter;
static uint32_t s_tick_index;
static can_reset_reason_t s_reset_reason;

static float elapsed_seconds(uint32_t tick_index)
{
    return (float)tick_index * ((float)CAN_PROTOCOL_DATA_PERIOD_MS / 1000.0f);
}

static int16_t simulated_temperature(uint32_t tick_index)
{
    float seconds = elapsed_seconds(tick_index);
    float degrees = 31.0f + 4.0f * sinf(2.0f * (float)M_PI * seconds / 23.0f);
    return can_protocol_clamp_temperature((int32_t)lroundf(degrees * 10.0f));
}

static uint16_t simulated_voltage(uint32_t tick_index)
{
    float seconds = elapsed_seconds(tick_index);
    float volts = 12.42f + 0.18f * sinf(2.0f * (float)M_PI * seconds / 11.0f);
    return can_protocol_clamp_voltage((int32_t)lroundf(volts * 100.0f));
}

static void transmit_data_frame(void)
{
    const can_data_payload_t payload = {
        .module_temperature_deci_degc = simulated_temperature(s_tick_index),
        .supply_voltage_centivolts = simulated_voltage(s_tick_index),
        .node_state = CAN_NODE_STATE_NORMAL,
        .sequence_counter = s_sequence_counter,
        .fault_code = CAN_FAULT_CODE_NONE,
    };

    can_protocol_encode_data(&payload, s_frame);
    can_bus_transmit(CAN_ID_POWER_DATA, s_frame, POWER_TX_TIMEOUT_MS);
    s_sequence_counter = can_protocol_next_counter(s_sequence_counter);
}

static void transmit_heartbeat_frame(void)
{
    const can_heartbeat_payload_t payload = {
        .uptime_seconds = (uint32_t)(s_tick_index / POWER_TICKS_PER_HEARTBEAT),
        .node_state = CAN_NODE_STATE_NORMAL,
        .reset_reason = s_reset_reason,
    };

    can_protocol_encode_heartbeat(&payload, s_frame);
    can_bus_transmit(CAN_ID_POWER_HEARTBEAT, s_frame, POWER_TX_TIMEOUT_MS);
}

static void report_statistics(void)
{
    can_bus_statistics_t statistics;

    can_bus_get_statistics(&statistics);
    printf("STAT node=PowerMonitor tx=%u fail=%u recoveries=%u last_err=%d seq=%u\n",
           (unsigned int)statistics.transmitted, (unsigned int)statistics.failed,
           (unsigned int)statistics.bus_off_recoveries, (int)statistics.last_error,
           (unsigned int)s_sequence_counter);
}

void app_main(void)
{
    s_reset_reason = can_bus_last_reset_reason();
    printf("BOOT node=PowerMonitor reset_reason=%d\n", (int)s_reset_reason);

    if (can_bus_start() != ESP_OK) {
        ESP_LOGE(TAG, "bus_start_failed");
        return;
    }

    transmit_heartbeat_frame();

    TickType_t next_wake = xTaskGetTickCount();
    for (;;) {
        can_bus_service();
        transmit_data_frame();

        if (s_tick_index != 0U && (s_tick_index % POWER_TICKS_PER_HEARTBEAT) == 0U) {
            transmit_heartbeat_frame();
        }
        if (s_tick_index != 0U && (s_tick_index % POWER_TICKS_PER_REPORT) == 0U) {
            report_statistics();
        }

        s_tick_index++;
        xTaskDelayUntil(&next_wake, pdMS_TO_TICKS(CAN_PROTOCOL_DATA_PERIOD_MS));
    }
}
