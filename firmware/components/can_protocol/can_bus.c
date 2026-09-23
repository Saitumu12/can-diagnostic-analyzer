#include "can_bus.h"

#include <string.h>

#include "driver/gpio.h"
#include "driver/twai.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "can_bus";

static can_bus_statistics_t s_statistics;
static bool s_started;

esp_err_t can_bus_start(void)
{
    twai_general_config_t general = TWAI_GENERAL_CONFIG_DEFAULT(
        (gpio_num_t)CAN_BUS_TWAI_TX_GPIO, (gpio_num_t)CAN_BUS_TWAI_RX_GPIO, TWAI_MODE_NORMAL);
    general.tx_queue_len = CAN_BUS_TX_QUEUE_LENGTH;
    general.rx_queue_len = CAN_BUS_RX_QUEUE_LENGTH;

    twai_timing_config_t timing = TWAI_TIMING_CONFIG_500KBITS();
    twai_filter_config_t filter = TWAI_FILTER_CONFIG_ACCEPT_ALL();

    esp_err_t status = twai_driver_install(&general, &timing, &filter);
    if (status != ESP_OK) {
        ESP_LOGE(TAG, "install_failed err=%s", esp_err_to_name(status));
        return status;
    }

    status = twai_start();
    if (status != ESP_OK) {
        ESP_LOGE(TAG, "start_failed err=%s", esp_err_to_name(status));
        twai_driver_uninstall();
        return status;
    }

    memset(&s_statistics, 0, sizeof(s_statistics));
    s_started = true;
    ESP_LOGI(TAG, "twai_ready tx_gpio=%d rx_gpio=%d bitrate=%d", CAN_BUS_TWAI_TX_GPIO,
             CAN_BUS_TWAI_RX_GPIO, CAN_PROTOCOL_BITRATE_BPS);
    return ESP_OK;
}

esp_err_t can_bus_transmit(uint32_t identifier, const uint8_t payload[CAN_PROTOCOL_DLC],
                           uint32_t timeout_ms)
{
    if (!s_started) {
        return ESP_ERR_INVALID_STATE;
    }

    twai_message_t message = {
        .identifier = identifier,
        .data_length_code = CAN_PROTOCOL_DLC,
    };
    memcpy(message.data, payload, CAN_PROTOCOL_DLC);

    esp_err_t status = twai_transmit(&message, pdMS_TO_TICKS(timeout_ms));
    if (status == ESP_OK) {
        s_statistics.transmitted++;
        return ESP_OK;
    }

    s_statistics.failed++;
    s_statistics.last_error = (int32_t)status;
    ESP_LOGW(TAG, "tx_failed id=0x%03X err=%s failed_total=%u", (unsigned int)identifier,
             esp_err_to_name(status), (unsigned int)s_statistics.failed);
    return status;
}

void can_bus_service(void)
{
    twai_status_info_t info;

    if (!s_started || twai_get_status_info(&info) != ESP_OK) {
        return;
    }

    if (info.state == TWAI_STATE_BUS_OFF) {
        ESP_LOGW(TAG, "bus_off tx_errors=%u rx_errors=%u", (unsigned int)info.tx_error_counter,
                 (unsigned int)info.rx_error_counter);
        if (twai_initiate_recovery() == ESP_OK) {
            s_statistics.bus_off_recoveries++;
        }
        return;
    }

    if (info.state == TWAI_STATE_STOPPED) {
        if (twai_start() == ESP_OK) {
            ESP_LOGI(TAG, "bus_restarted recoveries=%u",
                     (unsigned int)s_statistics.bus_off_recoveries);
        }
    }
}

void can_bus_get_statistics(can_bus_statistics_t *out)
{
    if (out != NULL) {
        *out = s_statistics;
    }
}

can_reset_reason_t can_bus_last_reset_reason(void)
{
    switch (esp_reset_reason()) {
    case ESP_RST_POWERON:
        return CAN_RESET_REASON_POWER_ON;
    case ESP_RST_SW:
        return CAN_RESET_REASON_SOFTWARE;
    case ESP_RST_INT_WDT:
    case ESP_RST_TASK_WDT:
    case ESP_RST_WDT:
        return CAN_RESET_REASON_WATCHDOG;
    default:
        return CAN_RESET_REASON_NONE;
    }
}
