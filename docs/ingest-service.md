# The Ingest Service

The ingest service is the server's program that takes each reading from the MQTT broker
and saves it in the database. This guide installs it on the server Pi, checks that it
works, connects the first station, and covers everyday tasks.

Before you start, the server Pi must be set up as in
[Setting Up the Server Pi](setting-up-the-server-pi.md), at least up to Step 7 (the
broker and the database). You need two of the passwords chosen there: the broker
password of `measure-station-db` (Step 6) and the database password of `ingest`
(Step 7).

**This hasn't been tried on real hardware yet.** If a step fails, stop, copy the last 20
or so lines from the terminal, and send them to the server's developer.

Contents:

- [What It Does](#what-it-does)
- [Step 1: Download the Software](#step-1-download-the-software)
- [Step 2: Run the Installer](#step-2-run-the-installer)
- [Step 3: Enter the Passwords](#step-3-enter-the-passwords)
- [Step 4: Start It and Check It](#step-4-start-it-and-check-it)
- [Step 5: Check That the Broker Keeps Readings for It](#step-5-check-that-the-broker-keeps-readings-for-it)
- [Step 6: Connect the Station](#step-6-connect-the-station)
- [What's Stored](#whats-stored)
- [Everyday Tasks](#everyday-tasks)
- [Updating the Software](#updating-the-software)
- [Troubleshooting](#troubleshooting)

## What It Does

- **It never loses a reading the broker has accepted.** It confirms each message to the
  broker only after the database has saved it. Until then the broker keeps the message,
  and sends it again if the connection drops. While the service is stopped (for an
  update or a restart), the broker keeps everything for it.
- **It never stores a reading twice.** A reading sent twice (which happens around
  reconnections) has the same station, source and time, and the database ignores the
  second copy.
- **It saves in batches**, up to 500 readings at once, or whatever has arrived after a
  second. That's quick, and kind to the SSD.
- **It sets aside messages that don't fit.** A message that doesn't follow the station's
  interface (for example, from a different kind of device) is kept exactly as received
  in a separate table, logged and confirmed, so it can't block the ones behind it.
  Nothing is thrown away: if a new kind of device is added later, its messages can still
  be translated and stored properly.
- **It keeps the averages right.** The database keeps hourly and daily averages up to
  date by itself, but only for the last few days. When older readings arrive (a
  station's backlog after an outage), the service has the averages for those days
  recalculated.
- **It looks after itself.** It starts at boot, restarts after a crash, and waits for
  the broker and the database if they aren't ready yet.

## Step 1: Download the Software

Connect to the server Pi with `ssh`. The software is downloaded from GitHub into
`/opt/measure-station-db`.

If you've already downloaded it (for example, for the message checker), skip to
[Step 2](#step-2-run-the-installer). Otherwise:

```bash
sudo apt install -y git
sudo git clone https://github.com/Mescalero66/measure-station-db-python.git /opt/measure-station-db
```

## Step 2: Run the Installer

```bash
sudo bash /opt/measure-station-db/deploy/install.sh
```

It prints a line starting with `==>` for each stage, and takes a few minutes. It:

- installs the system packages it needs;
- creates a `measure-station-db` user for the service, with no login;
- installs the software and its libraries in a private Python folder,
  `/opt/measure-station-db/.venv`;
- creates the settings file `/etc/measure-station-db/env`, for the passwords;
- sets the service up to start at every boot.

It's finished when it prints **`Installation complete.`** and a list of next steps. It's
safe to run again.

## Step 3: Enter the Passwords

Open the settings file:

```bash
sudo nano /etc/measure-station-db/env
```

Put the two passwords after the `=` signs, with no spaces and no quotes:

```text
MEASURE_STATION_DB_MQTT_PASSWORD=the-broker-password-of-measure-station-db
MEASURE_STATION_DB_DATABASE_PASSWORD=the-database-password-of-ingest
```

Save with **Ctrl+O** and **Enter**, then exit with **Ctrl+X**. Only the service can read
this file.

## Step 4: Start It and Check It

```bash
sudo systemctl start measure-station-db-ingest
systemctl status measure-station-db-ingest
```

Look for **`Active: active (running)`** in green. Press **q** to quit. Then look at its
log:

```bash
sudo journalctl -u measure-station-db-ingest -n 20
```

You should see these lines:

```text
INFO [measure_station_db.ingest] Database measure_station_db is ready
INFO [measure_station_db.ingest] Connected to the broker at localhost:1883, a new session (the broker had kept nothing for it)
```

The first time, the database line means the service has created its tables. The
**new session** is expected the first time: from now on, the broker keeps messages for
the service whenever it's stopped. If you see something else, see
[Troubleshooting](#troubleshooting).

## Step 5: Check That the Broker Keeps Readings for It

This proves, before any real data is at stake, that messages sent while the service is
stopped are delivered when it starts again. The test message is deliberately not a
valid reading, so it's set aside rather than stored as a reading (you delete it
afterwards).

1. Stop the service:

   ```bash
   sudo systemctl stop measure-station-db-ingest
   ```

2. Send a test message as the station would, using the station's broker password from
   the setup guide's Step 6:

   ```bash
   mosquitto_pub -h localhost -u mstn-2b -P the-station-password -q 1 -t measure-station/mstn-2b/test -m hello
   ```

3. Start the service again, wait 5 seconds, and look at its log:

   ```bash
   sudo systemctl start measure-station-db-ingest
   sudo journalctl -u measure-station-db-ingest -n 5
   ```

   You should see:

   ```text
   INFO [measure_station_db.ingest] Connected to the broker at localhost:1883, resuming its saved session
   WARNING [measure_station_db.ingest] Set aside a message on measure-station/mstn-2b/test in rejected_message: not valid JSON
   ```

   **resuming its saved session** and the **Set aside a message** line together show
   that the broker kept the message while the service was stopped, and delivered it. If
   you see **a new session** instead, stop: the broker isn't keeping messages, and
   connecting a station would lose data. See [Troubleshooting](#troubleshooting).

4. Delete the test message from the set-aside messages:

   ```bash
   sudo -u postgres psql -d measure_station_db -c "DELETE FROM rejected_message WHERE topic = 'measure-station/mstn-2b/test';"
   ```

   It prints `DELETE 1`.

## Step 6: Connect the Station

Now the station can send its stored readings safely.

1. **Start the message checker** in a second terminal, so you can watch them arrive:
   connect to the server Pi with `ssh` again, and run

   ```bash
   /opt/measure-station-db/.venv/bin/python -m measure_station_db.watch
   ```

   It asks for the broker password of `measure-station-db`. See
   [Checking Messages From a Station](checking-station-messages.md) for what it shows.

2. **Point the station at the server**, following the station's guide,
   [Step 7: Connect to the Server Pi](https://github.com/Mescalero66/measure-station-python/blob/main/docs/deploying-the-station.md#step-7-connect-to-the-server-pi-later),
   with the settings listed in the setup guide's
   [What Comes Next](setting-up-the-server-pi.md#what-comes-next).

3. **Watch the backlog arrive.** The checker shows the station's `status` as `online`,
   then a rising count of older readings. The ingest service's log shows how many it
   stores each minute:

   ```bash
   sudo journalctl -u measure-station-db-ingest -f
   ```

   ```text
   INFO [measure_station_db.ingest] Stored 61204 readings and 1 status messages; set aside 0 that didn't fit
   ```

   A backlog of several months takes some minutes. When it's done, the count settles at
   about 11 readings a minute per station. A few minutes later you may see
   `Recalculated the averages from … to …`: the averages for the backlog's days.
   Press **Ctrl+C** to stop following the log.

4. **Check that everything arrived.** On the station, the number of readings waiting to
   be sent should be near zero:

   ```bash
   sudo sqlite3 /var/lib/measure-station/outbox.sqlite3 'select count(*) from outbox'
   ```

   On the server, count what's stored for each source, with its oldest and newest
   reading:

   ```bash
   sudo -u postgres psql -d measure_station_db -c "SELECT source, count(*), min(ts), max(ts) FROM reading GROUP BY source ORDER BY source;"
   ```

   Every source the station has fitted should be listed, with its oldest reading about
   when the station started, and its newest within the last minute or two (two and a
   half for the MQ sensors).

## What's Stored

The data is kept forever. Readings older than about 4 months are compressed
automatically, which makes them several times smaller.

| Table | Contents |
|---|---|
| `reading` | One row per reading, exactly as the station sent it, with the time the server received it (`received_at`). This is the master copy: everything else can be rebuilt from it |
| `reading_value` | The same readings split into one row per value (e.g. `co2_ppm`), with its section (`values` or `meta`). Numbers and true/false (as 1 or 0) are in `num_value`; text in `text_value`. Charts use this |
| `station_status` | Each time a station went `online` or `offline`, by the server's clock |
| `value_hourly`, `value_daily` | The average, minimum, maximum and number of readings of every numeric value, per hour and per day (UTC). Readings with an `estimated` time are left out, because their time can be far out |
| `rejected_message` | Every message that didn't follow the station's interface, exactly as received, with the time and what was wrong. Normally empty |

## Everyday Tasks

Connect with `ssh` first.

| Task | Command |
|---|---|
| Is it running? | `systemctl status measure-station-db-ingest` (press **q** to quit) |
| Watch its log | `sudo journalctl -u measure-station-db-ingest -f` (**Ctrl+C** to stop) |
| Its log since this morning | `sudo journalctl -u measure-station-db-ingest --since today` |
| Restart it (e.g. after changing a setting) | `sudo systemctl restart measure-station-db-ingest` |
| Stop it (the broker keeps readings meanwhile) | `sudo systemctl stop measure-station-db-ingest` |
| Start it again | `sudo systemctl start measure-station-db-ingest` |
| Readings stored today | `sudo -u postgres psql -d measure_station_db -c "SELECT count(*) FROM reading WHERE ts >= current_date;"` |
| Messages set aside (normally none) | `sudo -u postgres psql -d measure_station_db -c "SELECT received_at, topic, problems FROM rejected_message ORDER BY received_at DESC LIMIT 20;"` |
| Database size | `sudo -u postgres psql -d measure_station_db -c "SELECT pg_size_pretty(pg_database_size('measure_station_db'));"` |

Don't leave it stopped for long while a station is connected: the broker keeps the
waiting readings in memory.

## Updating the Software

When a new version is released:

```bash
cd /opt/measure-station-db
sudo git pull
sudo bash deploy/install.sh
```

The installer restarts the service with the new version. The broker keeps any readings
that arrive meanwhile.

## Troubleshooting

| Log message or problem | What to do |
|---|---|
| `Configuration error: MEASURE_STATION_DB_… not set` | A password is missing from `/etc/measure-station-db/env` ([Step 3](#step-3-enter-the-passwords)). The service doesn't restart itself for this: fix the file, then `sudo systemctl restart measure-station-db-ingest` |
| `Configuration error: TimescaleDB isn't switched on in this database` | Run step 5 of the setup guide's [Create the Database and Its Logins](setting-up-the-server-pi.md#create-the-database-and-its-logins) (`CREATE EXTENSION`), then restart the service |
| `Can't use the database (… password authentication failed …)` | The database password in the env file doesn't match the one set for `ingest`. Correct one of them, then restart the service |
| `Can't use the database (… Connection refused …)` | PostgreSQL isn't running: `systemctl status postgresql@17-main`. The service keeps retrying every 10 seconds |
| `The broker refused the connection: Not authorized` | The broker password in the env file doesn't match the one set for `measure-station-db` with `mosquitto_passwd`. Correct one of them, then restart the service |
| `Lost the connection to the broker` | Mosquitto stopped or restarted. The service reconnects by itself, and nothing is lost |
| `Can't store N message(s)`, repeating | The database has a problem. The broker keeps the readings meanwhile. Check PostgreSQL's log: `sudo journalctl -u postgresql@17-main -n 50` |
| `Set aside a message on …` | A message that doesn't follow the station's interface. It's kept in `rejected_message` (see [Everyday Tasks](#everyday-tasks)), not lost. If a station's real readings are being set aside, send the line to the developer. Messages from a new kind of device are expected here until the server is taught its format |
| `a new session (the broker had kept nothing for it)` after the first start | The broker had forgotten the service. It only forgets if its saved data was lost, e.g. `persistence` isn't on (setup guide, Step 6). Readings sent while the service was stopped may be lost: check the broker's settings |
| The averages are missing some days after a backlog | Recalculate them all: `sudo -u postgres psql -d measure_station_db -c "CALL refresh_continuous_aggregate('value_hourly', NULL, NULL);"`, then the same with `value_daily`. It can take a few minutes |
