# Reading Modbus Devices

The server can read values from **Modbus TCP** devices on the network: power meters,
environment sensors, controllers and the like. This guide switches that on, describes a
device, and checks that its readings arrive.

It's **off by default**. Nothing needs doing until there's a device to read.

Contents:

- [How It Works](#how-it-works)
- [What You Need to Know About the Device](#what-you-need-to-know-about-the-device)
- [Step 1: Give the Poller a Broker Login](#step-1-give-the-poller-a-broker-login)
- [Step 2: Add Its Password](#step-2-add-its-password)
- [Step 3: Describe the Device](#step-3-describe-the-device)
- [Step 4: Start It and Check It](#step-4-start-it-and-check-it)
- [Settings](#settings)
- [Switching It Off](#switching-it-off)
- [Troubleshooting](#troubleshooting)

## How It Works

A station sends its readings by itself. A Modbus device never does: it only answers
when asked, e.g. "what's in holding register 10?". So a program on the server, the
**Modbus poller**, asks each device on a timer, and turns the answers into readings.

The poller publishes each device's readings to the broker in exactly the station's
message format, under a station ID you choose. To the rest of the server, **each device
looks like one more station** (a "virtual station"): the ingest service stores its
readings, they get hourly and daily averages, and the message checker shows them. No
other setting changes.

- **Every device is read on its own timer,** so a slow or missing device can't hold up
  the others.
- **A failed read sends nothing,** like a station's failed sensor. The device's `status`
  changes to `offline`, and back to `online` when it answers again.
- **Each reading is timestamped by the server's clock.** After a power cut, the poller
  waits until NTP has set the clock before reading, so no reading gets a wrong time.
- **If the broker is briefly unavailable,** readings wait in the poller's memory and are
  sent when it's back.

It runs as its own service, `measure-station-db-modbus`, installed by the server's
installer. While Modbus is switched off, it starts, logs that it's switched off, and
stops.

## What You Need to Know About the Device

From the device's manual (its "Modbus register map"):

| What | Example | Notes |
|---|---|---|
| Its IP address | `192.168.1.23` | Give it a fixed one, e.g. a DHCP reservation in the router, as for the server Pi |
| Its Modbus TCP port | `502` | Almost always 502 |
| Its unit ID | `1` | Also called the slave ID or device address. Often 1. A gateway that connects several devices gives each its own |
| Each value's register number | `10` | See [Register Numbers](#register-numbers) below |
| Each value's register type | holding | Holding registers (the most common), input registers, coils or discrete inputs |
| Each value's format | `int16` | How the number is stored: see [Settings](#settings) |
| Each value's scale | `0.1` | When the register holds 214 for 21.4 °C, the scale is 0.1 |

**Try the device with your PC tool first.** The Modbus tab of `device-data-receiver`
uses the same register numbers, formats and scales as the server, so values that look
right there can be copied straight into the server's settings.

### Register Numbers

Manuals number registers in two ways, and mixing them up is the most common Modbus
mistake. The server uses the number **as sent to the device**, counting from 0.

| The manual says | Meaning | Register in the server's settings |
|---|---|---|
| `40011` or `4x0011` | Holding register, the 11th | `10`, type `holding` |
| `30006` | Input register, the 6th | `5`, type `input` |
| `00004` or `1` + coil | Coil, the 4th | `3`, type `coil` |
| `10003` | Discrete input, the 3rd | `2`, type `discrete` |
| "address 10" or `0x000A` | Already counted from 0 | `10` |

If every value comes back one register out (for example, the temperature shows the
humidity), the manual was counting from 1: subtract 1 from each register.

## Step 1: Give the Poller a Broker Login

Do this once, the first time you add a device. Connect to the server Pi with `ssh`.

1. Create the poller's broker login. Choose a password using only letters, digits, `-`
   and `_` (no `-c` here: the file already exists):

   ```bash
   sudo mosquitto_passwd /etc/mosquitto/passwd measure-station-db-modbus
   ```

2. Let it publish. Open the broker's access list:

   ```bash
   sudo nano /etc/mosquitto/acl
   ```

   and add these lines at the end:

   ```text
   # The Modbus poller publishes for the devices it reads (docs/modbus-devices.md).
   user measure-station-db-modbus
   topic write measure-station/#
   ```

   Save with **Ctrl+O** and **Enter**, then exit with **Ctrl+X**.

3. Set the files' owner and permissions again, as after every change to them, and
   restart the broker:

   ```bash
   sudo chown mosquitto:mosquitto /etc/mosquitto/passwd /etc/mosquitto/acl
   sudo chmod 600 /etc/mosquitto/passwd /etc/mosquitto/acl
   sudo systemctl restart mosquitto
   ```

Unlike a station's login, this one may publish under any station ID, because it
publishes for every device it reads. It's only used by the poller, on the server Pi
itself.

## Step 2: Add Its Password

```bash
sudo nano /etc/measure-station-db/env
```

Find the line `#MEASURE_STATION_DB_MODBUS_MQTT_PASSWORD=`, delete the `#` at its start,
and put the password from Step 1 after the `=`:

```text
MEASURE_STATION_DB_MODBUS_MQTT_PASSWORD=the-poller-password
```

Save and exit as before.

## Step 3: Describe the Device

The devices are described in the server's settings file:

```bash
sudo nano /opt/measure-station-db/src/measure_station_db/sysconfig.py
```

Use **Ctrl+W** to find `MODBUS_ENABLED`, and change `False` to `True`:

```python
MODBUS_ENABLED: bool = True
```

Just below it is `MODBUS_DEVICES`, with an example device. Change the example to match
your device, and set its `"enabled"` to `True`. For example, a sensor at `192.168.1.23`
whose manual lists temperature in holding register 40011 (tenths of a degree, signed)
and humidity in 40012 (tenths of a percent):

```python
MODBUS_DEVICES: list[dict[str, Any]] = [
    {
        "enabled": True,
        "station": "modbus-01",
        "source": "environment",
        "host": "192.168.1.23",
        "port": 502,
        "unit": 1,
        "interval_s": 60,
        "values": [
            {"key": "temperature_c", "register": 10, "format": "int16", "scale": 0.1},
            {"key": "humidity_pct", "register": 11, "format": "uint16", "scale": 0.1},
        ],
    },
]
```

- **`station` and `source`** are how the device appears in the database and the charts.
  Choose them once and keep them: data is stored under them. A device must not use the
  same station ID as a real station.
- **Each `key`** names a value, in the form `<quantity>_<unit>` like the stations' keys:
  `temperature_c`, `humidity_pct`, `pressure_hpa`, `power_w`, `energy_wh`, `voltage_v`,
  `current_a`. Use the same key for the same quantity as the stations do, so they chart
  together.
- **More devices:** copy the whole `{ ... },` block, including its closing `},`, and
  change it. Each device needs a different station or source.

Keep the quotes, commas and brackets exactly as shown: a missing comma stops the poller
starting (its log says where). Save and exit.

## Step 4: Start It and Check It

```bash
sudo systemctl restart measure-station-db-modbus
sudo journalctl -u measure-station-db-modbus -n 20
```

You should see:

```text
INFO [measure_station_db.modbus] Reading modbus-01/environment (192.168.1.23:502 unit 1) every 60 s: 2 value(s) in 1 request(s)
INFO [measure_station_db.modbus] modbus-01/environment (192.168.1.23:502 unit 1) is answering
INFO [measure_station_db.modbus] Connected to the broker at localhost:1883
```

Then watch its readings arrive with the message checker
([Checking Messages From a Station](checking-station-messages.md)). The device appears
as a station with one source:

```text
16:37:58  modbus-01  environment      1 s old  ntp        173 B  temperature_c=21.4 humidity_pct=48.3
```

Check the values against the device's own display, or your PC tool. In the checker's
summary, the device's station also lists the real stations' sources under **Not
received**: that's expected, since the device doesn't have them.

The ingest service stores the readings with no further setup.

## Settings

All in `sysconfig.py`.

| Setting | Meaning |
|---|---|
| `MODBUS_ENABLED` | `True` to read devices. `False` (the default) switches the poller off |
| `MODBUS_QUEUE_MAX` | Readings kept in memory while the broker can't be reached (10,000). Beyond that, new readings are dropped, and logged |

Each device in `MODBUS_DEVICES`:

| Option | Required | Meaning |
|---|---|---|
| `enabled` | yes | `True` to read it. `False` keeps its settings without reading it |
| `station`, `source` | yes | Its name in the database: letters, digits, `.`, `_` and `-`. The source can't be `status` |
| `host` | yes | Its IP address (or a host name) |
| `port` | no | Its Modbus TCP port. Default `502` |
| `unit` | no | Its unit ID, 0 to 255. Default `1` |
| `interval_s` | yes | How often to read it, in seconds |
| `timeout_s` | no | How long to wait for an answer, in seconds. Default `3` |
| `max_gap` | no | Values up to this many registers apart are read in one request. Default `0`: only neighbouring registers. Raising it means fewer requests, but only works if the device allows reading the registers in between (otherwise it answers "illegal data address") |
| `sensor_type` | no | What to record as the hardware. Default `"modbus"` |
| `values` | yes | The values to read, as below |

Each value in `values`:

| Option | Required | Meaning |
|---|---|---|
| `key` | yes | Its name: lowercase letters, digits and `_`, in the form `<quantity>_<unit>` |
| `register` | yes | Its register number, counted from 0 ([Register Numbers](#register-numbers)) |
| `type` | no | `"holding"` (the default), `"input"`, `"coil"` or `"discrete"`. Coils and discrete inputs are on/off, and stored as true/false |
| `format` | registers only | `"uint16"` (0 to 65535), `"int16"` (-32768 to 32767), `"uint32"`, `"int32"` or `"float32"`. The 32-bit formats take two registers, high word first |
| `scale` | no | Multiplied in, e.g. `0.1` when 214 means 21.4. Default `1`. The result keeps as many decimals as the scale has |
| `section` | no | `"values"` (the default) for a measurement, or `"meta"` for a status flag or setting |

## Switching It Off

- **One device:** set its `"enabled"` to `False`.
- **All of them:** set `MODBUS_ENABLED` to `False`.

Then `sudo systemctl restart measure-station-db-modbus`. The data already stored is kept.
With nothing to read, `systemctl status measure-station-db-modbus` shows
`inactive (dead)`, and its log says why. That's normal.

## Troubleshooting

| Log message or problem | What to do |
|---|---|
| `Modbus polling is switched off` | `MODBUS_ENABLED` is `False` ([Step 3](#step-3-describe-the-device)) |
| `No Modbus device is enabled` | No device has `"enabled": True` |
| `Configuration error: MODBUS_DEVICES entry …` | A setting is wrong; the message names the device, the value and the option. Fix it, then restart the service |
| A `SyntaxError` in the log | A comma, quote or bracket is missing in `sysconfig.py`. The log shows the line |
| `… isn't answering: timed out` | The device didn't reply. Check its IP address, that it's on, and that the server can reach it: `ping -c 3 192.168.1.23` |
| `… isn't answering: … refused` | The device is there but not listening on that port. Check the port, and that Modbus TCP is switched on in the device |
| `… exception 2 (illegal data address)` | A register doesn't exist on the device. Check the register numbers ([Register Numbers](#register-numbers)). If you raised `max_gap`, set it back to `0` |
| `… exception 1 (illegal function)` | The register type is wrong, e.g. `holding` for an input register |
| `… exception 11 (gateway target device failed to respond)` | A gateway answered, but the device behind it didn't: check the `unit` and the device's wiring to the gateway |
| Values are wildly wrong | Check the `format` (signed or unsigned, 16 or 32 bits) and the `scale`. A 32-bit value that's wrong may be stored low word first, which the server doesn't support yet: tell the developer |
| Values are one register out | The manual counts from 1: subtract 1 from each register |
| `waiting for NTP to set the clock` | The server's clock isn't set yet (see the setup guide's [Step 5](setting-up-the-server-pi.md#step-5-set-the-time-source)). Reading starts as soon as it is |
| `The broker refused the connection` | The password in the env file doesn't match the one set in [Step 1](#step-1-give-the-poller-a-broker-login) |
| `sudo git pull` says `Your local changes would be overwritten` | Your edits to `sysconfig.py` clash with an update. Run `sudo git stash`, then `sudo git pull`, then `sudo git stash pop`. If that reports a conflict, ask the developer |
