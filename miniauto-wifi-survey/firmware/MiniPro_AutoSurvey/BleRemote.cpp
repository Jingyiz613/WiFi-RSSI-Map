/**
 * @file BleRemote.cpp
 * @brief miniAuto BLE Nordic UART Service (NUS) transport driver
 *
 * BLE NUS UUIDs (Nordic Semiconductor standard):
 *   Service  : 6E400001-B5A3-F393-E0A9-E50E24DCCA9E
 *   RX Char  : 6E400002-B5A3-F393-E0A9-E50E24DCCA9E  (APP writes -> MCU)
 *   TX Char  : 6E400003-B5A3-F393-E0A9-E50E24DCCA9E  (MCU notifies -> APP)
 *
 * Downlink frame (APP -> MCU) : CMD|p1|p2|p3|$
 * Uplink   frame (MCU -> APP) : C|speed|$   /   $distance_mm,voltage_mv$
 *
 * RX data arrives via BLE callback (FreeRTOS task context).
 * All shared flags are declared volatile to prevent compiler reordering.
 */

#include "BleRemote.h"
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// =========================================================
// BLE NUS UUIDs
// =========================================================
#define NUS_SERVICE_UUID  "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define NUS_RX_UUID       "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
#define NUS_TX_UUID       "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

// =========================================================
// Internal state  (anonymous namespace = file-private)
// =========================================================
namespace {

// --- BLE objects ---
static BLEServer*         s_pServer   = nullptr;
static BLECharacteristic* s_pTxChar   = nullptr;
static volatile bool      s_connected = false;

// --- pending command flags (volatile: set in BLE task, read in main task) ---
static volatile bool s_hasMotion   = false;
static volatile bool s_hasRgb      = false;
static volatile bool s_hasSpeed    = false;
static volatile bool s_hasDataReq  = false;
static volatile bool s_hasServo    = false;
static volatile bool s_hasLift     = false;
static volatile bool s_hasAvoid    = false;

// --- command parameter cache ---
static int     s_motionState = 8;   // CMD A  (0~11, default = stop)
static uint8_t s_rgbR        = 0;   // CMD B
static uint8_t s_rgbG        = 0;
static uint8_t s_rgbB        = 0;
static uint8_t s_speed       = 50;  // CMD C  (0~100 %)
static int     s_servoOffset = 0;   // CMD E  (-90~90 deg) 机械爪开合
static int     s_liftCommand = 0;   // CMD G  (-90=降低, 0=复位, 90=抬升)
static int     s_avoidState  = 0;   // CMD F  (0/1)

// --- RX accumulation buffer ---
static String s_rxBuf = "";

// --- frame parser (called from BLE callback context) ---
static void parseFrame(const String& frame) {
#if BLE_REMOTE_DEBUG
    Serial.print("[BLE RX] ");
    Serial.println(frame);
#endif
    String rec[4] = {"", "", "", ""};
    String tmp = frame;
    int idx = 0;
    while (tmp.indexOf('|') != -1 && idx < 4) {
        rec[idx] = tmp.substring(0, tmp.indexOf('|'));
        tmp      = tmp.substring(tmp.indexOf('|') + 1);
        idx++;
    }
    if (rec[0] == "A") {
        s_motionState = rec[1].toInt();
        s_hasMotion   = true;
    } else if (rec[0] == "B") {
        s_rgbR   = (uint8_t)constrain(rec[1].toInt(), 0, 255);
        s_rgbG   = (uint8_t)constrain(rec[2].toInt(), 0, 255);
        s_rgbB   = (uint8_t)constrain(rec[3].toInt(), 0, 255);
        s_hasRgb = true;
    } else if (rec[0] == "C") {
        s_speed    = (uint8_t)constrain(rec[1].toInt(), 0, 100);
        s_hasSpeed = true;
    } else if (rec[0] == "D") {
        s_hasDataReq = true;
    } else if (rec[0] == "E") {
        s_servoOffset = constrain(rec[1].toInt(), -90, 90);
        s_hasServo    = true;
    } else if (rec[0] == "F") {
        s_avoidState = rec[1].toInt();
        s_hasAvoid   = true;
    } else if (rec[0] == "G") {
        const int cmd = rec[1].toInt();
        if (cmd > 0) {
            s_liftCommand = 90;
        } else if (cmd < 0) {
            s_liftCommand = -90;
        } else {
            s_liftCommand = 0;
        }
        s_hasLift = true;
    }
}

// --- BLE server connection callbacks ---
class ServerCallbacks : public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) override {
        (void)pServer;
        s_connected = true;
        Serial.println("[BLE] Client connected");
    }
    void onDisconnect(BLEServer* pServer) override {
        (void)pServer;
        s_connected = false;
        s_rxBuf     = "";
        Serial.println("[BLE] Client disconnected, restarting advertising");
        BLEDevice::startAdvertising();
    }
};

// --- RX characteristic callback: APP writes frame bytes here ---
class RxCallbacks : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic* pChar) override {
        String raw(pChar->getValue().c_str());
#if BLE_REMOTE_DEBUG
        // Print every raw byte received over BLE
        Serial.print("[BLE RAW] len=");
        Serial.print(raw.length());
        Serial.print(" data=\"");
        Serial.print(raw);
        Serial.println("\"");
#endif
        for (char c : raw) {
            if (c == '$') {
                if (s_rxBuf.length() > 0) {
                    parseFrame(s_rxBuf);
                    s_rxBuf = "";
                }
            } else {
                // Guard against runaway buffer (e.g. malformed frames)
                if (s_rxBuf.length() < 64) {
                    s_rxBuf += c;
                } else {
                    s_rxBuf = "";   // discard and resync
                }
            }
        }
    }
};

}  // namespace

// =========================================================
// Public API
// =========================================================

void bleRemoteInit() {
    Serial.begin(115200);
    Serial.println("[BLE] Initializing NUS service...");
    BLEDevice::init("miniAutoPro");

    s_pServer = BLEDevice::createServer();
    s_pServer->setCallbacks(new ServerCallbacks());

    // Create NUS service
    BLEService* pService = s_pServer->createService(NUS_SERVICE_UUID);

    // TX characteristic: MCU -> APP (notify + read)
    s_pTxChar = pService->createCharacteristic(
        NUS_TX_UUID,
        BLECharacteristic::PROPERTY_NOTIFY | BLECharacteristic::PROPERTY_READ
    );
    s_pTxChar->addDescriptor(new BLE2902());

    // RX characteristic: APP -> MCU (write / write-without-response)
    BLECharacteristic* pRxChar = pService->createCharacteristic(
        NUS_RX_UUID,
        BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR
    );
    pRxChar->setCallbacks(new RxCallbacks());

    pService->start();

    // Advertise NUS service so the APP can discover the device
    BLEAdvertising* pAdv = BLEDevice::getAdvertising();
    pAdv->addServiceUUID(NUS_SERVICE_UUID);
    pAdv->setScanResponse(true);
    pAdv->setMinPreferred(0x06);   // helps iPhone connectivity
    pAdv->setMinPreferred(0x12);
    BLEDevice::startAdvertising();
    Serial.println("[BLE] Advertising as \"MiniAutoPro\" (NUS)");
}

// BLE data arrives via RX callbacks - no serial polling required.
// This function is retained for API compatibility with BleRemoteControl.
void bleRemoteUpdate() {}

bool bleRemoteIsConnected() {
    return s_connected;
}

bool bleRemoteConsumeMotionState(int& state) {
    if (!s_hasMotion) return false;
    state       = s_motionState;
    s_hasMotion = false;
    return true;
}

bool bleRemoteConsumeRgbCommand(uint8_t& r, uint8_t& g, uint8_t& b) {
    if (!s_hasRgb) return false;
    r = s_rgbR;  g = s_rgbG;  b = s_rgbB;
    s_hasRgb = false;
    return true;
}

bool bleRemoteConsumeSpeedCommand(uint8_t& speed) {
    if (!s_hasSpeed) return false;
    speed      = s_speed;
    s_hasSpeed = false;
    return true;
}

bool bleRemoteConsumeDataRequest() {
    if (!s_hasDataReq) return false;
    s_hasDataReq = false;
    return true;
}

bool bleRemoteConsumeServoCommand(int& offset) {
    if (!s_hasServo) return false;
    offset     = s_servoOffset;
    s_hasServo = false;
    return true;
}

bool bleRemoteConsumeLiftCommand(int& command) {
    if (!s_hasLift) return false;
    command   = s_liftCommand;
    s_hasLift = false;
    return true;
}

bool bleRemoteConsumeAvoidCommand(int& state) {
    if (!s_hasAvoid) return false;
    state      = s_avoidState;
    s_hasAvoid = false;
    return true;
}

// Uplink: speed acknowledgement frame  C|speed|$
void bleRemoteSendSpeedAck(uint8_t speed) {
    if (!s_connected || s_pTxChar == nullptr) return;
    String msg = "C|" + String(speed) + "|$";
    Serial.print("[BLE TX] ");
    Serial.println(msg);
    s_pTxChar->setValue(msg.c_str());
    s_pTxChar->notify();
}

// Uplink: sensor data frame  $distance_mm,voltage_mv$
void bleRemoteSendSensorData(uint16_t distance_mm, uint32_t voltage_mv) {
    if (!s_connected || s_pTxChar == nullptr) return;
    String msg = "$" + String(distance_mm) + "," + String(voltage_mv) + "$";
    Serial.print("[BLE TX] ");
    Serial.println(msg);
    s_pTxChar->setValue(msg.c_str());
    s_pTxChar->notify();
}
