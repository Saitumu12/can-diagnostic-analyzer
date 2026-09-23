#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "can_protocol.h"
#include "esp_err.h"
#include "sdkconfig.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CAN_BUS_TWAI_TX_GPIO CONFIG_CAN_BENCH_TWAI_TX_GPIO
#define CAN_BUS_TWAI_RX_GPIO CONFIG_CAN_BENCH_TWAI_RX_GPIO
#define CAN_BUS_TX_QUEUE_LENGTH 8
#define CAN_BUS_RX_QUEUE_LENGTH 8

typedef struct {
    uint32_t transmitted;
    uint32_t failed;
    uint32_t bus_off_recoveries;
    int32_t last_error;
} can_bus_statistics_t;

esp_err_t can_bus_start(void);

esp_err_t can_bus_transmit(uint32_t identifier, const uint8_t payload[CAN_PROTOCOL_DLC],
                           uint32_t timeout_ms);

void can_bus_service(void);

void can_bus_get_statistics(can_bus_statistics_t *out);

can_reset_reason_t can_bus_last_reset_reason(void);

#ifdef __cplusplus
}
#endif
