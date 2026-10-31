# Setting Up the Server Pi

This guide takes a Raspberry Pi 4 from an empty USB SSD to a server ready to receive
Measure Station data. It installs and connects the off-the-shelf services the server is
built on. It takes about three hours. You don't need to know Linux: every command is
given in full, and each step says what you should see.

| Service | What it does | Installed in |
|---|---|---|
| **Mosquitto** | The MQTT broker: the "post office" the stations send their readings to | [Step 6](#step-6-install-the-mqtt-broker-mosquitto) |
| **PostgreSQL + TimescaleDB** | The database the readings are stored in | [Step 7](#step-7-install-the-database-postgresql--timescaledb) |
| **Grafana** | Draws the charts, for phones and the TV | [Step 8](#step-8-install-grafana) |
| **Chromium** | A web browser, shown full screen on the TV | [Step 9](#step-9-set-up-the-tv) |

The server's own programs (the ingest service, which moves readings from the broker into
the database, and the TV remote) come later, with their own installer. See the
[build phases](../README.md#build-phases).

**This hasn't been tried on real hardware yet.** If a step fails, stop, copy the last 20
or so lines from the terminal, and send them to the server's developer.

Contents:

- [What You Need](#what-you-need)
- [Linux Basics for This Guide](#linux-basics-for-this-guide)
- [Step 1: Switch the Pi to Booting From USB](#step-1-switch-the-pi-to-booting-from-usb)
- [Step 2: Write the Operating System to the SSD](#step-2-write-the-operating-system-to-the-ssd)
- [Step 3: Start the Pi and Connect to It](#step-3-start-the-pi-and-connect-to-it)
- [Step 4: Give the Pi a Fixed Address](#step-4-give-the-pi-a-fixed-address)
- [Step 5: Set the Time Source](#step-5-set-the-time-source)
- [Step 6: Install the MQTT Broker (Mosquitto)](#step-6-install-the-mqtt-broker-mosquitto)
- [Step 7: Install the Database (PostgreSQL + TimescaleDB)](#step-7-install-the-database-postgresql--timescaledb)
- [Step 8: Install Grafana](#step-8-install-grafana)
- [Step 9: Set Up the TV](#step-9-set-up-the-tv)
- [Step 10: Check Everything](#step-10-check-everything)
- [What Comes Next](#what-comes-next)
- [Everyday Tasks](#everyday-tasks)
- [Troubleshooting](#troubleshooting)

## What You Need

**Hardware**

| Item | Notes |
|---|---|
| Raspberry Pi 4 Model B (2 GB) | From the SC0400 desktop kit |
| The kit's power supply | The official USB-C supply. The SSD draws its power through the Pi, and a weaker supply makes it disconnect at random |
| USB SSD | Any size from 120 GB. Either a USB SSD, or a SATA SSD in a USB 3 adapter or enclosure. It's erased in Step 2 |
| microSD card | Only for Step 1; the kit's card will do. It's erased. Afterwards the Pi runs without one |
| Ethernet cable | More reliable than Wi-Fi for a server. Plug it into your router or a network switch |
| The kit's micro-HDMI cable, and a TV | For the TV display (Step 9). Also useful in Step 1 to see that the bootloader update worked |
| A computer | Windows, Mac or Linux, with a way to connect the SSD (a USB port) and the SD card (a card reader) |

The kit's keyboard and mouse aren't needed: you control the Pi from your computer.

**Information to collect before you start**

The examples below are used throughout this guide. Wherever you see one, use your own
value instead.

| What | Example | Used in |
|---|---|---|
| A name for the Pi on the network (its hostname) | `server1` | Step 2 |
| A username for logging in to the Pi | `admin` | Step 2 |
| The address the server Pi will always have | `192.168.1.20` | Step 4 |
| The IP address of the time source on your network, the same one the stations use | `192.168.1.1` | Step 5 |
| Each station's ID | `mstn-2b` | Step 6 |
| Access to your router's settings page | | Step 4 |

**Passwords to choose.** You need six. Store them in a password manager, or write them
down and keep them safe.

| Password for | Who uses it | Set in |
|---|---|---|
| Your login to the Pi | You | Step 2 |
| The broker login `measure-station-db` | The ingest service (later) | Step 6 |
| A broker login for each station, e.g. `mstn-2b` | That station | Step 6 |
| The database login `ingest` | The ingest service (later) | Step 7 |
| The database login `grafana_reader` | Grafana | Step 7 |
| Grafana's `admin` login | You, to edit dashboards | Step 8 |

**Use only letters, digits, `-` and `_` in every password**, and make each at least 16
characters long. Other characters (such as quotes, `$`, `\` or spaces) have special
meanings in the settings files and commands below, and would be changed or cut short.

## Linux Basics for This Guide

You control the Pi by typing commands into a **terminal**: a text window on your computer
that is connected to the Pi. These few things are all you need.

- **Typing a command.** Type it (or paste it) and press **Enter**. Linux is
  case-sensitive and spaces matter, so copy commands exactly. In this guide, commands are
  shown in grey boxes.
- **The prompt.** When the Pi is ready for a command it shows a line such as
  `admin@server1:~ $`. The part before `@` is your user; the part after is the Pi's name.
  While a command is still running, the prompt doesn't appear.
- **Pasting.** In Windows Terminal or PowerShell, paste with **Ctrl+V** or a right-click.
  On a Mac, use **Cmd+V**.
- **Ctrl+C stops a command.** In a terminal, Ctrl+C means "stop", not "copy". Use it to end
  a command that keeps running, such as a log viewer.
- **`sudo` means "as administrator".** Commands that change the system start with `sudo`.
  The first time, it asks for your Pi password. **Nothing appears while you type it**, not
  even dots. Type it and press Enter.
- **Pagers.** Some commands show their output one screen at a time. Use the arrow keys to
  scroll, and press **q** to quit.
- **Lines starting with `#`** in a settings file are comments: the system ignores them.
  The settings files in this guide use them to explain each setting.

**Creating or editing a file with nano**

`nano` is a simple text editor that runs inside the terminal. For example,
`sudo nano /etc/mosquitto/acl` opens that file, or starts a new empty one if it doesn't
exist yet (the file is created when you save).

| To | Press |
|---|---|
| Move around | The arrow keys (the mouse doesn't work) |
| Paste | As in the terminal: **Ctrl+V** or a right-click (Windows), **Cmd+V** (Mac) |
| Save | **Ctrl+O**, then **Enter** to keep the file name |
| Exit | **Ctrl+X**. If you haven't saved, it asks: press **Y**, then **Enter** |
| Search | **Ctrl+W**, type the word, then **Enter** |

**Whenever this guide says "create a file with these contents":** open it with the
`sudo nano` command given, paste the whole grey box into nano, change any example values
(such as addresses) to yours, then save and exit.

## Step 1: Switch the Pi to Booting From USB

A Pi normally starts from its SD card. This step updates the Pi's **bootloader** (the
small program, stored on the Pi itself, that starts everything else) so that it starts
from the USB SSD instead. Then everything is on the SSD, which lasts far longer than an
SD card under a database's constant writing.

1. On your computer, download and install **Raspberry Pi Imager** from
   [raspberrypi.com/software](https://www.raspberrypi.com/software/).
2. Put the microSD card in your computer's card reader and start Imager.
3. Choose:
   - **Device:** Raspberry Pi 4.
   - **Operating system:** *Misc utility images*, then *Bootloader (Pi 4 family)*, then
     **USB Boot**. (Menu names can differ slightly between Imager versions: look for the
     bootloader entry that mentions USB.)
   - **Storage:** your SD card. Everything on it is erased.
4. Write the card, then remove it from your computer.
5. With the Pi unplugged, put the SD card in the Pi (the slot is on the underside) and
   connect the TV to the Pi's **HDMI 0** port, the micro-HDMI port next to the USB-C
   power socket. Switch the TV to that input.
6. Plug in the power. After about 10 seconds, the **TV screen turns green** and the Pi's
   green light flashes steadily. That means the update worked.
7. Unplug the power and take out the SD card. You don't need it again.

## Step 2: Write the Operating System to the SSD

1. Connect the SSD to your computer and start Imager.
2. Choose:
   - **Device:** Raspberry Pi 4.
   - **Operating system:** *Raspberry Pi OS (other)*, then **Raspberry Pi OS Lite
     (64-bit)**. "Lite" has no desktop, which saves memory for the database and the TV's
     browser. It must be the **64-bit** version: Grafana and TimescaleDB need it.
   - **Storage:** your SSD. Check it's the right drive: everything on it is erased.
3. Imager offers to **customise the OS settings**. Accept, and set the following. (Older
   versions of Imager show these in an "Edit settings" window with tabs; newer ones show
   them as pages, one after another.)

   | Setting | Value |
   |---|---|
   | Hostname | Your chosen name, e.g. `server1` |
   | Username and password | Your chosen login. Write the password down |
   | Wireless LAN | Leave off (the Pi uses the Ethernet cable) |
   | Locale | Your time zone and keyboard layout |
   | SSH (on the Services or Remote access page) | **Enable**, with password authentication |

4. Write the SSD. It takes 5 to 15 minutes. When Imager says it's finished, disconnect
   the SSD.

## Step 3: Start the Pi and Connect to It

**SSH** lets you type commands on the Pi from your own computer, over the network.

1. Connect the SSD to one of the Pi's **blue** USB ports. The blue ones are USB 3, which
   is about ten times faster than the black ones.
2. Connect the Ethernet cable from the Pi to your router or switch. Leave the TV
   connected if you like: it shows the Pi starting up.
3. Plug in the power. The red light comes on and the green light flickers.
4. **Wait 3 minutes.** On its first start, the Pi sets itself up and restarts once.
5. On your computer, open a terminal:
   - **Windows:** press the Windows key, type `PowerShell` and press Enter.
   - **Mac:** open **Terminal** (in Applications, then Utilities).
6. Connect, using your username and hostname from Step 2:

   ```bash
   ssh admin@server1.local
   ```

7. The first time, it asks `Are you sure you want to continue connecting?`. Type `yes`
   and press Enter.
8. Enter your Pi password (it doesn't show as you type). You should now see the Pi's
   prompt, e.g. `admin@server1:~ $`. Every command from here on is typed at this prompt.

If `ssh` says it can't find the host, see [Troubleshooting](#troubleshooting).

**Check that the Pi started from the SSD:**

```bash
findmnt -n -o SOURCE /
```

It should print `/dev/sda2`. If it prints something starting with `/dev/mmcblk`, the Pi
started from an SD card: unplug it, remove the card, and start again.

Now bring the operating system up to date. This can take 10 to 20 minutes:

```bash
sudo apt update && sudo apt full-upgrade -y
```

When the prompt comes back, restart the Pi:

```bash
sudo reboot
```

Your SSH connection closes. Wait 2 minutes, then connect again with the same `ssh`
command.

## Step 4: Give the Pi a Fixed Address

The stations find the server by its IP address, so that address must never change. By
default, your router hands out addresses and may give the Pi a different one after a
restart. A **DHCP reservation** tells the router to always give the Pi the same address.

1. Find the Pi's network hardware address (its MAC address):

   ```bash
   ip link show eth0
   ```

   Look for the line starting with `link/ether`. The address after it looks like
   `dc:a6:32:12:34:56`.

2. Open your router's settings page in a web browser (often `http://192.168.1.1`; the
   address and login are usually on a label on the router).
3. Find the setting called **DHCP reservation**, **address reservation**, **static
   lease** or **static DHCP**. It's often under *LAN* or *DHCP server*.
4. Add a reservation for the Pi's MAC address, with the address you've chosen, e.g.
   `192.168.1.20`. Choose one in the same range as your other devices' addresses. Save.
5. Restart the Pi so it collects its new address:

   ```bash
   sudo reboot
   ```

6. Wait 2 minutes, connect again, and check:

   ```bash
   hostname -I
   ```

   The first address printed should be the one you chose.

## Step 5: Set the Time Source

The Pi has no clock that keeps time while it's off, so it sets its clock from the network
each time it starts. **The server and the stations must all get the time from the same
source on your network**, so that their clocks agree. Use the same time source as the
stations (their `MEASURE_STATION_NTP_SERVERS` setting), with a public server as a
fallback.

1. Create the folder for the setting:

   ```bash
   sudo mkdir -p /etc/systemd/timesyncd.conf.d
   ```

2. Create a file with these contents, using `sudo nano
   /etc/systemd/timesyncd.conf.d/measure-station-db.conf`:

   ```ini
   # Time source for the Measure Station server: the same one the stations use,
   # then a public server as a fallback. See docs/setting-up-the-server-pi.md.
   [Time]
   NTP=192.168.1.1 pool.ntp.org
   ```

   Replace `192.168.1.1` with your time source's address. The servers are separated by a
   space, in order of preference.

3. Apply it:

   ```bash
   sudo systemctl restart systemd-timesyncd
   ```

4. Check, after a minute:

   ```bash
   timedatectl timesync-status
   ```

   The `Server:` line should show your time source's address. Then:

   ```bash
   timedatectl
   ```

   should show **`System clock synchronized: yes`**.

**The server Pi must never serve time to the stations.** It has no battery-backed clock,
so after a power cut its clock starts out wrong. The setup above only *receives* time, so
it can't. Don't install `chrony` or `ntp` on it.

## Step 6: Install the MQTT Broker (Mosquitto)

The broker is the post office between the stations and the database. A station hands
each reading to the broker and waits for a receipt; as soon as it has the receipt, the
station deletes its own copy. So the broker must look after every message it has given a
receipt for, until the ingest service has saved it in the database.

### Install It

```bash
sudo apt install -y mosquitto mosquitto-clients
```

`mosquitto` is the broker; `mosquitto-clients` adds two test tools, `mosquitto_pub`
(send a message) and `mosquitto_sub` (listen for messages), used below.

Mosquitto's main settings file already saves messages to disk ("persistence"). Check:

```bash
grep persistence /etc/mosquitto/mosquitto.conf
```

You should see `persistence true` and `persistence_location /var/lib/mosquitto/`. Don't
repeat these two lines in the file below: Mosquitto refuses to start if
`persistence_location` is set twice.

### Settings

Create a file with these contents, using `sudo nano
/etc/mosquitto/conf.d/measure-station-db.conf`:

```text
# Measure Station server settings. See docs/setting-up-the-server-pi.md.
# persistence and persistence_location are set in /etc/mosquitto/mosquitto.conf.

# Listen for the stations on the network, on the standard MQTT port.
listener 1883

# Everyone must log in, and may only use the topics the access list allows.
allow_anonymous false
password_file /etc/mosquitto/passwd
acl_file /etc/mosquitto/acl

# Save waiting messages to disk every 60 s (the default is 30 minutes), so a crash or
# power cut loses at most a minute of them.
autosave_interval 60

# Keep any number of messages for the ingest service while it's stopped. The default
# (1000) would throw away the rest of a station's backlog.
max_queued_messages 0

# Send the ingest service up to 1000 messages before it confirms them (the default is
# 20), so it can save them to the database in large batches.
max_inflight_messages 1000
```

**Waiting messages are held in memory.** Normally the ingest service collects messages
within a second or so, and almost nothing waits. But if the ingest service is stopped
while a station sends a large backlog (after weeks without a connection), the broker
holds all of it in memory, and a backlog of several months could need several hundred
MB. So keep the ingest service running whenever a station is connected.

### Who May Do What

The **access list** says which login may use which topics. Create it with these
contents, using `sudo nano /etc/mosquitto/acl`:

```text
# Measure Station access list. See docs/setting-up-the-server-pi.md.

# Each station logs in with its station ID as its username (e.g. mstn-2b), and may only
# publish under its own topics. %u stands for the username, so this one line covers
# every station.
pattern write measure-station/%u/#

# The ingest service may read every station's messages.
user measure-station-db
topic read measure-station/#
```

### Logins

Create the ingest service's login. The `-c` creates the password file, so **use `-c`
only this once**: using it again erases every login already in the file.

```bash
sudo mosquitto_passwd -c /etc/mosquitto/passwd measure-station-db
```

It asks for the password twice (nothing shows as you type).

Then add a login for each station. **The username must be exactly the station's ID**,
because the access list uses it to decide which topics the station may publish on:

```bash
sudo mosquitto_passwd /etc/mosquitto/passwd mstn-2b
```

Mosquitto only reads these two files if they belong to it and are hidden from other
users. Run this now, and again **every time you add or change a login**:

```bash
sudo chown mosquitto:mosquitto /etc/mosquitto/passwd /etc/mosquitto/acl
sudo chmod 600 /etc/mosquitto/passwd /etc/mosquitto/acl
```

### Start It

```bash
sudo systemctl restart mosquitto
systemctl status mosquitto
```

Look for **`Active: active (running)`** in green. Press **q** to quit. If it says
`failed`, see [Troubleshooting](#troubleshooting).

### Test It

You need two terminals for this. Open a second terminal window on your computer and
connect to the Pi with `ssh` again, as in Step 3.

1. **In the first terminal**, listen as the ingest service (use its password from
   above):

   ```bash
   mosquitto_sub -h 192.168.1.20 -u measure-station-db -P the-ingest-password -t 'measure-station/#' -v
   ```

   It waits, printing nothing yet.

2. **In the second terminal**, send a test message as the station:

   ```bash
   mosquitto_pub -h 192.168.1.20 -u mstn-2b -P the-station-password -t measure-station/mstn-2b/test -m hello
   ```

   The first terminal should print `measure-station/mstn-2b/test hello`. That proves the
   logins work, and that the broker answers on its network address, as a station would
   reach it.

3. **Check that a station can't publish under another station's name:**

   ```bash
   mosquitto_pub -h 192.168.1.20 -u mstn-2b -P the-station-password -t measure-station/other/test -m nope
   ```

   Nothing should appear in the first terminal. (The broker discards it without an error
   message.)

4. **Check that logging in is required:**

   ```bash
   mosquitto_pub -h 192.168.1.20 -t measure-station/mstn-2b/test -m nope
   ```

   It should fail with `not authorised`.

Stop the listener in the first terminal with **Ctrl+C**, and close the second terminal.
These test messages aren't stored anywhere.

**Don't connect a station yet.** The broker only keeps messages for a listener it already
knows about. Until the ingest service has connected once (the next phase of the build),
the broker would give the station receipts for its readings and then throw them away. The
station keeps its readings safely until then. See [What Comes Next](#what-comes-next).

## Step 7: Install the Database (PostgreSQL + TimescaleDB)

**PostgreSQL** is the database. **TimescaleDB** is an add-on for it that handles
time-stamped data: it keeps hourly and daily averages up to date by itself, and
compresses old data. Both come from their makers' own package sources, which have newer
versions than Raspberry Pi OS's.

### Install It

1. Install the tools used to add the package sources:

   ```bash
   sudo apt install -y gnupg postgresql-common apt-transport-https lsb-release wget
   ```

2. Add PostgreSQL's own package source. It asks you to press **Enter** to continue:

   ```bash
   sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh
   ```

3. Add TimescaleDB's package source and its signing key (the key lets the Pi check that
   the packages really come from Timescale). These are two long commands, each on one
   line:

   ```bash
   echo "deb https://packagecloud.io/timescale/timescaledb/debian/ $(lsb_release -c -s) main" | sudo tee /etc/apt/sources.list.d/timescaledb.list
   ```

   ```bash
   wget --quiet -O - https://packagecloud.io/timescale/timescaledb/gpgkey | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/timescaledb.gpg
   ```

4. Install PostgreSQL 17 with TimescaleDB:

   ```bash
   sudo apt update
   sudo apt install -y timescaledb-2-postgresql-17 postgresql-client-17 timescaledb-tools
   ```

   This is the **community edition** of TimescaleDB, which includes the averages and
   compression features. Don't install `timescaledb-2-oss-postgresql-17`: that edition
   leaves them out.

### Tune It for the Pi

TimescaleDB comes with a tool that sets PostgreSQL's memory and other settings to suit the
machine. The database shares the Pi's 2 GB with the broker, Grafana and the TV's browser,
so tell it to plan for 512 MB rather than all of it:

```bash
sudo timescaledb-tune --memory=512MB --yes
```

It prints the settings it changed. It also tells PostgreSQL to load TimescaleDB at
start-up.

Next, stop TimescaleDB from sending usage statistics to its makers. Create the folder,
then a file:

```bash
sudo mkdir -p /etc/postgresql/17/main/conf.d
```

Create a file with these contents, using `sudo nano
/etc/postgresql/17/main/conf.d/measure-station-db.conf`:

```ini
# Measure Station server. See docs/setting-up-the-server-pi.md.
# Don't send usage statistics to Timescale.
timescaledb.telemetry_level = off
```

Restart the database to apply both:

```bash
sudo systemctl restart postgresql
```

### Create the Database and Its Logins

You create two logins (PostgreSQL calls them "roles"):

- **`ingest`** owns the database: the ingest service creates the tables and writes the
  readings.
- **`grafana_reader`** can only read, so a mistake in a chart can never change or delete
  data.

1. Open PostgreSQL's command prompt as its administrator:

   ```bash
   sudo -u postgres psql
   ```

   The prompt changes to `postgres=#`. Commands typed here are database commands, not
   Linux ones; each ends with `;`.

2. Create the `ingest` login, then set its password (`\password` asks for it twice;
   nothing shows as you type):

   ```sql
   CREATE ROLE ingest LOGIN;
   ```

   ```text
   \password ingest
   ```

3. The same for `grafana_reader`:

   ```sql
   CREATE ROLE grafana_reader LOGIN;
   ```

   ```text
   \password grafana_reader
   ```

4. Create the database, owned by `ingest`, and switch to it (the prompt changes to
   `measure_station_db=#`):

   ```sql
   CREATE DATABASE measure_station_db OWNER ingest;
   ```

   ```text
   \c measure_station_db
   ```

5. Switch on TimescaleDB in this database, and let `grafana_reader` read everything:

   ```sql
   CREATE EXTENSION IF NOT EXISTS timescaledb;
   GRANT pg_read_all_data TO grafana_reader;
   ```

6. Check the edition:

   ```sql
   SHOW timescaledb.license;
   ```

   It should say **`timescale`**. If it says `apache`, the wrong edition was installed:
   see [Troubleshooting](#troubleshooting).

7. Leave the database prompt:

   ```text
   \q
   ```

### Test the Logins

```bash
psql -h localhost -U grafana_reader -d measure_station_db -c 'SELECT 1;'
```

Enter the `grafana_reader` password. It should print a small table with `1` in it. Do the
same with `-U ingest` and its password.

**The database is empty for now.** The ingest service creates its tables when it's
installed. The database is only reachable from the Pi itself (PostgreSQL's default),
which is all that's needed: everything that uses it runs on the Pi. It's stored on the
SSD, in `/var/lib/postgresql/17/main`.

## Step 8: Install Grafana

Grafana draws the charts. It runs as a website on the Pi, which phones and the TV open in
a browser.

### Install It

1. Add Grafana's package source and its signing key:

   ```bash
   sudo mkdir -p /etc/apt/keyrings
   sudo wget -O /etc/apt/keyrings/grafana.asc https://apt.grafana.com/gpg-full.key
   sudo chmod 644 /etc/apt/keyrings/grafana.asc
   echo "deb [signed-by=/etc/apt/keyrings/grafana.asc] https://apt.grafana.com stable main" | sudo tee /etc/apt/sources.list.d/grafana.list
   ```

2. Install it (the `grafana` package is the open-source edition):

   ```bash
   sudo apt update
   sudo apt install -y grafana
   ```

### Settings

Grafana's settings are given to it as a list of `GF_...` values, in a small add-on file
for the service that starts it. Create the folder:

```bash
sudo mkdir -p /etc/systemd/system/grafana-server.service.d
```

Then create a file with these contents, using `sudo nano
/etc/systemd/system/grafana-server.service.d/measure-station-db.conf`:

```ini
# Grafana settings for the Measure Station server. See docs/setting-up-the-server-pi.md.
[Service]
# Anyone on the network can view dashboards without logging in. Editing them still
# needs the admin login.
Environment=GF_AUTH_ANONYMOUS_ENABLED=true
Environment=GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer
# Let the TV page show dashboards inside itself.
Environment=GF_SECURITY_ALLOW_EMBEDDING=true
# Don't send usage statistics to Grafana Labs, or check the internet for updates.
Environment=GF_ANALYTICS_REPORTING_ENABLED=false
Environment=GF_ANALYTICS_CHECK_FOR_UPDATES=false
Environment=GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false
```

### Connect It to the Database

Grafana needs to know where the database is, and how to log in to it. Create a file with
these contents, using `sudo nano
/etc/grafana/provisioning/datasources/measure-station-db.yaml`:

```yaml
# The Measure Station database, read-only. See docs/setting-up-the-server-pi.md.
apiVersion: 1
datasources:
  - name: Measure Station
    uid: measure-station-db
    type: postgres
    url: localhost:5432
    user: grafana_reader
    isDefault: true
    jsonData:
      database: measure_station_db
      sslmode: disable
      timescaledb: true
    secureJsonData:
      password: the-grafana-reader-password
```

Replace `the-grafana-reader-password` with the `grafana_reader` password from Step 7.
Keep the spaces at the start of each line exactly as shown: in this kind of file, they
matter.

The file contains a password, so hide it from other users:

```bash
sudo chown root:grafana /etc/grafana/provisioning/datasources/measure-station-db.yaml
sudo chmod 640 /etc/grafana/provisioning/datasources/measure-station-db.yaml
```

### Start It

Start Grafana, and set it to start at every boot:

```bash
sudo systemctl daemon-reload
sudo systemctl enable grafana-server
sudo systemctl restart grafana-server
```

Wait 30 seconds (Grafana is slow to start on a Pi), then check:

```bash
systemctl status grafana-server
```

Look for **`Active: active (running)`**. Press **q** to quit.

### First Login

1. On your computer, open a web browser and go to `http://192.168.1.20:3000` (the
   server's address, then `:3000`).
2. Click **Sign in** and log in as `admin`, password `admin`. Grafana then asks you to
   choose a new password: use the Grafana admin password you chose.
3. Check the database connection: open the menu (☰, top left), then **Connections**, then
   **Data sources**, then **Measure Station**. Scroll to the bottom and click **Test**. It
   should say the database connection is OK.

You can also open the same address on a phone connected to your home Wi-Fi. There are no
dashboards yet: they come with the next phases.

## Step 9: Set Up the TV

The TV shows a web browser (Chromium) full screen, with nothing else: no desktop, no
address bar. For now it shows Grafana's home page; later it shows the TV page that the
phone remote controls.

How it works: at start-up, the Pi logs your user in automatically on its own screen, and
that login starts the browser. **Cage** is a tiny display program whose only job is to
show one app full screen.

1. **Set the TV picture to 1080p.** At 4K, the browser would make the Pi work four times
   as hard for no visible gain at viewing distance. This adds a setting to the Pi's start-up
   options, unless it's already there:

   ```bash
   grep -q 'video=HDMI-A-1' /boot/firmware/cmdline.txt || sudo sed -i '1 s/$/ video=HDMI-A-1:1920x1080@60/' /boot/firmware/cmdline.txt
   ```

   Check it: `cat /boot/firmware/cmdline.txt` should print **one** line, ending with
   `video=HDMI-A-1:1920x1080@60`.

2. **Install the browser and Cage:**

   ```bash
   sudo apt install -y cage chromium
   ```

3. **Create the kiosk script**, which starts the browser and starts it again if it ever
   closes. Create a file with these contents, using `nano ~/kiosk.sh` (no `sudo`: this
   file belongs to your user):

   ```bash
   #!/bin/sh
   # Shows a web page full screen on the TV, and restarts the browser if it closes.
   # Its messages go to the system log, tagged "kiosk". See docs/setting-up-the-server-pi.md.
   URL="http://localhost:3000/?kiosk"
   while true; do
       systemd-cat -t kiosk cage -- chromium --kiosk --incognito --noerrdialogs \
           --disable-infobars --no-first-run --password-store=basic \
           --ozone-platform=wayland "$URL"
       sleep 5
   done
   ```

   Then make it runnable:

   ```bash
   chmod +x ~/kiosk.sh
   ```

4. **Start it when your user logs in on the TV.** This adds two lines to the end of your
   login settings, which start the kiosk only on the Pi's own screen, not when you
   connect with `ssh`. Paste all five lines at once, and **do it only once**:

   ```bash
   cat >> ~/.profile <<'EOF'

   # Start the TV kiosk, on the Pi screen only (not over ssh).
   if [ "$(tty)" = "/dev/tty1" ]; then exec ~/kiosk.sh; fi
   EOF
   ```

   Check it: `tail -n 2 ~/.profile` should show the comment and the `if` line.

5. **Log in automatically on the TV at start-up:**

   ```bash
   sudo raspi-config nonint do_boot_behaviour B2
   ```

   (`B2` is raspi-config's "console, logged in automatically" option. The same setting
   is in `sudo raspi-config`, under *System Options*, then *Boot / Auto Login*.)

6. **Restart, and watch the TV:**

   ```bash
   sudo reboot
   ```

   After a minute or so, the TV should show Grafana's home page, filling the screen.

To get out of the kiosk, connect with `ssh` as usual: the TV keeps showing the browser
while you work.

## Step 10: Check Everything

Connect with `ssh` and run these checks.

| Check | Command | Expect |
|---|---|---|
| Started from the SSD | `findmnt -n -o SOURCE /` | `/dev/sda2` |
| Clock synchronised | `timedatectl` | `System clock synchronized: yes` |
| Broker running | `systemctl status mosquitto` | `active (running)` (**q** to quit) |
| Database running | `systemctl status postgresql@17-main` | `active (running)` |
| Grafana running | `systemctl status grafana-server` | `active (running)` |
| Memory | `free -h` | In the `Mem:` row, `available` above about 500 MB, with the TV showing Grafana |
| Memory safety net | `swapon --show` | A line for `/dev/zram0`: compressed swap in memory, which current Raspberry Pi OS sets up by itself (older versions show `/var/swap` instead, which also works) |
| Free space | `df -h /` | Plenty in `Avail` |

Then restart the Pi once more (`sudo reboot`) and check that everything comes back by
itself: the TV shows Grafana, and the three services are running.

**The server Pi is set up.**

## What Comes Next

The next phase of the build installs the **ingest service**, which creates the database
tables and moves readings from the broker into the database. It uses the logins
`measure-station-db` (broker) and `ingest` (database).

**Only after the ingest service has run once,** connect each station by following the
station's guide,
[Step 7: Connect to the Server Pi](https://github.com/Mescalero66/measure-station-python/blob/main/docs/deploying-the-station.md#step-7-connect-to-the-server-pi-later),
with:

| Station setting | Value |
|---|---|
| `MEASURE_STATION_MQTT_HOST` | The server's fixed address, e.g. `192.168.1.20` |
| `MEASURE_STATION_MQTT_PORT` | `1883` |
| `MEASURE_STATION_MQTT_USERNAME` | The station's ID, e.g. `mstn-2b` |
| `MEASURE_STATION_MQTT_PASSWORD` | The station's broker password from Step 6 |

**Adding another station later:** give it a broker login with its station ID as the
username (Step 6, [Logins](#logins), including the `chown` and `chmod` commands), then
`sudo systemctl restart mosquitto`. The access list already covers it, and the database
needs no change.

## Everyday Tasks

Connect with `ssh` first, as in Step 3.

| Task | Command |
|---|---|
| Are the services running? | `systemctl status mosquitto postgresql@17-main grafana-server` (press **q** to quit) |
| The broker's log | `sudo tail -n 50 /var/log/mosquitto/mosquitto.log` |
| The database's log | `sudo journalctl -u postgresql@17-main -n 50` |
| Grafana's log | `sudo journalctl -u grafana-server -n 50` |
| Free memory | `free -h` |
| Free space on the SSD | `df -h /` (look at `Avail`) |
| Update all the software | `sudo apt update && sudo apt full-upgrade -y`, then `sudo reboot` |
| Shut down safely | `sudo shutdown -h now`, then wait for the green light to stop before unplugging |

**Always shut down before unplugging** if you can. The broker saves its waiting messages
to disk on a clean stop; pulling the plug can lose up to the last 60 seconds of them.

## Troubleshooting

| Problem | What to do |
|---|---|
| The TV doesn't turn green in Step 1 | Check the TV is on the right input, and that the cable is in **HDMI 0** (next to the power socket). Write the SD card again and retry |
| The Pi doesn't start from the SSD | Try the other blue USB port. Check Step 1 worked. Some USB-to-SATA adapters don't work with a Pi 4: try another adapter, or a different SSD |
| The SSD disconnects, or is very slow | Some USB-to-SATA adapters misbehave with the Pi 4's fast USB mode. Run `lsusb` and find the adapter's line, e.g. `ID 152d:0578`. Add ` usb-storage.quirks=152d:0578:u` (with your ID, and a space before it) to the end of the single line in `/boot/firmware/cmdline.txt` with `sudo nano`, then `sudo reboot` |
| `ssh` says `Could not resolve hostname` | Your computer can't find the Pi by name. Find its IP address in your router's list of connected devices (look for your hostname), then use it: `ssh admin@192.168.1.20` |
| `ssh` says `Connection refused` or times out | The Pi is still starting (wait 2 more minutes), or SSH wasn't enabled in Step 2 (write the SSD again) |
| `ssh` warns `REMOTE HOST IDENTIFICATION HAS CHANGED` | Normal after writing the SSD again. Clear the old entry, then connect again: `ssh-keygen -R server1.local` |
| `System clock synchronized: no` for a long time | Check the address in `/etc/systemd/timesyncd.conf.d/measure-station-db.conf`, and that the time source is reachable: `timedatectl timesync-status` shows which server it's trying |
| Mosquitto shows `failed` | Its log says why: `sudo journalctl -u mosquitto -n 30`. Usually a typo in one of the files from Step 6. `Duplicate persistence_location` means the persistence lines were repeated in `measure-station-db.conf`: remove them |
| A test in Step 6 says `not authorised` when it should work | The username or password is wrong. Set the password again with `sudo mosquitto_passwd /etc/mosquitto/passwd <username>` (no `-c`), run the `chown` and `chmod` commands, then `sudo systemctl restart mosquitto` |
| `apt` can't find `timescaledb-2-postgresql-17` | Check the TimescaleDB source was added: `cat /etc/apt/sources.list.d/timescaledb.list` should show one `deb` line ending in `trixie main` (or your system's name). Then `sudo apt update` and try again |
| `SHOW timescaledb.license;` says `apache` | The open-source-only edition was installed. Run `sudo apt install -y timescaledb-2-postgresql-17`, then `sudo systemctl restart postgresql`, and check again |
| Grafana's **Test** says `password authentication failed` | The password in the Grafana file doesn't match the database. Set it again in the database (Step 7, `\password grafana_reader`), put the same one in the Grafana file (Step 8), then `sudo systemctl restart grafana-server` |
| Forgotten the Grafana admin password | `sudo grafana cli --homepath /usr/share/grafana --config /etc/grafana/grafana.ini admin reset-admin-password the-new-password`, then `sudo systemctl restart grafana-server` |
| The TV shows a login prompt or text, not the browser | Check steps 4 and 5 of [Step 9](#step-9-set-up-the-tv). The browser's messages since start-up are in `journalctl -b -t kiosk` |
| The browser shows "Aw, Snap!" or the Pi becomes very slow | Probably out of memory. Check `free -h`, and send the output to the developer |
| The TV goes black after about 10 minutes | Run `sudo sed -i '1 s/$/ consoleblank=0/' /boot/firmware/cmdline.txt` (once), then `sudo reboot` |
