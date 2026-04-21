const int SENSOR_PIN       = 2;
const int DEBOUNCE_US      = 5000;
const unsigned long TIMEOUT_US     = 2000000UL;
const unsigned long REPORT_INTERVAL = 100;

volatile unsigned long lastPulseTime = 0;
volatile unsigned long pulseInterval = 0;

int numMagnets = 1;
unsigned long lastReport = 0;

void pulseISR() {
    unsigned long now = micros();
    unsigned long gap = now - lastPulseTime;
    if (gap < DEBOUNCE_US) return;
    pulseInterval = gap;
    lastPulseTime = now;
}

void setup() {
    Serial.begin(115200);
    pinMode(SENSOR_PIN, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(SENSOR_PIN), pulseISR, FALLING);
}

void loop() {
    if (Serial.available()) {
        String cmd = Serial.readStringUntil('\n');
        cmd.trim();
        if (cmd.startsWith("MAGNETS:")) {
            int n = cmd.substring(8).toInt();
            numMagnets = constrain(n, 1, 8);
        } else if (cmd == "PING") {
            Serial.println("PONG:RPM_SENSOR");
        }
    }

    if (millis() - lastReport >= REPORT_INTERVAL) {
        lastReport = millis();

        noInterrupts();
        unsigned long lastPulse = lastPulseTime;
        unsigned long interval  = pulseInterval;
        interrupts();

        float rpm = 0.0;
        if (interval > 0 && (micros() - lastPulse) < TIMEOUT_US) {
            rpm = 60000000.0 / ((float)interval * numMagnets);
        }

        Serial.print("RPM:");
        Serial.println(rpm, 1);
    }
}
