idf_component_register(SRCS "thermal_controller_main.c"
                       INCLUDE_DIRS "."
                       REQUIRES can_protocol driver esp_system)
