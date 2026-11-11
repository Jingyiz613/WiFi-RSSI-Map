#pragma once
#include <esp_http_server.h>
void autoMotionRegister(httpd_handle_t server);
void autoMotionUpdate();
void autoMotionManualOverride();
bool autoMotionOwnsControl();
void autoMotionPrintKey();
