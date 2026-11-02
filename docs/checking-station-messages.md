# Checking Messages From a Station

This guide shows how to check, on the server Pi, that a station's readings are reaching
the MQTT broker, and that they're in the form the station's interface promises. Use it
when you first connect a station, and later whenever data seems to be missing.

There are two tools:

- **A quick look** with `mosquitto_sub`, which the
  [setup guide](setting-up-the-server-pi.md#step-6-install-the-mqtt-broker-mosquitto)
  already installed. It shows the raw messages as they arrive.
- **The checker**, `measure_station_db.watch`. It shows one tidy line per reading, checks
  every message against the station's interface, and when you stop it, lists each
  source it heard from and the ones it didn't.

**Both only listen.** The broker sends every listener its own copy of each message, so
watching never takes messages away from the ingest service, and never changes anything.

Contents:

- [Before You Connect a Station](#before-you-connect-a-station)
- [A Quick Look With mosquitto_sub](#a-quick-look-with-mosquitto_sub)
- [Install the Checker](#install-the-checker)
- [Run the Checker](#run-the-checker)
- [What the Problems Mean](#what-the-problems-mean)
- [Troubleshooting](#troubleshooting)
- [Updating the Checker](#updating-the-checker)

## Before You Connect a Station

**Connect a station only once the ingest service is installed and running** (see
[The Ingest Service](ingest-service.md)).

As soon as a station connects, it sends every reading it has stored, and deletes each one
once the broker has received it. The broker keeps a message only for a listener it
already knows about: the ingest service, once it has connected. Before that, the broker
receives the readings and throws them away, and the station has already deleted its
copies. Watching with these tools doesn't keep anything, because they only listen while
they run.

## A Quick Look With mosquitto_sub

No installation needed. Connect to the server Pi with `ssh`, then:

```bash
mosquitto_sub -h localhost -u measure-station-db -P the-ingest-password -t 'measure-station/+/+' -v
```

It prints each message's topic, then its contents:

```text
measure-station/mstn-2b/status online
measure-station/mstn-2b/co2 {"station":"mstn-2b","source":"co2","sensor_type":"scd41","ts":"2026-10-21T09:40:02.517+00:00","ts_src":"ntp","values":{"co2_ppm":500,"temperature_c":25.0,"humidity_pct":37.0},"meta":{"pressure_hpa":1007,"comp_src":"hpa"}}
```

The `status` line appears straight away, because the broker keeps the last one. Readings
appear about once a minute per sensor. Press **Ctrl+C** to stop.

This is a temporary listener: it has no `-i` or `-c`, so the broker forgets it when it
stops.

## Install the Checker

The checker is part of this project's software, so it's installed with the ingest
service: [Step 1](ingest-service.md#step-1-download-the-software) and
[Step 2](ingest-service.md#step-2-run-the-installer) of its guide.

## Run the Checker

```bash
/opt/measure-station-db/.venv/bin/python -m measure_station_db.watch
```

It asks for the broker password of `measure-station-db` (nothing shows as you type),
connects, and prints a line for each message:

```text
Connected to the broker at localhost:1883 as measure-station-db.
Waiting for messages. Press Ctrl+C to stop and see the summary.

15:53:26  mstn-2b    status        online
15:53:28  mstn-2b    co2              2 s old  ntp        222 B  co2_ppm=500 temperature_c=25.0 humidity_pct=37.0
15:53:28  mstn-2b    humidity         2 s old  ntp        170 B  temperature_c=18.41 humidity_pct=61.3
15:53:28  mstn-2b    light            2 s old  ntp        171 B  illuminance_lux=166.64
15:53:29  ... 1,250 older readings received so far (a backlog: counted, not shown one by one)
```

Each line shows:

| Column | Meaning |
|---|---|
| `15:53:28` | When the message arrived, by the server's clock |
| `mstn-2b` | The station |
| `co2` | The source: which sensor (or `status`, or `health` for the station's own condition) |
| `2 s old` | How long ago the station took the reading. A few seconds is normal |
| `ntp` | How the station got the reading's time: `ntp` and `corrected` are accurate; `estimated` means the station's clock wasn't set (see [Troubleshooting](#troubleshooting)) |
| `222 B` | The message's size, in bytes |
| The rest | The reading's values (shortened if long) |

**A backlog.** When a station connects for the first time, or after an outage, it sends
its stored readings first, oldest first, as fast as the broker takes them. Readings more
than 10 minutes old aren't shown one by one: a running count appears instead, every few
seconds. When the backlog has been sent, normal lines resume.

**Watch for at least 3 minutes.** Most sources send once a minute, but each MQ gas sensor
only every 150 seconds.

Then press **Ctrl+C**. The checker prints a summary:

```text
Watched for 3 min 12 s.

Station mstn-2b (status: online)
  Source         Received  Newest reading    Problems
  air-quality           3  21 s old          0
  barometer             3  20 s old          0
  co2                   3  20 s old          0
  cpu                   3  22 s old          0
  health                3  19 s old          0
  humidity              3  19 s old          0
  light                 3  20 s old          0
  mqboard-mq135         1  75 s old          0
  mqboard-mq2           1  75 s old          0
  mqboard-mq4           1  75 s old          0
  mqboard-mq5           1  75 s old          0
  mqboard-mq7           1  75 s old          0
  mqboard-mq8           1  75 s old          0
  particulates          3  18 s old          0
  temperature           3  21 s old          0

Every message followed the station's interface.
```

**What a good result looks like:**

- the station's status is `online`;
- every source the station has fitted has received readings, with `0` problems;
- the newest readings are a minute or two old at most (150 seconds for the MQ sensors);
- the last line says **Every message followed the station's interface.**

A source that's never received is listed under **Not received**. It's either disabled on
the station (e.g. a sensor that isn't fitted), or it has failed: see
[Troubleshooting](#troubleshooting).

## What the Problems Mean

A message with a problem is shown as `PROBLEM:` instead of its values, and counted in the
summary. Problems mean the message doesn't follow the station's interface. The ingest
service doesn't store such a message as a reading: it keeps it as received, in the
`rejected_message` table, so nothing is lost. Note the exact line and tell the
developer.

| Problem | Meaning |
|---|---|
| `not valid JSON` | The contents aren't readable data. Something other than the station software may be publishing |
| `the payload's station or source doesn't match the topic` | The message says it's from a different station or sensor than its topic does |
| `'…' is missing or not a …` | A field the interface requires is missing or the wrong type |
| `'ts' … isn't an ISO 8601 time with a time zone …` | The reading's time can't be understood, or has no time zone, so the moment it means is unknown. (Any standard form with a zone is accepted, e.g. ending in `Z` or `+10:00`, with or without milliseconds) |
| `taken in the future: the station's and the server's clocks disagree` | The reading's time is ahead of the server's clock. The two Pis aren't using the same time source, or one hasn't set its clock. See the setup guide's [Step 5](setting-up-the-server-pi.md#step-5-set-the-time-source) and the station's time server setting |
| `status is …, not 'online' or 'offline'` | Something other than the station is publishing on its `status` topic |
| `unexpected topic …` | A message on a topic that isn't `measure-station/<station>/<source>` |

## Troubleshooting

| Problem | What to do |
|---|---|
| `Can't reach the broker at localhost:1883` | Mosquitto isn't running. Check with `systemctl status mosquitto`, and see the setup guide's [Troubleshooting](setting-up-the-server-pi.md#troubleshooting) |
| `The broker refused the login` | The password for `measure-station-db` is wrong. Set it again (setup guide, [Logins](setting-up-the-server-pi.md#logins)) |
| Nothing at all arrives, not even `status` | The station hasn't connected to this broker yet. On the station, check its log: `sudo journalctl -u measure-station -n 20` should show `Connected to MQTT broker`. If it shows `MQTT link down`, check its `MEASURE_STATION_MQTT_HOST` (this Pi's address), port, username and password (the station's guide, [Step 7](https://github.com/Mescalero66/measure-station-python/blob/main/docs/deploying-the-station.md#step-7-connect-to-the-server-pi-later)) |
| The broker's log shows the station connecting, but no readings arrive | The station's username must be exactly its station ID, or the access list blocks its messages. Check the broker's log: `sudo tail -n 30 /var/log/mosquitto/mosquitto.log` |
| `status: offline` | The station stopped, or lost its connection. It's set by the station when it stops, or by the broker about 45 seconds after the connection drops |
| A source is under **Not received** | If that sensor isn't fitted, that's expected. Otherwise, check the station's log for that sensor: `sudo journalctl -u measure-station -n 50` on the station. A sensor that keeps failing logs `crashed` |
| Many lines show `estimated` | The station's clock isn't set (it can't reach its time server). Its readings are kept and re-timed on the station once it is, except its `health` readings, which are sent straight away so the problem is visible. Check the station's time server setting |
| A long backlog never seems to finish | The count keeps rising while it arrives. If new lines never appear, check the station's `outbox_backlog` in its `health` readings: it falls to near zero when the backlog has been sent |

## Updating the Checker

It's updated with the rest of the software: see
[Updating the Software](ingest-service.md#updating-the-software).
