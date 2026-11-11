# Measure Station Server

The server for [Measure Station](https://github.com/Mescalero66/measure-station-python):
a Raspberry Pi that receives readings from one or more stations over MQTT, stores them in
a database on a USB SSD, and presents them on a TV and on phones on the local network.

**Status: building.** This README is the design plan. Written so far: the Pi setup
guide, the ingest service and the message checker. None of it has run on the real Pi
yet.

## Documentation

- [Setting Up the Server Pi](docs/setting-up-the-server-pi.md): from an empty SSD to a
  Pi running the broker, database, Grafana and the TV display, step by step.
- [Reading Modbus Devices](docs/modbus-devices.md): reading Modbus TCP devices on the
  network as "virtual stations" (off until switched on).
- [The Ingest Service](docs/ingest-service.md): installing the service that stores the
  readings, checking it, connecting the first station, and everyday tasks.
- [Checking Messages From a Station](docs/checking-station-messages.md): checking on the
  server Pi that a station's readings arrive, complete and in the right form.
- [Development](docs/development.md): setting up a development copy, running the tests
  and checks, and the database tests on the Pi.

## Goals

- **Reliable storage:** never lose or duplicate a reading the broker has acknowledged.
- **Online storage later:** keep the option of copying the data to a cloud database.
- **More stations later,** including stations with different sensors, with no database
  changes.
- **Presentation:** a TV (HDMI, no keyboard or mouse) controlled from a phone, and phones
  browsing the data on the LAN. Users pick one or more sensors, see graphs over time,
  and compare current values with weekly and monthly averages. Ease of use matters.
- **LED display (later):** an 8-digit 7-segment I2C display with a push button that
  cycles through values.

## The Station Interface Is Fixed

The station's
[Server Pi Interface](https://github.com/Mescalero66/measure-station-python/blob/main/docs/server-pi-interface.md)
is the contract: topics, payloads, delivery rules, timestamps, and what the broker and
consumer must do. This server is built to it as written. A change to the station is
proposed only for a show-stopping error.

## Architecture

```text
 Station(s) ──MQTT──► Mosquitto ──► ingest service ──► PostgreSQL + TimescaleDB (USB SSD)
                      (broker)      (Python)              │              │
                                                          ▼              ▼
                                                       Grafana      display service ──► LED display + button
                                                       (charts)     (later)
                                                          │
                                                          ▼ (shown inside)
 Phone ── remote page ──► remote app ──► TV page (Chromium, full screen) ──► TV (HDMI)
 Phone ── browse ───────────────────────► Grafana
```

Everything between the stations and the phones runs on the server Pi.

| Component | What it does | Ours or off the shelf |
|---|---|---|
| **Mosquitto** | The MQTT broker: receives every station's messages, acknowledges them, and holds them on disk until the ingest service has them | Off the shelf, configured by us |
| **Ingest service** | Takes messages from the broker, stores them in the database in batches, and acknowledges each one only after it's saved | Ours (Python) |
| **PostgreSQL + TimescaleDB** | The database. TimescaleDB is a PostgreSQL extension for time-stamped data: it keeps hourly and daily averages up to date by itself and compresses old data | Off the shelf, with our schema |
| **Grafana** | Draws the charts: sensor pickers, time ranges, several sensors on one graph, comparisons with averages | Off the shelf, with our dashboards kept as files in this repo |
| **Remote app** | A small web app with two pages: the **TV page** (full screen, shows a Grafana dashboard) and the **remote page** (on a phone: buttons that choose what the TV shows) | Ours (Python) |
| **Kiosk** | Chromium in full-screen mode on the TV, started at boot, with no desktop | Off the shelf, configured by us |
| **Modbus poller** (optional, off by default) | Reads Modbus TCP devices on a timer and publishes their values to the broker as station messages, so each device is a "virtual station" | Ours (Python), with the Modbus client copied from `device-data-receiver` |
| **Display service** (later) | Drives the LED display; the button cycles through values | Ours (Python) |

Each of our programs is its own `systemd` service, so a fault in one (e.g. the display)
can't stop data being stored.

### Why These Pieces

- **No Telegraf.** Many guides put Telegraf between the broker and the database. Our
  ingest service does that job instead, because the station's rules are specific:
  acknowledge only after the database commit, ignore duplicates, treat the `status` topic
  separately, and never let one bad message block the queue. A small Python service
  can be tested against the interface's own examples.
- **PostgreSQL + TimescaleDB, not InfluxDB.**
  - The interface's suggested schema is PostgreSQL: one row per message, with
    `(station, source, ts)` as the primary key and "insert, ignore if it exists".
  - Continuous aggregates (automatic hourly and daily averages) correct themselves when
    late data arrives, which the station's backlog guarantees.
  - `JSONB` columns store any station's `values` and `meta` with no schema change.
  - Every cloud provider offers hosted PostgreSQL.
  - The query language is SQL, which has stayed the same for decades. InfluxDB's has
    changed twice.
- **Grafana, plus our own remote layer.** Grafana does the hard parts (graphs, zoom,
  time ranges, averages) for free. Grafana addresses are adjustable
  (`/d/<dashboard>?var-source=co2&from=now-7d&to=now&kiosk`), so the remote only has to
  tell the TV page which address to show. If Grafana ever feels wrong on the TV, only
  this display layer is replaced; the database underneath stays.
- **Installed directly, not with Docker.** Packages come from `apt`, our programs are run
  by `systemd`, and an `install.sh` sets things up, the same pattern as the station.

## Hardware

| Item | Detail |
|---|---|
| Server Pi | Raspberry Pi 4 Model B, 2 GB (SC0400 desktop kit), with its official power supply |
| Storage | USB SSD in a blue USB 3 port. The Pi **boots from the SSD**, so the SD card isn't used at all |
| TV | HDMI, output set to 1080p (a 4K output would make the browser work four times as hard) |
| LED display | 8-digit 7-segment, I2C, plus a push button (details later) |

### Memory Budget (2 GB)

The Pi runs Raspberry Pi OS **Lite** (64-bit, no desktop). The TV's browser is started
directly in full-screen mode. These are estimates, to be measured on the Pi:

| Component | Approximate RAM |
|---|---|
| OS (Lite) | 150 MB |
| Mosquitto | 10 MB |
| Ingest service | 40 MB |
| PostgreSQL + TimescaleDB (tuned for a small machine) | 150 to 250 MB |
| Grafana | 150 to 250 MB |
| Remote app | 50 MB |
| Chromium on the TV | 400 to 700 MB |
| **Total** | **About 1.0 to 1.5 GB** |

Compressed swap in RAM (`zram`) is enabled as a safety net.

## Data Flow and Reliability

The station deletes a reading as soon as the broker acknowledges it. From then on, this
server is responsible for it.

**Broker (Mosquitto):**

- Persistence on, saved to the SSD every 60 s (`autosave_interval 60`), and no limit on
  queued messages, as the interface requires.
- `max_inflight_messages` raised from its default of 20 to 1000, because the broker sends
  the ingest service at most that many unacknowledged messages at once, which limits the
  batch size (see below).
- Logins: each station logs in with its station ID as its username, and may only publish
  (and retain) on `measure-station/<station>/#`. The ingest service (`measure-station-db`)
  may only subscribe. No anonymous access.
- Queued messages are held in memory. A months-long backlog arriving while the ingest
  service is stopped could need several hundred MB, so the ingest service should run
  whenever a station is connected.

**Ingest service:**

- Fixed client ID `measure-station-db` and a persistent session
  (`clean_session=False`), so the broker keeps messages for it while it's stopped.
- Subscribes to `measure-station/+/+` at QoS 1.
- Uses paho-mqtt 2.x with `manual_ack=True`. It collects messages until it has a batch
  (a few hundred) or 1 s has passed. It then saves the batch in one database
  transaction, and only then acknowledges every message in it, in the order they
  arrived (MQTT requires that order).
- Duplicates are ignored by the primary key (`ON CONFLICT DO NOTHING`).
- A message that doesn't fit the interface (invalid JSON, a payload whose `station` or
  `source` doesn't match its topic, a missing field): kept exactly as received in
  `rejected_message`, logged and acknowledged, so it can't block the queue and nothing
  is lost.
- Times are accepted in any ISO 8601 form with a time zone (e.g. `Z`, no milliseconds)
  and stored as UTC. The station always sends `…T09:40:00.000+00:00`; other devices may
  not.
- Unknown sources and keys are stored like any other.
- `status` messages (plain text `online` or `offline`) go to their own table.

**Start order matters.** The broker only keeps messages for a session it already
knows about. The ingest service must connect at least once **before** a station is
pointed at the broker; otherwise the station's backlog is acknowledged and then thrown
away.

## Database Design

| Table | Contents | Notes |
|---|---|---|
| `reading` | One row per message: `station`, `source`, `ts`, `sensor_type`, `ts_src`, `values_json`, `meta_json`, `boot_id`, `boot_ts`, `received_at` | The interface's suggested schema. Primary key `(station, source, ts)`. **The source of truth:** everything else can be rebuilt from it |
| `reading_value` | One row per value in `values` and `meta`: `station`, `source`, `section`, `key`, `ts`, `ts_src`, `num_value`, `text_value` | Written in the same transaction as `reading`. Easy to chart. Numbers and booleans (1 or 0) in `num_value`; text (and the one list, `mqboard_stale_sensors`, as JSON) in `text_value`. Primary key `(station, source, section, key, ts)` |
| `value_hourly`, `value_daily` | Average, minimum, maximum and count per numeric series per hour and per UTC day | TimescaleDB continuous aggregates over `reading_value`, leaving out `estimated` timestamps. They feed the "compared with weekly/monthly average" charts |
| `rejected_message` | Every message that didn't fit the interface: `received_at`, `topic`, the raw `payload` (bytes) and its `problems` | Nothing the broker delivered is lost. When a new kind of device appears, a translator for its format can be added and its earlier messages stored properly |
| `station_status` | Each `online`/`offline` change, with the time the server stored it | From the retained `status` topic. Only changes are stored, since the broker resends it at every reconnect |

- The schema is in `src/measure_station_db/schema.py`. The ingest service applies it at
  every start, so every statement is safe to repeat.
- `reading` and `reading_value` are TimescaleDB hypertables: tables split into 7-day
  chunks by time, which makes time-range queries fast and lets old chunks be compressed.
- **Data is kept forever.**
- **Compression starts after about 4 months.** That's longer than the station's longest
  backlog (about 3 months), so late data never lands in a compressed chunk.
- **Late data:** the aggregates refresh themselves for the last 3 days (hourly) and 7
  days (daily). When the ingest service stores readings more than 2 days old (after an
  outage), it has their whole days refreshed, at most every 5 minutes while they keep
  arriving.
- **Averages include every non-`estimated` reading,** whatever its quality flags (e.g.
  `air-quality` during `warmup`). Dashboards that must exclude flagged readings query
  `reading_value` instead.
- **Quality:** `ts_src` and the `meta` flags are stored with the data. Dashboards hide
  `estimated` timestamps and readings flagged as untrustworthy by default (e.g.
  `air-quality` while `validity` isn't `"normal"`).
- **Series catalogue:** a table in `sysconfig.py` gives each known series a label, a unit
  (from its key's suffix) and a role (primary, secondary or meta, from the interface's
  "Best Source for Each Quantity" table). A series that isn't in the catalogue, such as
  one from a new station's sensor, is still stored and shown under its raw name.
- **Volume:** about 16,400 messages and roughly 77,000 values a day per station: a few GB
  a year before compression.

## Presentation

- **Grafana** is open to anyone on the LAN as a viewer, so phones need no login. Editing
  dashboards needs the admin login. Dashboards and the database connection are
  provisioned from files in this repo.
- **The TV page** (`/tv` in the remote app) fills the screen with a Grafana dashboard in
  kiosk mode. It listens for changes from the remote app, so it switches as soon as a
  button is pressed on a phone.
- **The remote page** (`/remote`) is made for a phone, and can be added to the home
  screen like an app. Its buttons choose a preset screen (e.g. "Indoor air", "Weather",
  "CO2 compared with last month"), a time range, and the sensors to show. Presets are
  defined in `sysconfig.py`.
- **Browsing:** phones can also open Grafana directly to explore the data.
- **Possible later addition:** the TV's own remote, through HDMI-CEC (Anynet+, Bravia Sync
  and similar), sending the same commands as the phone.

## Alerts (Deferred)

**Come back to this once build phases 1 to 4 are done** (see
[Build Phases](#build-phases)). The plan so far: Grafana alert rules, taken from the
interface's checklist:

- no `health` reading from a station for several minutes;
- `status` = `offline`;
- `clock_synced` false;
- a rising `outbox_backlog`;
- any source silent for several of its intervals.

Active alerts show on a dashboard panel, so on the TV and phones. How to notify someone
away from home needs internet access, and is decided later.

## Time

The server Pi takes NTP from the **same LAN time source as the stations**, and doesn't
serve time itself (see the interface's
[Network and Time](https://github.com/Mescalero66/measure-station-python/blob/main/docs/server-pi-interface.md#network-and-time)).
The server Pi needs a fixed IP address (e.g. a DHCP reservation), since stations are
configured with the address.

## Backups

Deferred: there's no backup location yet. Until there is, the SSD is a single point of
failure. The plan is a nightly `pg_dump` (a full database export) to that location once
it exists.

## Online Storage (Future)

Not built now. Two routes stay open:

- copying the database to a hosted PostgreSQL (TimescaleDB's cloud service, or any other
  provider) with PostgreSQL's built-in replication, or a periodic sync job;
- a Mosquitto *bridge*: a few lines of broker configuration that forward every message to
  a cloud MQTT broker as well.

## Sensor.Community (Future)

**To do, for future thought:** sharing live readings with
[Sensor.Community](https://sensor.community), the citizen network that shows air quality
on a public map. Not built. The plan so far, from a first look on 2026-11-11:

**Their API.** One HTTPS `POST` per sensor type, about every 2½ minutes, to
`https://api.sensor.community/v1/push-sensor-data/`
([API notes](https://github.com/opendata-stuttgart/meta/wiki/EN-APIs)). Each request has:

- an `X-Sensor` header: the device ID (for a Raspberry Pi, `raspi-` plus the Pi's serial
  number);
- an `X-Pin` header: a number for the kind of sensor (1 = particulates, 3 = BMP280,
  7 = temperature and humidity, 17 = SCD30);
- a JSON body: `{"software_version": "...", "sensordatavalues": [{"value_type": "P2",
  "value": "4.1"}, ...]}`.

**Their server stamps each reading with its time of arrival**, and asks for no timestamp.
So only live readings can be sent: a backlog after an outage would land at the wrong
time, and must be skipped.

**Which of our sensors fit.** Sensor.Community knows only the models its own firmware
supports:

| Our source (model) | Their nearest match | Fit |
|---|---|---|
| `particulates` (SEN62/SEN65) | Pin 1 (SPS30, SDS011…): `P0` = PM1, `P2` = PM2.5, `P1` = PM10 | Good, and the map's main subject; but the SEN6x isn't on their list |
| `barometer` (BMP280) | Pin 3, BMP280 | Exact; the units may need converting (probably pascals, not hPa) |
| `humidity` (CHT8305) | Pin 7 (SHT3x, DHT22) | Good |
| `co2` (SCD41) | Pin 17, SCD30 | Possible, but a different model |
| light, MQ gases, ENS160, health | none | Not sent |

**To settle first:**

- **Registration:** a station is registered on `devices.sensor.community`, which ties each
  sensor type to a pin. Check what the form offers for a Raspberry Pi, and ask them
  whether a SEN6x may be listed as an SPS30, rather than quietly labelling it as one.
- **Siting:** the network is about outdoor air, so the station would need to be outside.
- **Privacy:** the station's location appears on a public map, and the data is published
  under an open licence.

**How it would be built.** A small separate service, `measure-station-db-sensorcommunity`,
switched on and off in `sysconfig.py` like the Modbus poller. The station, ingest service
and database don't change.

- It listens to the broker like the message checker: its own read-only login and a clean
  session (missed messages aren't wanted, since they'd be stale).
- It keeps the latest reading of each mapped source, and about every 2½ minutes sends the
  ones less than a few minutes old.
- When Sensor.Community can't be reached, it logs that and skips the round; nothing piles
  up.
- No new packages: Python's built-in `urllib` makes the HTTPS request.

Roughly the size of the message checker. Best built once phases 1 and 2 are running on
the Pi, and after registering with Sensor.Community.

## Other Kinds of Device

**Modbus TCP devices** are read by the Modbus poller, which publishes each one as a
"virtual station" in the station's own message format: see
[Reading Modbus Devices](docs/modbus-devices.md). It's off until a device is configured.

**Devices that publish MQTT in their own format** are for the future:

any station that sends the Measure Station message format is stored with no change,
whatever its sensors (new sources and keys become new series automatically), but a device
with its own MQTT format (e.g. Tasmota, ESPHome, Shelly, Zigbee2MQTT) needs a **translator**,
written once that device exists: a small function that turns its topic and payload into a
station, source, time and values. Each translator also needs:

- a subscription to the device's topics, and an access-list line letting it publish them;
- a rule for devices that send no time: use the time of arrival, marked as such (as the
  MQ board does with `ts_basis: "receipt"`).

Until then, nothing is lost: a message that doesn't fit is kept in `rejected_message`,
and can be translated and stored once its translator is written. (Messages on topics the
service doesn't subscribe to never reach it, so a new device must be added to the
subscription before its data is kept.)

## Proposed Project Layout

```text
src/measure_station_db/
    sysconfig.py        all settings (secrets from MEASURE_STATION_DB_* environment variables)
    logging_setup.py    logging to the journal, shared by every program
    messages.py         parsing and checking station messages (shared)
    ingest.py           the ingest service: MQTT to database
    schema.py           the database tables, applied by the ingest service
    watch.py            checker: shows and checks the messages arriving from stations
    modbus.py           the Modbus poller: Modbus TCP devices as virtual stations
    drivers/            code copied unchanged from other projects (the Modbus TCP client)
    remote/             the remote app: TV page and remote page
    display/            LED display and button (later)
deploy/
    install.sh          installs or updates the server's own programs
    env.example         template for /etc/measure-station-db/env (the passwords)
    grafana/            dashboards, as provisioning files (phase 3)
    systemd/            one unit per service
docs/                   one page per topic: Pi setup, operating, development
tests/                  tests; fixtures/ holds the station's documented example messages
```

The third-party services (Mosquitto, PostgreSQL + TimescaleDB, Grafana, the TV kiosk)
are installed and configured by hand, following
[Setting Up the Server Pi](docs/setting-up-the-server-pi.md), which holds their settings
files.

The package is `measure-station-db` (imported as `measure_station_db`, since Python names
can't contain hyphens). Python 3.11 or later. Main libraries: `paho-mqtt` 2.x, `psycopg` 3
(PostgreSQL), a small web framework for the remote app, and `smbus2` (display, later).

## Build Phases

1. **Pi setup** ([guide written](docs/setting-up-the-server-pi.md)): SSD boot,
   Raspberry Pi OS Lite 64-bit, fixed IP, NTP, Mosquitto with persistence and logins,
   PostgreSQL + TimescaleDB with the database and its logins, Grafana connected to the
   database, and the TV kiosk showing Grafana.
2. **Database and ingest service** ([written](docs/ingest-service.md), not yet run on
   the Pi): schema, hypertables, batching with acknowledgement after commit,
   de-duplication, status, tests, installer. Then the station is pointed at the server,
   and its backlog drains.
3. **Grafana:** the averages, and the first dashboards for phones.
4. **TV:** TV page, remote page and presets (the kiosk then shows the TV page).
5. **Revisit alerts** (deferred; see [Alerts](#alerts-deferred)), including whether to
   notify someone away from home.
6. **Backups,** once there's a location for them.
7. **LED display and button.**
8. **Later:** online storage, more stations, translators for other MQTT devices,
   HDMI-CEC, and sharing readings with
   [Sensor.Community](#sensorcommunity-future).

Also written, outside the phases: the message checker, and the Modbus poller (off by
default).

## Testing

- **Tests that don't need a database** (topic and payload checks, batching, the order of
  acknowledgements, bad JSON) run anywhere, including the Windows development PC.
- **Database tests** (schema, de-duplication, averages updating after late data) need
  PostgreSQL + TimescaleDB, so they run on the Pi. Where no database is available, they
  are skipped rather than failed.
