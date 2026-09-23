#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "can_bus.h"
#include "can_protocol.h"
#include "driver/uart.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#define THERMAL_TX_TIMEOUT_MS 20
#define THERMAL_TICKS_PER_HEARTBEAT \
    (CAN_PROTOCOL_HEARTBEAT_PERIOD_MS / CAN_PROTOCOL_DATA_PERIOD_MS)
#define THERMAL_TICKS_PER_REPORT 100

#define THERMAL_COMMAND_UART UART_NUM_0
#define THERMAL_COMMAND_BAUD 115200
#define THERMAL_COMMAND_LINE_BYTES 96
#define THERMAL_COMMAND_RX_BYTES 512
#define THERMAL_COMMAND_STACK_WORDS 3072
#define THERMAL_COMMAND_PRIORITY 4

#define THERMAL_MAX_INJECTION_SECONDS 600
#define THERMAL_MAX_COUNTER_SKIP 255
#define THERMAL_RESTART_GRACE_MS 50

typedef enum {
    THERMAL_INJECTION_NONE = 0,
    THERMAL_INJECTION_RANGE = 1,
    THERMAL_INJECTION_SILENT = 2,
} thermal_injection_t;

static const char *TAG = "thermal_controller";

static uint8_t s_frame[CAN_PROTOCOL_DLC];
static uint8_t s_sequence_counter;
static uint32_t s_tick_index;
static can_reset_reason_t s_reset_reason;

static SemaphoreHandle_t s_state_lock;
static StaticSemaphore_t s_state_lock_storage;
static thermal_injection_t s_injection_mode;
static TickType_t s_injection_deadline;
static uint8_t s_pending_counter_skip;

static StaticTask_t s_command_task_storage;
static StackType_t s_command_task_stack[THERMAL_COMMAND_STACK_WORDS];
static char s_command_line[THERMAL_COMMAND_LINE_BYTES];
static size_t s_command_length;

static bool deadline_passed(TickType_t deadline)
{
    return (int32_t)(xTaskGetTickCount() - deadline) >= 0;
}

static void lock_state(void)
{
    xSemaphoreTake(s_state_lock, portMAX_DELAY);
}

static void unlock_state(void)
{
    xSemaphoreGive(s_state_lock);
}

static void set_injection(thermal_injection_t mode, uint32_t seconds)
{
    lock_state();
    s_injection_mode = mode;
    s_injection_deadline = xTaskGetTickCount() + pdMS_TO_TICKS(seconds * 1000U);
    unlock_state();
}

static void clear_injection(void)
{
    lock_state();
    s_injection_mode = THERMAL_INJECTION_NONE;
    unlock_state();
}

static void queue_counter_skip(uint8_t steps)
{
    lock_state();
    s_pending_counter_skip = steps;
    unlock_state();
}

static thermal_injection_t active_injection(void)
{
    thermal_injection_t mode;
    bool expired = false;

    lock_state();
    if (s_injection_mode != THERMAL_INJECTION_NONE && deadline_passed(s_injection_deadline)) {
        s_injection_mode = THERMAL_INJECTION_NONE;
        expired = true;
    }
    mode = s_injection_mode;
    unlock_state();

    if (expired) {
        printf("INJECT mode=none reason=expired\n");
    }
    return mode;
}

static uint8_t take_counter_skip(void)
{
    uint8_t steps;

    lock_state();
    steps = s_pending_counter_skip;
    s_pending_counter_skip = 0U;
    unlock_state();
    return steps;
}

static float elapsed_seconds(uint32_t tick_index)
{
    return (float)tick_index * ((float)CAN_PROTOCOL_DATA_PERIOD_MS / 1000.0f);
}

static int16_t simulated_temperature(uint32_t tick_index)
{
    float seconds = elapsed_seconds(tick_index);
    float degrees = 24.5f + 6.0f * sinf(2.0f * (float)M_PI * seconds / 17.0f);
    return can_protocol_clamp_temperature((int32_t)lroundf(degrees * 10.0f));
}

static uint16_t simulated_voltage(uint32_t tick_index)
{
    float seconds = elapsed_seconds(tick_index);
    float volts = 12.36f + 0.14f * sinf(2.0f * (float)M_PI * seconds / 13.0f);
    return can_protocol_clamp_voltage((int32_t)lroundf(volts * 100.0f));
}

static void transmit_data_frame(thermal_injection_t injection)
{
    uint8_t skip = take_counter_skip();
    if (skip != 0U) {
        s_sequence_counter = can_protocol_advance_counter(s_sequence_counter, skip);
        printf("INJECT mode=skip applied=%u next_seq=%u\n", (unsigned int)skip,
               (unsigned int)s_sequence_counter);
    }

    can_data_payload_t payload = {
        .supply_voltage_centivolts = simulated_voltage(s_tick_index),
        .sequence_counter = s_sequence_counter,
    };

    if (injection == THERMAL_INJECTION_RANGE) {
        payload.module_temperature_deci_degc = (int16_t)CAN_PROTOCOL_SATURATED_TEMPERATURE_RAW;
        payload.node_state = CAN_NODE_STATE_DEGRADED;
        payload.fault_code = CAN_FAULT_CODE_SENSOR_SATURATION;
    } else {
        payload.module_temperature_deci_degc = simulated_temperature(s_tick_index);
        payload.node_state = CAN_NODE_STATE_NORMAL;
        payload.fault_code = CAN_FAULT_CODE_NONE;
    }

    can_protocol_encode_data(&payload, s_frame);
    can_bus_transmit(CAN_ID_THERMAL_DATA, s_frame, THERMAL_TX_TIMEOUT_MS);
    s_sequence_counter = can_protocol_next_counter(s_sequence_counter);
}

static void transmit_heartbeat_frame(thermal_injection_t injection)
{
    const can_heartbeat_payload_t payload = {
        .uptime_seconds = (uint32_t)(s_tick_index / THERMAL_TICKS_PER_HEARTBEAT),
        .node_state = (injection == THERMAL_INJECTION_RANGE) ? CAN_NODE_STATE_DEGRADED
                                                             : CAN_NODE_STATE_NORMAL,
        .reset_reason = s_reset_reason,
    };

    can_protocol_encode_heartbeat(&payload, s_frame);
    can_bus_transmit(CAN_ID_THERMAL_HEARTBEAT, s_frame, THERMAL_TX_TIMEOUT_MS);
}

static void report_statistics(void)
{
    can_bus_statistics_t statistics;

    can_bus_get_statistics(&statistics);
    printf("STAT node=ThermalController tx=%u fail=%u recoveries=%u last_err=%d seq=%u\n",
           (unsigned int)statistics.transmitted, (unsigned int)statistics.failed,
           (unsigned int)statistics.bus_off_recoveries, (int)statistics.last_error,
           (unsigned int)s_sequence_counter);
}

static bool parse_count(const char *argument, uint32_t limit, uint32_t *out)
{
    char *end = NULL;
    long value;

    if (argument == NULL) {
        return false;
    }

    value = strtol(argument, &end, 10);
    if (end == argument || *end != '\0' || value <= 0 || (uint32_t)value > limit) {
        return false;
    }

    *out = (uint32_t)value;
    return true;
}

static void handle_command(char *line)
{
    char *saved = NULL;
    char *verb = strtok_r(line, " \t", &saved);
    char *argument;
    uint32_t count = 0U;

    if (verb == NULL) {
        return;
    }
    argument = strtok_r(NULL, " \t", &saved);

    if (strcmp(verb, "normal") == 0) {
        clear_injection();
        printf("CMD ok=normal\n");
        return;
    }
    if (strcmp(verb, "range") == 0 &&
        parse_count(argument, THERMAL_MAX_INJECTION_SECONDS, &count)) {
        set_injection(THERMAL_INJECTION_RANGE, count);
        printf("CMD ok=range seconds=%u\n", (unsigned int)count);
        return;
    }
    if (strcmp(verb, "silent") == 0 &&
        parse_count(argument, THERMAL_MAX_INJECTION_SECONDS, &count)) {
        set_injection(THERMAL_INJECTION_SILENT, count);
        printf("CMD ok=silent seconds=%u\n", (unsigned int)count);
        return;
    }
    if (strcmp(verb, "skip") == 0 && parse_count(argument, THERMAL_MAX_COUNTER_SKIP, &count)) {
        queue_counter_skip((uint8_t)count);
        printf("CMD ok=skip steps=%u\n", (unsigned int)count);
        return;
    }
    if (strcmp(verb, "restart") == 0) {
        printf("CMD ok=restart\n");
        fflush(stdout);
        vTaskDelay(pdMS_TO_TICKS(THERMAL_RESTART_GRACE_MS));
        esp_restart();
    }

    printf("CMD err=unknown verb=%s\n", verb);
}

static void command_task(void *parameters)
{
    uint8_t byte;

    (void)parameters;
    for (;;) {
        if (uart_read_bytes(THERMAL_COMMAND_UART, &byte, 1, pdMS_TO_TICKS(100)) != 1) {
            continue;
        }

        if (byte == '\r' || byte == '\n') {
            if (s_command_length > 0U) {
                s_command_line[s_command_length] = '\0';
                handle_command(s_command_line);
                s_command_length = 0U;
            }
            continue;
        }

        if (s_command_length + 1U < sizeof(s_command_line)) {
            s_command_line[s_command_length++] = (char)byte;
        } else {
            s_command_length = 0U;
            printf("CMD err=too_long\n");
        }
    }
}

static esp_err_t start_command_interface(void)
{
    const uart_config_t configuration = {
        .baud_rate = THERMAL_COMMAND_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    esp_err_t status;

    status = uart_driver_install(THERMAL_COMMAND_UART, THERMAL_COMMAND_RX_BYTES, 0, 0, NULL, 0);
    if (status != ESP_OK) {
        return status;
    }

    status = uart_param_config(THERMAL_COMMAND_UART, &configuration);
    if (status != ESP_OK) {
        uart_driver_delete(THERMAL_COMMAND_UART);
        return status;
    }

    if (xTaskCreateStatic(command_task, "thermal_cmd", THERMAL_COMMAND_STACK_WORDS, NULL,
                          THERMAL_COMMAND_PRIORITY, s_command_task_stack,
                          &s_command_task_storage) == NULL) {
        uart_driver_delete(THERMAL_COMMAND_UART);
        return ESP_FAIL;
    }

    return ESP_OK;
}

void app_main(void)
{
    s_reset_reason = can_bus_last_reset_reason();
    printf("BOOT node=ThermalController reset_reason=%d\n", (int)s_reset_reason);

    s_state_lock = xSemaphoreCreateMutexStatic(&s_state_lock_storage);

    if (can_bus_start() != ESP_OK) {
        ESP_LOGE(TAG, "bus_start_failed");
        return;
    }
    if (start_command_interface() != ESP_OK) {
        ESP_LOGE(TAG, "command_interface_failed");
        return;
    }

    printf("READY commands=normal|range <s>|silent <s>|skip <n>|restart\n");
    transmit_heartbeat_frame(THERMAL_INJECTION_NONE);

    TickType_t next_wake = xTaskGetTickCount();
    for (;;) {
        thermal_injection_t injection = active_injection();

        can_bus_service();

        if (injection != THERMAL_INJECTION_SILENT) {
            transmit_data_frame(injection);
            if (s_tick_index != 0U && (s_tick_index % THERMAL_TICKS_PER_HEARTBEAT) == 0U) {
                transmit_heartbeat_frame(injection);
            }
        }
        if (s_tick_index != 0U && (s_tick_index % THERMAL_TICKS_PER_REPORT) == 0U) {
            report_statistics();
        }

        s_tick_index++;
        xTaskDelayUntil(&next_wake, pdMS_TO_TICKS(CAN_PROTOCOL_DATA_PERIOD_MS));
    }
}
