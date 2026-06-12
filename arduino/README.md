# KODA Arduino — MPU6050 closed-loop rotation

This folder ships `koda_arduino.ino`, the firmware that runs on the
Arduino Uno + Adafruit Motor Shield v1 + MPU6050 gyroscope.

## Hardware wiring

```
Arduino Uno ─┬─ Motor Shield (L293D, stacked)
             │      M1 = right DC motor
             │      M3 = left  DC motor
             │      Servo 1 (pin 9)  = right arm
             │      Servo 2 (pin 10) = left arm
             │      Servo 3 (pin 11) = head / Nextion bracket
             │
             ├─ USB-B (to Raspberry Pi)         ── serial @ 9600 bauds
             │
             └─ MPU6050 (GY-521 breakout)
                    VCC ─ 5V        (the breakout has a 3.3V regulator on board)
                    GND ─ GND
                    SCL ─ A5        (Arduino Uno I2C clock)
                    SDA ─ A4        (Arduino Uno I2C data)
                    INT, XDA, XCL, AD0 ─ left floating
```

⚠️ Keep the robot **completely still during the first ~1.5 s after boot**
— that window is when the firmware measures the gyro bias. Move it and
every later rotation will drift.

## Required libraries (Arduino IDE)

Install via Library Manager:

- `Adafruit Motor Shield Library` (the **v1** one — for L293D shields)
- `Servo` (built-in)
- `Wire` (built-in)

No MPU6050 library is required — the sketch talks to the chip directly
via the `Wire` (I2C) primitives to keep dependencies minimal.

## Flashing

1. Open `koda_arduino.ino` in the Arduino IDE
2. Tools → Board → "Arduino Uno"
3. Tools → Port → `/dev/ttyUSB0` (or whatever the Pi enumerates as)
4. Sketch → Upload
5. Open Serial Monitor at **9600 baud** to confirm — you should see one of:

   ```
   READY:gyro_bias=12.45
   ```
   (gyro detected and calibrated — typical bias values are between -50 and +50)

   ```
   READY:gyro=nok
   ```
   (no I2C ack from the MPU6050 — check SDA/SCL/VCC/GND wiring)

## Serial protocol

### Legacy single-byte commands (immediate, no response)

| Byte | Action |
|---|---|
| `F` | Forward (continuous, send `S` to stop) |
| `B` | Backward (continuous) |
| `S` | Stop motors |
| `H` | Hello — both arms wave |
| `T` | Head servo bump |
| `G` | Left arm servo |
| `D` | Right arm servo |
| `A` | All servos |
| `+` | Speed up (motor PWM +10) |
| `-` | Speed down (motor PWM -10) |
| `?` | Status — replies `STATUS:speed=N,gyro=ok\|nok\n` |

### Extended rotation commands (blocking, with response)

| Format | Example | Response on success | Response on failure |
|---|---|---|---|
| `L<aaa>\n` | `L045\n` | `DONE:46\n` | `ERR:timeout\n`, `ERR:nogyro\n`, … |
| `R<aaa>\n` | `R180\n` | `DONE:182\n` | same |

The angle is always **3 ASCII digits** (000 to 360). The firmware:
1. Spins the differential motors in the requested direction at the
   configured `motor_speed`.
2. Reads the gyro Z-rate at 200 Hz and integrates to track the actual angle.
3. Switches to "brake speed" (PWM 110) when within 6° of the target so the
   chassis inertia doesn't overshoot.
4. Stops the motors when the integrated angle reaches the target.
5. Waits 150 ms more to let the chassis settle (keeps integrating so the
   reported angle includes the coast).
6. Replies `DONE:<actual>` where `<actual>` is the absolute degrees actually
   travelled. Drift is typically ±2°.

### Safety

- A 6 s firmware-side timeout aborts the rotation if the gyro returns
  garbage or the chassis is stuck. The Pi receives `ERR:timeout`.
- If the MPU6050 wasn't detected at boot, every extended rotation command
  immediately answers `ERR:nogyro` — no motors are powered, no risk.

## Testing without the Pi

Open the Serial Monitor (9600 baud, line ending = `Newline`) and type:

```
?                ← status
R045             ← right 45°  (then wait for DONE)
L090             ← left 90°
H                ← hello (servos wave)
```

You can chain extended and legacy commands. Single bytes execute immediately;
multi-char commands buffer until a newline.

## Recommended speed tuning

Default `motor_speed = 200` (out of 255) gives ~60°/s on a freshly-charged
3-pile pack. If your robot rotates too fast / overshoots a lot:

- Send `-` two or three times to drop PWM to 180 → ~45°/s, easier to brake.
- Or edit `motor_speed` constant in the sketch.

If too slow:

- Send `+` to bump PWM to 220 → ~75°/s, but expect more overshoot.

The brake-speed constant (`ROTATION_BRAKE_SPEED = 110`) is the sweet spot
for the AAA-pile build. With a fresh LiPo, drop it to ~90.
