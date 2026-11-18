#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>

// XIAO ESP32-C6 — USB-UART passthrough + BLE UART (Nordic NUS)
// D6 = GPIO16 = TX -> device RX
// D7 = GPIO17 = RX <- device TX
// LED = GPIO15 activity blink (only one user LED on this board)
// Boot: 3 short flashes + 1 long flash to show power-on
// BLE name: BLESerial + last 3 MAC bytes, e.g. BLESerialfffe21
// 3.3V logic both sides

static const uint32_t BAUD = 115200;
static const int LED = 15; // LED_BUILTIN

// Nordic UART Service
static const char *SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E";
static const char *CHAR_UUID_RX = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"; // phone writes
static const char *CHAR_UUID_TX = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"; // phone notifies

static const size_t BLE_NOTIFY_CHUNK = 20; // safe for default MTU
static const size_t BRIDGE_BUF = 128;

static uint32_t ledOffAt = 0;

static BLEServer *bleServer = nullptr;
static BLECharacteristic *bleTxChar = nullptr;
static volatile bool bleConnected = false;
static bool bleWasConnected = false;

// BLE writes land here; loop() drains to UART/USB
static uint8_t bleRxRing[512];
static volatile size_t bleRxHead = 0;
static volatile size_t bleRxTail = 0;

void ledOn() {
    digitalWrite(LED, LOW); // active low
}

void ledOff() {
    digitalWrite(LED, HIGH);
}

void ledActivity() {
    ledOn();
    ledOffAt = millis() + 30;
}

void bootFlash() {
    for (int i = 0; i < 3; i++) {
        ledOn();
        delay(80);
        ledOff();
        delay(80);
    }
    ledOn();
    delay(250);
    ledOff();
}

static void blePushRx(const uint8_t *data, size_t len) {
    for (size_t i = 0; i < len; i++) {
        size_t next = (bleRxHead + 1) % sizeof(bleRxRing);
        if (next == bleRxTail) {
            break; // ring full, drop
        }
        bleRxRing[bleRxHead] = data[i];
        bleRxHead = next;
    }
}

static void bleSend(const uint8_t *data, size_t len) {
    if (!bleConnected || !bleTxChar) {
        return;
    }
    while (len > 0) {
        size_t n = len > BLE_NOTIFY_CHUNK ? BLE_NOTIFY_CHUNK : len;
        bleTxChar->setValue(data, n);
        bleTxChar->notify();
        data += n;
        len -= n;
    }
}

class ServerCallbacks : public BLEServerCallbacks {
    void onConnect(BLEServer *pServer) override {
        bleConnected = true;
    }
    void onDisconnect(BLEServer *pServer) override {
        bleConnected = false;
    }
};

class RxCallbacks : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) override {
        String value = pCharacteristic->getValue();
        if (value.length() > 0) {
            blePushRx((const uint8_t *)value.c_str(), value.length());
        }
    }
};

void bleSetup() {
    uint64_t mac = ESP.getEfuseMac();
    // last 3 bytes of MAC in standard print order (MAC[3], MAC[4], MAC[5])
    uint8_t m3 = (mac >> 24) & 0xFF;
    uint8_t m4 = (mac >> 32) & 0xFF;
    uint8_t m5 = (mac >> 40) & 0xFF;

    char name[20];
    snprintf(name, sizeof(name), "BLESerial%02x%02x%02x", m3, m4, m5);

    BLEDevice::init(name);
    BLEDevice::setMTU(512);

    bleServer = BLEDevice::createServer();
    bleServer->setCallbacks(new ServerCallbacks());

    BLEService *service = bleServer->createService(SERVICE_UUID);

    bleTxChar = service->createCharacteristic(
        CHAR_UUID_TX, BLECharacteristic::PROPERTY_NOTIFY);

    BLECharacteristic *rxChar = service->createCharacteristic(
        CHAR_UUID_RX,
        BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
    rxChar->setCallbacks(new RxCallbacks());

    service->start();

    BLEAdvertising *advertising = BLEDevice::getAdvertising();
    advertising->addServiceUUID(SERVICE_UUID);
    advertising->setScanResponse(true);
    BLEDevice::startAdvertising();
}

void setup() {
    pinMode(LED, OUTPUT);
    ledOff();

    bootFlash();

    Serial.begin(BAUD);
    // must precede begin(): setRxBufferSize() is a no-op once the driver is installed
    Serial1.setRxBufferSize(4096);
    Serial1.begin(BAUD, SERIAL_8N1, 17 /* D7 RX */, 16 /* D6 TX */);

    bleSetup();
}

void loop() {
    uint8_t buf[BRIDGE_BUF];

    // USB -> UART + BLE
    size_t n = 0;
    while (Serial.available() && n < sizeof(buf)) {
        buf[n++] = (uint8_t)Serial.read();
    }
    if (n) {
        Serial1.write(buf, n);
        bleSend(buf, n);
        ledActivity();
    }

    // UART -> USB + BLE
    n = 0;
    while (Serial1.available() && n < sizeof(buf)) {
        buf[n++] = (uint8_t)Serial1.read();
    }
    if (n) {
        Serial.write(buf, n);
        bleSend(buf, n);
        ledActivity();
    }

    // BLE -> USB + UART
    n = 0;
    while (bleRxHead != bleRxTail && n < sizeof(buf)) {
        buf[n++] = bleRxRing[bleRxTail];
        bleRxTail = (bleRxTail + 1) % sizeof(bleRxRing);
    }
    if (n) {
        Serial.write(buf, n);
        Serial1.write(buf, n);
        ledActivity();
    }

    if (ledOffAt && millis() >= ledOffAt) {
        ledOff();
        ledOffAt = 0;
    }

    // reconnect advertising after drop
    if (!bleConnected && bleWasConnected) {
        delay(500);
        BLEDevice::startAdvertising();
    }
    bleWasConnected = bleConnected;

    if (!Serial.available() && !Serial1.available() && bleRxHead == bleRxTail) {
        delayMicroseconds(50);
    }
}
