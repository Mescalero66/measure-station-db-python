# The LED Display and Button

This guide connects an 8-digit LED display and a push button to the server Pi. The
display shows the latest reading of one value, e.g. `co2  612`. Each press of the button
moves on to the next value: CO2, temperature, humidity, pressure, fine dust and light, then
back to CO2. It takes about an hour.

| Part | Model |
|---|---|
| Display | DFRobot **DFR0646** (Gravity: 8-Digital LED Segment Display Module, red or green). Its chip is a Holtek HT16K33A |
| Button | DFRobot **DFR0029-G** (Gravity: Digital Push Button, green) |

**This hasn't been tried on real hardware yet.** The software was checked against your
MicroPython driver, but not on a Pi with the parts connected. If a step doesn't do what
it says, stop, and send the last 20 or so lines from the terminal to the server's
developer.

Contents:

- [Words Used in This Guide](#words-used-in-this-guide)
- [What You Need](#what-you-need)
- [Step 1: Update the Pi's Software](#step-1-update-the-pis-software)
- [Step 2: Switch On the Pi's I2C](#step-2-switch-on-the-pis-i2c)
- [Step 3: Check the Display Is Safe for the Pi](#step-3-check-the-display-is-safe-for-the-pi)
- [Step 4: Connect the Display and Button](#step-4-connect-the-display-and-button)
- [Step 5: Check the Wiring](#step-5-check-the-wiring)
- [Step 6: Choose What It Shows](#step-6-choose-what-it-shows)
- [Step 7: Switch It On](#step-7-switch-it-on)
- [Using It](#using-it)
- [Everyday Tasks](#everyday-tasks)
- [Settings](#settings)
- [Troubleshooting](#troubleshooting)

## Words Used in This Guide

| Word | Meaning |
|---|---|
| **GPIO header** | The double row of 40 metal pins on the Pi's board, for connecting electronics |
| **Pin number** | A pin's position on the header, 1 to 40. This guide always says "pin" for these |
| **GPIO number** | The Pi's own name for a pin it can read or switch, e.g. GPIO 17 (which is pin 11). The settings use GPIO numbers |
| **I2C** | A way for chips to talk over two wires: SDA (data) and SCL (clock). The display uses it |
| **3.3 V and 5 V** | The two supply voltages on the header. The Pi's GPIO pins work at 3.3 V and **can be damaged by 5 V** |
| **Gravity** | DFRobot's range of plug-in modules. Each comes with a cable; the plug's wires are labelled on the module |

## What You Need

| Item | Notes |
|---|---|
| The display and button | See the table above |
| Their cables, with **female** jumper sockets at the Pi end | The Pi's pins are male, so the wire ends that go on them must be sockets. If a module's cable has a plug at both ends, use a Gravity-to-jumper ("Dupont") cable instead |
| A multimeter | For Step 3. Any cheap one that measures DC volts |
| Possibly an I2C level shifter | Only if Step 3 says so. A small "bidirectional I2C logic level converter" board with a 3.3 V side and a 5 V side (e.g. one based on BSS138 transistors) |

**The Pi's case:** in the SC0400 kit's case, take the lid off to reach the GPIO header.

**Finding the pins:** with the Pi's USB and network sockets pointing towards you, the
header runs along the right-hand edge, and pins 1 and 2 are at its far end (away from
you). Pin 1 is in the inner row (nearer the middle of the board), pin 2 in the outer row
(nearer the edge). Odd numbers (1, 3, 5, …) continue along the inner row, even numbers
(2, 4, 6, …) along the outer row. Check with the picture at
[pinout.xyz](https://pinout.xyz) before connecting anything.

## Step 1: Update the Pi's Software

Connect to the Pi with `ssh`, then:

```bash
cd /opt/measure-station-db
sudo git pull
sudo bash deploy/install.sh
```

This installs the display's program, and lets the server's programs use the display and
button. The last lines should include:

```text
The LED display is off until switched on: see docs/led-display.md.
```

## Step 2: Switch On the Pi's I2C

The Pi's I2C is off until it's switched on. Run these:

```bash
sudo raspi-config nonint do_i2c 0
sudo apt install -y i2c-tools
sudo reboot
```

The first switches I2C on (`0` means "on" here). The second installs a tool that lists
the chips on the I2C wires. The third restarts the Pi; reconnect with `ssh` after a
minute. Then check:

```bash
ls /dev/i2c-1
```

It should print `/dev/i2c-1`. (`No such file or directory` means I2C isn't on yet: run
the first command again, and restart again.)

## Step 3: Check the Display Is Safe for the Pi

**Why:** the display runs on 5 V, but the Pi's SDA and SCL pins must never see more than
3.3 V. Some display boards hold their SDA and SCL wires up at 5 V when idle (with
*pull-up resistors*); connecting one of those straight to the Pi can damage it. The
display's documentation doesn't say which kind the DFR0646 is, so this step measures it.

1. **Shut down the Pi** and unplug its power:

   ```bash
   sudo poweroff
   ```

   Wait until its green light stops flickering, then unplug the power.

2. **Connect only the display's power:** its **VCC** (or **+**) to **pin 2** (5 V), and its
   **GND** (or **−**) to **pin 6**. Go by the labels on the module, not the wire colours.
   Leave its SDA and SCL wires unconnected, and not touching anything.
3. Plug the Pi's power back in. The display may stay blank: that's fine.
4. Set the multimeter to DC volts. Put its black probe on the display's **GND**, and its
   red probe on the display's **SDA** pin (or the end of its SDA wire). Note the reading.
   Do the same for **SCL**.
5. Unplug the Pi's power again.

| Reading on SDA and SCL | What it means | What to do |
|---|---|---|
| Below 3.6 V (e.g. 0 V, or about 3.3 V) | No 5 V pull-ups: safe for the Pi | Connect the display directly (Step 4) |
| About 5 V | The board holds them at 5 V | **Don't connect SDA and SCL straight to the Pi.** Use a level shifter (Step 4 shows how) |

Without a multimeter, use a level shifter to be safe.

## Step 4: Connect the Display and Button

With the Pi's power **unplugged**, connect:

**The display (DFR0646):**

| Display | Pi, connected directly | Pi, with a level shifter |
|---|---|---|
| VCC (+) | pin 2 (5 V) | pin 2 (5 V) |
| GND (−) | pin 6 (GND) | pin 6 (GND) |
| SDA | pin 3 (GPIO 2, SDA) | the shifter's 5 V-side SDA; its 3.3 V-side SDA to pin 3 |
| SCL | pin 5 (GPIO 3, SCL) | the shifter's 5 V-side SCL; its 3.3 V-side SCL to pin 5 |

A level shifter also needs power on both sides: its **LV** (low voltage) pin to **pin 1**
(3.3 V), its **HV** (high voltage) pin to **pin 4** (5 V), and its **GND** to **pin 14**
(GND).

**The button (DFR0029-G):**

| Button | Pi |
|---|---|
| VCC (+) | **pin 1 (3.3 V)**. Not 5 V: the button's signal wire carries its supply voltage, and 5 V would damage the Pi's input |
| GND (−) | pin 9 (GND) |
| Signal (D) | pin 11 (GPIO 17) |

(If a level shifter already uses pin 1, put the button's VCC on **pin 17**, the other
3.3 V pin.)

Check every wire against the tables once more, then plug the Pi's power back in.

## Step 5: Check the Wiring

1. **Is the display found?** Reconnect with `ssh`, then:

   ```bash
   i2cdetect -y 1
   ```

   It prints a grid. **`70`** should appear in the `70:` row, like this:

   ```text
        0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
   ...
   70: 70 -- -- -- -- -- -- --
   ```

   If the grid shows only `--`, the Pi can't see the display: see
   [Troubleshooting](#troubleshooting).

2. **Test the display and button:**

   ```bash
   /opt/measure-station-db/.venv/bin/python -m measure_station_db.display test
   ```

   - Every segment and dot on the display lights up (`8.8.8.8.8.8.8.8.`), and it prints
     `Memory read-back: PASS`.
   - Then the display shows `PrESS  0`, and you have 15 seconds to press the button a few
     times. Each press should add one: `PrESS  1`, `PrESS  2`, … and print
     `Button pressed (1)` and so on.
   - At the end it prints how many presses it counted, and the display goes blank.

   If the display doesn't light, or presses aren't counted, see
   [Troubleshooting](#troubleshooting).

## Step 6: Choose What It Shows

The values are listed in the server's settings file. Open it:

```bash
sudo nano /opt/measure-station-db/src/measure_station_db/sysconfig.py
```

Press **Ctrl+W**, type `DISPLAY_STATION` and press Enter. You'll see:

```python
DISPLAY_STATION: str = "mstn-2b"
```

Change `mstn-2b` to your station's ID, keeping the quotes. Just below is the list of
values, `DISPLAY_ITEMS`, in button order:

```python
DISPLAY_ITEMS: list[dict[str, Any]] = [
    {"label": "co2", "source": "co2", "key": "co2_ppm", "decimals": 0},
    {"label": "t", "source": "temperature", "key": "temperature_c", "decimals": 1},
    {"label": "rh", "source": "humidity", "key": "humidity_pct", "decimals": 0},
    {"label": "P", "source": "barometer", "key": "pressure_hpa", "decimals": 1},
    {"label": "Pm2.5", "source": "particulates", "key": "pm25_ugm3", "decimals": 1},
    {"label": "LUX", "source": "light", "key": "illuminance_lux", "decimals": 0},
]
```

Each line is one value:

| Part | Meaning |
|---|---|
| `label` | A short name shown on the display, up to 8 characters. 7 segments can't draw every letter well: `t`, `rh`, `co2`, `P` and `Pm2.5` read clearly; `k`, `m`, `v`, `w` and `x` don't. Only letters, digits, spaces and `- . = _ °` can be shown |
| `source` and `key` | Which value, by its names in the database. Grafana's Explore page lists every one ([Viewing the Data in Grafana](viewing-data-in-grafana.md#step-2-see-whats-arriving), Step 2) |
| `decimals` | How many digits after the decimal point |

To leave a value out, delete its line. To add one, copy a line and change it. Keep the
`{ }`, the quotes, and the comma at the end of each line. Save and exit when done.

## Step 7: Switch It On

1. In the same settings file, press **Ctrl+W**, type `DISPLAY_ENABLED` and press Enter.
   Change `False` to `True`:

   ```python
   DISPLAY_ENABLED: bool = True
   ```

   Save and exit.

2. Start the display's service:

   ```bash
   sudo systemctl restart measure-station-db-display
   ```

3. Check it's running:

   ```bash
   sudo journalctl -u measure-station-db-display -n 10
   ```

   You should see:

   ```text
   INFO [measure_station_db.display.service] Showing 6 value(s) from mstn-2b on the display at I2C bus 1, address 0x70
   INFO [measure_station_db.display.service] Watching the button on GPIO 17
   ```

The display now shows `co2`, then `co2  612` (with your reading). It starts by itself
whenever the Pi starts.

## Using It

- **Press the button** to move to the next value. Its label shows on its own for 1.5
  seconds (e.g. `rh`), then the reading with the label in front (e.g. `rh    61`). After
  the last value, it goes back to the first.
- **The reading updates** every 10 seconds.
- **When a label and its value don't both fit** in 8 digits, only the value is shown.
- **`----`** means there's no reading from the last 5 minutes: the sensor or station has
  stopped sending.
- **`Err`** means the database can't be read. Check the
  [ingest service](ingest-service.md#troubleshooting) is running.
- **Letters are approximations:** a 7-segment display draws `5` and `S` the same, and
  `2` and `Z`.

## Everyday Tasks

| Task | Command |
|---|---|
| Is it running? | `systemctl status measure-station-db-display` (press **q** to quit) |
| Its log | `sudo journalctl -u measure-station-db-display -n 50` |
| Restart it (after changing a setting) | `sudo systemctl restart measure-station-db-display` |
| Test the wiring again | `sudo systemctl stop measure-station-db-display`, then Step 5's test, then `sudo systemctl start measure-station-db-display` |
| Switch it off | Set `DISPLAY_ENABLED` to `False` (Step 7), then restart it |

## Settings

In `/opt/measure-station-db/src/measure_station_db/sysconfig.py`. Restart the service
after a change.

| Setting | Default | Meaning |
|---|---|---|
| `DISPLAY_ENABLED` | `False` | `True` to run the display |
| `DISPLAY_STATION` | `"mstn-2b"` | The station whose values are shown |
| `DISPLAY_ITEMS` | CO2, temperature, humidity, pressure, PM2.5, light | The values, in button order ([Step 6](#step-6-choose-what-it-shows)). An item can also have `"station": "…"` (another station) or `"section": "meta"` (a value from `meta`) |
| `DISPLAY_BRIGHTNESS` | `8` | 0 (dim, but not off) to 15 (brightest) |
| `DISPLAY_ROTATED` | `False` | `True` if the display is mounted upside down |
| `DISPLAY_LABEL_S` | `1.5` | Seconds the label shows on its own after a press |
| `DISPLAY_REFRESH_S` | `10` | Seconds between readings of the database |
| `DISPLAY_STALE_S` | `300` | A reading older than this many seconds shows as `----` |
| `DISPLAY_BUTTON_GPIO` | `17` | The button's GPIO number (pin 11) |
| `DISPLAY_BUTTON_PRESSED_HIGH` | `True` | `False` for a button whose signal goes low when pressed |
| `DISPLAY_I2C_ADDRESS` | `0x70` | The display's address. Only change it if `i2cdetect` shows another (`71` to `73`) |

## Troubleshooting

| Problem or message | What to do |
|---|---|
| `i2cdetect` shows only `--` | The Pi can't see the display. Unplug the power and check: VCC on pin 2, GND on pin 6, SDA on pin 3, SCL on pin 5 (or through the level shifter, with its LV, HV and GND connected). SDA and SCL swapped is the most common mistake |
| `i2cdetect` shows a number other than `70` | Set `DISPLAY_I2C_ADDRESS` to it, e.g. `0x71` |
| `Error: Could not open file /dev/i2c-1` | I2C isn't on: Step 2 |
| The test prints `The display isn't answering` | As for `i2cdetect` showing only `--` |
| The test prints `Memory read-back: FAIL`, or segments flicker or show the wrong pattern | The I2C signals are unreliable. Use a level shifter (Steps 3 and 4), and keep the wires short |
| The display works, but the test counts no presses | Check the button: VCC on pin 1, GND on pin 9, signal on pin 11. The button's own LED should light while it's pressed |
| Each press counts twice, or presses are missed | Send the developer the test's output |
| `Can't read the button on GPIO 17 (…)` in the log | Another program is using GPIO 17, or the setting is wrong. The display keeps working, but stays on the first value |
| `Configuration error: …` in the log | A mistake in the display settings; the message says which. Fix it, then restart the service |
| `The display isn't answering (…); retrying` in the log | A loose wire, or the display lost power. The service keeps trying, and carries on by itself once the display answers |
| The display shows `----` for one value | That sensor hasn't sent a reading for 5 minutes, or its `source` or `key` is misspelt. Compare with Grafana's list ([Step 6](#step-6-choose-what-it-shows)) |
