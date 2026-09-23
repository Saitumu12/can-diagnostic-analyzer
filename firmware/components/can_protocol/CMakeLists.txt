idf_component_register(SRCS "can_protocol.c" "can_bus.c"
                       INCLUDE_DIRS "include"
                       REQUIRES driver esp_system log freertos)
