/*
 * KODA Arduino Firmware — Motors + Servos + Closed-Loop Rotation (MPU6050)
 *
 * Hardware:
 *   - Arduino Uno + Adafruit Motor Shield v1 (L293D)
 *       M1 = right DC motor       (rotation right => M1 forward, M3 backward)
 *       M3 = left DC motor
 *       Servo 1 (pin 9)  = right arm
 *       Servo 2 (pin 10) = left arm
 *       Servo 3 (pin 11 reused via wire) = head/Nextion
 *   - MPU6050 gyroscope on I2C
 *       SDA -> A4
 *       SCL -> A5
 *       VCC -> 5V (the GY-521 breakout has a 3.3V regulator)
 *       GND -> GND
 *
 * Serial protocol (9600 baud):
 *
 *   Single-byte commands (legacy, immediate):
 *     F = forward         (continuous, caller sends 'S' to stop)
 *     B = backward        (continuous)
 *     S = stop motors
 *     H = hello           (both arms wave)
 *     T = head servo bump
 *     G = left  arm servo
 *     D = right arm servo
 *     A = all servos
 *     + = speed up
 *     - = speed down
 *     ? = report status -> "STATUS:speed=N,gyro=ok|nok\n"
 *
 *   Extended commands (closed-loop, line terminated):
 *     L<aaa>\n  = rotate left  by <aaa> degrees using gyro integration
 *     R<aaa>\n  = rotate right by <aaa> degrees using gyro integration
 *     Examples: "L045\n", "R180\n", "L090\n"
 *     Response when complete:
 *       DONE:<actual_angle>\n      e.g. "DONE:46\n"   (slight overshoot is normal)
 *       ERR:<reason>\n             e.g. "ERR:nogyro\n", "ERR:timeout\n"
 *
 * Why closed-loop?
 *   The previous open-loop timing model (slope=60deg/s, offset=3.3deg) drifts
 *   with battery voltage, floor friction and wheel slip. The MPU6050 reads
 *   the actual yaw rate at 200 Hz; we integrate it in real time and stop the
 *   motors when the target angle is reached. Typical accuracy: +-2 deg.
 */

#include <Wire.h>
#include <AFMotor.h>
#include <Servo.h>

// ---------- Hardware setup ----------
AF_DCMotor motor_right(1);   // M1
AF_DCMotor motor_left(3);    // M3
Servo servo_right_arm;       // pin 9
Servo servo_left_arm;        // pin 10
Servo servo_head;            // pin 11 (Nextion holder)

const uint8_t SERVO_RIGHT_ARM_PIN = 9;
const uint8_t SERVO_LEFT_ARM_PIN  = 10;
const uint8_t SERVO_HEAD_PIN      = 11;

// Default motor speed (0-255). Adjust with '+' / '-' from the Pi.
uint8_t motor_speed = 200;

// ---------- MPU6050 ----------
const uint8_t MPU6050_ADDR = 0x68;
const uint8_t REG_PWR_MGMT_1 = 0x6B;
const uint8_t REG_GYRO_CONFIG = 0x1B;
const uint8_t REG_GYRO_ZOUT_H = 0x47;

// At +-250 deg/s full scale, the sensitivity is 131 LSB per deg/s
// (see MPU6050 register map section 4.19). Higher ranges trade resolution
// for headroom but our chassis rotates well under 100 deg/s.
const float GYRO_Z_SCALE_DPS_PER_LSB = 1.0f / 131.0f;

bool mpu_ok = false;
float gyro_z_bias = 0.0f;   // measured at boot while the robot is still

// Closed-loop rotation parameters
const unsigned long ROTATION_SAMPLE_INTERVAL_US = 5000UL;   // 200 Hz integration
const unsigned long ROTATION_TIMEOUT_MS = 6000UL;           // safety: never spin >6s
const float ROTATION_EARLY_BRAKE_DEG = 6.0f;                // slow down within this much of target
const uint8_t ROTATION_BRAKE_SPEED = 110;                   // speed for the brake phase

// ---------- Serial line buffer (for L/R extended commands) ----------
char line_buf[8];
uint8_t line_len = 0;

// ---------- I2C helpers ----------
bool mpu_write(uint8_t reg, uint8_t value) {
    Wire.beginTransmission(MPU6050_ADDR);
    Wire.write(reg);
    Wire.write(value);
    return Wire.endTransmission() == 0;
}

bool mpu_read_bytes(uint8_t reg, uint8_t *out, uint8_t n) {
    Wire.beginTransmission(MPU6050_ADDR);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0) return false;
    if (Wire.requestFrom((int)MPU6050_ADDR, (int)n, (int)true) != n) return false;
    for (uint8_t i = 0; i < n; i++) out[i] = Wire.read();
    return true;
}

bool mpu_setup() {
    Wire.begin();
    Wire.setClock(400000UL);  // fast-mode I2C
    if (!mpu_write(REG_PWR_MGMT_1, 0x00)) return false;          // wake up
    if (!mpu_write(REG_GYRO_CONFIG, 0x00)) return false;         // +-250 deg/s
    delay(50);
    return true;
}

int16_t mpu_read_gyro_z_raw() {
    uint8_t buf[2];
    if (!mpu_read_bytes(REG_GYRO_ZOUT_H, buf, 2)) return 0;
    return (int16_t)((buf[0] << 8) | buf[1]);
}

void mpu_calibrate_bias() {
    // Robot MUST stay still during boot. We average 200 samples over ~1 s.
    const int SAMPLES = 200;
    long sum = 0;
    for (int i = 0; i < SAMPLES; i++) {
        sum += mpu_read_gyro_z_raw();
        delay(5);
    }
    gyro_z_bias = (float)sum / (float)SAMPLES;
}

// ---------- Motor primitives ----------
void motors_stop() {
    motor_right.run(RELEASE);
    motor_left.run(RELEASE);
}

void motors_forward(uint8_t speed) {
    motor_right.setSpeed(speed);
    motor_left.setSpeed(speed);
    motor_right.run(FORWARD);
    motor_left.run(FORWARD);
}

void motors_backward(uint8_t speed) {
    motor_right.setSpeed(speed);
    motor_left.setSpeed(speed);
    motor_right.run(BACKWARD);
    motor_left.run(BACKWARD);
}

// Differential rotation: one motor forward, the other backward.
void motors_rotate_right(uint8_t speed) {
    motor_right.setSpeed(speed);
    motor_left.setSpeed(speed);
    motor_right.run(BACKWARD);
    motor_left.run(FORWARD);
}

void motors_rotate_left(uint8_t speed) {
    motor_right.setSpeed(speed);
    motor_left.setSpeed(speed);
    motor_right.run(FORWARD);
    motor_left.run(BACKWARD);
}

// ---------- Closed-loop rotation ----------
// direction: +1 = right, -1 = left. target_deg is always positive.
// Returns the integrated angle actually achieved.
float rotate_closed_loop(int direction, float target_deg) {
    if (!mpu_ok) {
        Serial.println(F("ERR:nogyro"));
        return 0.0f;
    }
    if (target_deg < 0.5f) {
        Serial.println(F("DONE:0"));
        return 0.0f;
    }

    // Start at full speed in the requested direction.
    if (direction > 0) motors_rotate_right(motor_speed);
    else               motors_rotate_left(motor_speed);

    float yaw = 0.0f;
    unsigned long t_start = millis();
    unsigned long t_last = micros();
    bool braking = false;

    while (fabs(yaw) < target_deg) {
        // Safety: never spin forever if the gyro lies or wheels slip.
        if (millis() - t_start > ROTATION_TIMEOUT_MS) {
            motors_stop();
            Serial.println(F("ERR:timeout"));
            return yaw;
        }

        // Sample at ~200 Hz. delayMicroseconds is more accurate than delay() here.
        unsigned long t_now = micros();
        unsigned long dt_us = t_now - t_last;
        if (dt_us < ROTATION_SAMPLE_INTERVAL_US) {
            delayMicroseconds(ROTATION_SAMPLE_INTERVAL_US - dt_us);
            t_now = micros();
            dt_us = t_now - t_last;
        }
        t_last = t_now;

        float rate_dps = ((float)mpu_read_gyro_z_raw() - gyro_z_bias)
                         * GYRO_Z_SCALE_DPS_PER_LSB;
        // Integrate (degrees = rate * dt_seconds). Sign of rate gives direction.
        yaw += rate_dps * (dt_us / 1000000.0f);

        // Brake phase: as we approach the target, drop motor speed so we
        // overshoot less. Without this, the chassis inertia adds 5-10 deg.
        float remaining = target_deg - fabs(yaw);
        if (!braking && remaining < ROTATION_EARLY_BRAKE_DEG) {
            braking = true;
            if (direction > 0) motors_rotate_right(ROTATION_BRAKE_SPEED);
            else               motors_rotate_left(ROTATION_BRAKE_SPEED);
        }
    }

    motors_stop();
    // Brief tail wait so the chassis settles before reporting; the integration
    // keeps running so the reported angle includes the coast.
    unsigned long settle_until = millis() + 150UL;
    while (millis() < settle_until) {
        unsigned long t_now = micros();
        float rate_dps = ((float)mpu_read_gyro_z_raw() - gyro_z_bias)
                         * GYRO_Z_SCALE_DPS_PER_LSB;
        yaw += rate_dps * ((t_now - t_last) / 1000000.0f);
        t_last = t_now;
        delay(5);
    }

    int reported = (int)round(fabs(yaw));
    Serial.print(F("DONE:"));
    Serial.println(reported);
    return yaw;
}

// ---------- Servo helpers ----------
void servo_sweep(Servo &s, uint8_t start_deg, uint8_t end_deg, uint16_t step_ms = 15) {
    if (start_deg < end_deg) {
        for (int a = start_deg; a <= end_deg; a += 2) {
            s.write(a); delay(step_ms);
        }
    } else {
        for (int a = start_deg; a >= end_deg; a -= 2) {
            s.write(a); delay(step_ms);
        }
    }
}

void servos_hello() {
    // Both arms up, down, back to neutral.
    servo_sweep(servo_right_arm, 90, 160);
    servo_sweep(servo_left_arm,  90,  20);
    delay(200);
    servo_sweep(servo_right_arm, 160, 90);
    servo_sweep(servo_left_arm,  20,  90);
}

// ---------- Extended command parser ----------
// We just received "L045" or "R180" (no LF). Execute closed-loop rotation.
void handle_extended_line(const char *buf, uint8_t len) {
    if (len < 2) {
        Serial.println(F("ERR:short"));
        return;
    }
    char dir = buf[0];
    int direction = 0;
    if      (dir == 'L' || dir == 'l') direction = -1;
    else if (dir == 'R' || dir == 'r') direction = +1;
    else { Serial.println(F("ERR:dir")); return; }

    // Parse remaining digits as the target angle.
    int angle = 0;
    for (uint8_t i = 1; i < len; i++) {
        if (buf[i] < '0' || buf[i] > '9') { Serial.println(F("ERR:digits")); return; }
        angle = angle * 10 + (buf[i] - '0');
        if (angle > 360) { Serial.println(F("ERR:range")); return; }
    }
    rotate_closed_loop(direction, (float)angle);
}

// ---------- Single-byte command dispatcher ----------
void handle_byte(char c) {
    switch (c) {
    case 'F': motors_forward(motor_speed); break;
    case 'B': motors_backward(motor_speed); break;
    case 'S': motors_stop(); break;
    case 'H': servos_hello(); break;
    case 'T': servo_sweep(servo_head, 90, 60); servo_sweep(servo_head, 60, 90); break;
    case 'G': servo_sweep(servo_left_arm, 90, 20); servo_sweep(servo_left_arm, 20, 90); break;
    case 'D': servo_sweep(servo_right_arm, 90, 160); servo_sweep(servo_right_arm, 160, 90); break;
    case 'A': servos_hello(); servo_sweep(servo_head, 90, 60); servo_sweep(servo_head, 60, 90); break;
    case '+': if (motor_speed < 245) motor_speed += 10; break;
    case '-': if (motor_speed >  60) motor_speed -= 10; break;
    case '?':
        Serial.print(F("STATUS:speed=")); Serial.print(motor_speed);
        Serial.print(F(",gyro=")); Serial.println(mpu_ok ? F("ok") : F("nok"));
        break;
    default: /* ignore */ break;
    }
}

// ---------- Setup / Loop ----------
void setup() {
    Serial.begin(9600);
    while (!Serial) { /* wait */ }

    servo_right_arm.attach(SERVO_RIGHT_ARM_PIN);
    servo_left_arm.attach(SERVO_LEFT_ARM_PIN);
    servo_head.attach(SERVO_HEAD_PIN);
    servo_right_arm.write(90);
    servo_left_arm.write(90);
    servo_head.write(90);

    motors_stop();

    // MPU6050 wake-up + calibration. Keep the robot STILL for 1s at boot
    // (this happens once, right after power-on; the Pi waits a bit anyway).
    mpu_ok = mpu_setup();
    if (mpu_ok) {
        mpu_calibrate_bias();
        Serial.print(F("READY:gyro_bias="));
        Serial.println(gyro_z_bias, 2);
    } else {
        Serial.println(F("READY:gyro=nok"));
    }
}

void loop() {
    while (Serial.available()) {
        char c = (char)Serial.read();

        // Line terminator => execute buffered extended command.
        if (c == '\n' || c == '\r') {
            if (line_len > 0) {
                line_buf[line_len] = '\0';
                handle_extended_line(line_buf, line_len);
                line_len = 0;
            }
            continue;
        }

        // If the FIRST char in the buffer is L/R/l/r, we're collecting an
        // extended command — keep buffering until LF.
        if (line_len > 0 || c == 'L' || c == 'l' || c == 'R' || c == 'r') {
            if (line_len < sizeof(line_buf) - 1) {
                line_buf[line_len++] = c;
            } else {
                // Buffer overflow — reset and signal error.
                line_len = 0;
                Serial.println(F("ERR:overflow"));
            }
            continue;
        }

        // Otherwise it's a legacy single-byte command, execute now.
        handle_byte(c);
    }
}
