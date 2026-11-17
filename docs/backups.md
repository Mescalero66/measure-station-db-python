# Backups

This guide sets up a nightly backup of the whole database. Every night the Pi sends a copy
to a backup folder on your Synology NAS, and the NAS copies that folder on to Google
Drive. Setting it up takes about an hour, most of it on the NAS.

**Why it matters:** until this is done, the Pi's SSD holds the only copy of the readings.
The stations delete each reading once the server has it, so if the SSD failed, they'd
all be lost.

**This hasn't been tried on real hardware yet,** least of all the Synology steps (menu
names change between versions). If a step doesn't do what it says, stop, copy the last 20
or so lines from the terminal, and send them to the server's developer.

You'll be typing commands on the Pi, as in the
[setup guide](setting-up-the-server-pi.md#linux-basics-for-this-guide): every command is
given in full, and each step says what you should see.

Contents:

- [Words Used in This Guide](#words-used-in-this-guide)
- [How It Works](#how-it-works)
- [What You Need](#what-you-need)
- [Step 1: Prepare the NAS](#step-1-prepare-the-nas)
- [Step 2: Update the Pi's Software](#step-2-update-the-pis-software)
- [Step 3: Give the Pi a Key to the NAS](#step-3-give-the-pi-a-key-to-the-nas)
- [Step 4: Choose the Backup Password](#step-4-choose-the-backup-password)
- [Step 5: Create the Backup Folder](#step-5-create-the-backup-folder)
- [Step 6: Switch On and Make the First Backup](#step-6-switch-on-and-make-the-first-backup)
- [Step 7: Copy It to Google Drive](#step-7-copy-it-to-google-drive)
- [Step 8: Try a Restore](#step-8-try-a-restore)
- [Which Copies Are Kept](#which-copies-are-kept)
- [Everyday Tasks](#everyday-tasks)
- [Restoring After a Failure](#restoring-after-a-failure)
- [Settings](#settings)
- [Troubleshooting](#troubleshooting)

## Words Used in This Guide

| Word | Meaning |
|---|---|
| **NAS** | Your Synology box: a small computer full of disks, for storing files on your home network |
| **DSM** | The NAS's control panel, a web page you open in your browser |
| **Shared folder** | A top-level folder on the NAS, with its own list of who may use it |
| **SFTP** | A safe way of copying files over the network. The Pi uses it to send backups to the NAS |
| **Key** | A pair of files that work like a door key, so the Pi can log in to the NAS without typing a password: a private file that stays on the Pi, and a public one that goes on the NAS |
| **restic** | The backup program on the Pi |
| **Repository** | restic's name for the backup folder on the NAS. It holds every copy, in pieces, encrypted |
| **Copy** (restic says *snapshot*) | One night's backup: the whole database as it was that night |
| **Encrypted** | Scrambled with a password, so it's unreadable without it |
| **Cloud Sync** | Synology's app that copies a NAS folder to Google Drive |

## How It Works

```text
 Server Pi, 03:30 every night            Synology NAS                     Google Drive
 database ──► pg_dump ──► restic ──SFTP──► backups/measure-station-db ──Cloud Sync──► a copy
                          (encrypts)                                   (upload only)
```

- **`pg_dump`** is the database's own export tool. It writes the whole database out as
  one file, which can be loaded back into an empty database later.
- **restic** cuts that file into pieces, and stores only the pieces it hasn't seen
  before. Most of the database is old readings that never change, so each night adds
  only that day's new readings: a few MB. That's why many copies take little more space
  than one.
- **Everything is encrypted on the Pi, before it leaves,** with a password only you have.
  Google, or anyone who gets into the NAS or your Google account, sees only scrambled
  files.
- **Copies kept:** one a day for 2 weeks, one a week for 2 months, and one a month for 2
  years. See [Which Copies Are Kept](#which-copies-are-kept).
- **Every Sunday,** restic also reads back a tenth of the stored pieces, to check
  they're still sound.
- **If anything goes wrong,** no copy is saved that night, the old ones are kept, and the
  log says what happened.
- **What a restore loses:** the readings since the last backup, so up to a day.

## What You Need

| What | Example | Used in |
|---|---|---|
| The NAS's address on your network. It must never change: give it a fixed address (a *DHCP reservation*) in your router, the same way as for the server Pi in the setup guide's [Step 4](setting-up-the-server-pi.md#step-4-give-the-pi-a-fixed-address) | `192.168.1.30` | Steps 3 and 5 |
| An administrator login to the NAS (the one you use for DSM) | | Steps 1 and 7 |
| A Google account for the off-site copy | | Step 7 |
| A password manager (or a safe place to write a password down) | | Step 4 |
| Free space on the NAS and in Google Drive: about 1 GB for the first year, then about 0.5 GB a year (estimates) | | |

**Google Drive's free 15 GB is shared with Gmail and Google Photos.** To see how much is
free, open [drive.google.com](https://drive.google.com) and look at the bottom left.

**Wherever this guide shows `192.168.1.30`, type your NAS's address instead.**

## Step 1: Prepare the NAS

On your computer, open DSM in a web browser and log in as an administrator. You'll make
a folder for the backups, a login for the Pi that can only reach that folder, and switch
on SFTP.

### Make a Shared Folder

1. Click **Control Panel**, then **Shared Folder**, then **Create**, then **Create Shared
   Folder**.
2. **Name:** `backups`. **Untick** **Enable Recycle Bin**: restic tidies away old pieces
   itself, and a recycle bin would keep them, filling the disk. Click **Next**.
3. Leave encryption off (restic already encrypts) and click **Next**.
4. If it offers **Enable data checksum for advanced data integrity**, tick it: the NAS
   then notices if a stored file gets damaged. Click **Next**, then **Apply**.
5. If a window asks who may use the folder, close it: the next part sets that.

### Make a Login for the Pi

1. In **Control Panel**, click **User & Group**, the **User** tab, then **Create**.
2. **Name:** `measure-station-db`. **Password:** make up a long one and write it down for
   a moment: you need it once, in Step 3. Tick **Disallow the user to change account
   password**. Click **Next**.
3. **Join groups:** leave just `users`. Click **Next**.
4. **Assign shared folder permissions:** for `backups`, tick **Read/Write**. For every
   other folder, tick **No access**. Click **Next**.
5. **User quota:** leave it, click **Next**.
6. **Assign application permissions:** tick **Allow** for **SFTP** only, and **Deny** for
   all the others. (If there's no SFTP row, allow **FTP** instead: DSM covers SFTP with
   it. FTP itself stays switched off.) Click **Next**.
7. **Speed limit:** leave it, click **Next**, then **Done**.

### Switch On SFTP and Home Folders

1. In **Control Panel**, click **File Services**, then the **FTP** tab. Under **SFTP**,
   tick **Enable SFTP service**, leave the port at `22`, and click **Apply**.
2. In **Control Panel**, click **User & Group**, then the **Advanced** tab. Under **User
   Home**, tick **Enable user home service**, and click **Apply**. (The Pi's key is
   stored in its login's home folder, so this must be on.)

## Step 2: Update the Pi's Software

Connect to the Pi with `ssh` (setup guide,
[Step 3](setting-up-the-server-pi.md#step-3-start-the-pi-and-connect-to-it)), then run
these three commands, one at a time:

```bash
cd /opt/measure-station-db
sudo git pull
sudo bash deploy/install.sh
```

The first moves into the server's software folder, the second downloads the latest
version, and the third installs it (including restic). It takes a few minutes. The last
lines should include:

```text
Backups are off until switched on: see docs/backups.md.
```

## Step 3: Give the Pi a Key to the NAS

1. **Make the key.** This creates the two key files in the Pi's settings folder, and lets
   only the backup service read the private one:

   ```bash
   sudo ssh-keygen -t ed25519 -N "" -C measure-station-db-backup -f /etc/measure-station-db/backup-key
   sudo chown measure-station-db:measure-station-db /etc/measure-station-db/backup-key
   sudo chmod 600 /etc/measure-station-db/backup-key
   ```

   The first command prints `Your identification has been saved in …` and a small
   picture made of characters (the key's "fingerprint"). The other two print nothing.

2. **Record the NAS's own key.** The NAS has a key too, which proves it's really your
   NAS. This saves it, so the Pi can check it every night:

   ```bash
   ssh-keyscan -t ed25519 192.168.1.30 | sudo tee /etc/measure-station-db/backup-known-hosts
   ```

   It should print one line starting with `192.168.1.30 ssh-ed25519` (and perhaps a line
   starting with `#`). If it prints nothing, check the NAS's address and that SFTP is
   switched on (Step 1).

3. **Put the public key on the NAS.** This logs in to the NAS once with the password from
   Step 1, and leaves the public key there:

   ```bash
   sudo ssh-copy-id -f -s -i /etc/measure-station-db/backup-key.pub measure-station-db@192.168.1.30
   ```

   - If it asks `Are you sure you want to continue connecting (yes/no)?`, type `yes` and
     press Enter.
   - When it asks for `measure-station-db@192.168.1.30's password:`, type the NAS login's
     password from Step 1. (If `sudo` asks for a password first, that's your Pi
     password.) Nothing shows as you type.
   - It should end with `Number of key(s) added: 1`.

   You no longer need the NAS login's password: the Pi uses the key from now on.

4. **Check the key works,** by logging in exactly as the backup will:

   ```bash
   sudo -u measure-station-db sftp -i /etc/measure-station-db/backup-key -o BatchMode=yes -o UserKnownHostsFile=/etc/measure-station-db/backup-known-hosts measure-station-db@192.168.1.30
   ```

   You should get an `sftp>` prompt, **without being asked for a password**. Type `ls`
   and press Enter: the list should include `backups`. Type `bye` and press Enter to
   leave.

   If it says `Permission denied`, see
   [Fixing the Key on the NAS](#fixing-the-key-on-the-nas), then try this check again.

## Step 4: Choose the Backup Password

This password encrypts the backups. **Without it, nobody can read them, not even you.**
If the Pi's SSD fails, the copy of the password on the Pi is lost with it, so it must
also be kept somewhere else. Save it in your password manager **before** using it.

1. Make a random password:

   ```bash
   tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 32; echo
   ```

   It prints 32 random letters and digits, e.g. `q7Rk2VwX9mLp4TzN8bHc3YdF6sJg1Ea5`.

2. Copy it: select it with the mouse, then press **Ctrl+Shift+C** (Windows Terminal) or
   **Cmd+C** (Mac). (In the terminal, Ctrl+C on its own means "stop", not "copy".)
3. **Save it in your password manager** now, e.g. as "Measure Station backup password".
4. Add it to the Pi's passwords file. Open the file:

   ```bash
   sudo nano /etc/measure-station-db/env
   ```

   Move to the end with the arrow keys, and add this line, pasting your password after
   the `=` (no spaces):

   ```text
   MEASURE_STATION_DB_BACKUP_PASSWORD=q7Rk2VwX9mLp4TzN8bHc3YdF6sJg1Ea5
   ```

   Save (**Ctrl+O**, then **Enter**) and exit (**Ctrl+X**).

## Step 5: Create the Backup Folder

1. **Tell the Pi the NAS's address.** Skip this if your NAS is at `192.168.1.30`.
   Otherwise open the server's settings file:

   ```bash
   sudo nano /opt/measure-station-db/src/measure_station_db/sysconfig.py
   ```

   Press **Ctrl+W**, type `BACKUP_REPOSITORY` and press Enter, to jump to this line:

   ```python
   BACKUP_REPOSITORY: str = "sftp:measure-station-db@192.168.1.30:/backups/measure-station-db"
   ```

   Change only `192.168.1.30` to your NAS's address, keeping the quotes and everything
   else. Save and exit.

2. **Create the backup folder** (the *repository*):

   ```bash
   sudo measure-station-db-backup init
   ```

   You should see:

   ```text
   created restic repository 1a2b3c4d5e at sftp:measure-station-db@192.168.1.30:/backups/measure-station-db

   Please note that knowledge of your password is required to access
   the repository. Losing your password means that your data is
   irrecoverably lost.
   ```

   In DSM's **File Station**, `backups` now holds a folder `measure-station-db`, with a
   few folders inside.

## Step 6: Switch On and Make the First Backup

1. **Switch backups on.** Open the settings file again (as in Step 5), press **Ctrl+W**,
   type `BACKUP_ENABLED` and press Enter. Change `False` to `True`, so the line reads:

   ```python
   BACKUP_ENABLED: bool = True
   ```

   Save and exit.

2. **Make the first backup now,** rather than waiting for tonight:

   ```bash
   sudo systemctl start measure-station-db-backup
   ```

   The first backup stores everything, so it takes a few minutes. Nothing is shown while
   it works; the prompt comes back when it's done.

3. **Read what it did:**

   ```bash
   sudo journalctl -u measure-station-db-backup -n 20
   ```

   The last lines should look like this (your numbers will differ):

   ```text
   INFO [measure_station_db.backup] Backing up measure_station_db (postgresql-17.6, timescaledb-2.29.2) to sftp:measure-station-db@192.168.1.30:/backups/measure-station-db
   INFO [measure_station_db.backup] Backed up the database (2.1 GiB): 812.4 MiB of new data stored, in 395 s, as copy 4f8e2a1b
   INFO [measure_station_db.backup] Kept the copies of the last 14 days, 8 weeks and 24 months
   ```

   - "2.1 GiB" is the size of the whole export; "812.4 MiB of new data" is what was
     stored after compression. Tomorrow night, the new data will be only a few MB.
   - Lines containing `warning: there are circular foreign-key constraints` are normal.
   - A line with `ERROR` means it failed: see [Troubleshooting](#troubleshooting).

4. **List the copies:**

   ```bash
   sudo measure-station-db-backup restic snapshots
   ```

   There should be one copy:

   ```text
   ID        Time                 Host                Tags                                         Paths                     Size
   ---------------------------------------------------------------------------------------------------------------------------------
   4f8e2a1b  2026-11-18 14:02:11  measure-station-db  database,postgresql-17.6,timescaledb-2.29.2  /measure_station_db.dump  2.1 GiB
   ---------------------------------------------------------------------------------------------------------------------------------
   1 snapshots
   ```

   The tags record the database's versions, which a restore needs (see
   [Restoring After a Failure](#restoring-after-a-failure)).

5. **Check it's scheduled:**

   ```bash
   systemctl list-timers measure-station-db-backup
   ```

   Under **NEXT**, it should show 03:30 tonight.

## Step 7: Copy It to Google Drive

Synology's **Cloud Sync** app copies the backup folder to Google Drive, and keeps that
copy up to date. This protects the backups from anything that affects your home, such as
a fire or theft.

1. In DSM, open **Package Center**, search for **Cloud Sync**, and click **Install**.
2. Open **Cloud Sync** (from the main menu, top left). Choose **Google Drive**, and sign in
   to your Google account when asked.
3. On the settings page:
   - **Local path:** click it and choose `backups`, then `measure-station-db`.
   - **Remote path:** make a new folder in your Drive, e.g. `Measure Station backup`.
   - **Sync direction:** **Upload local changes only.** This matters: with a two-way
     sync, deleting the files in Google Drive would delete them on the NAS too.
   - Leave **Don't remove files in the destination folder when they are removed in the
     source folder** unticked, so Google Drive doesn't fill up with pieces restic has
     tidied away.
   - Leave **Enable data encryption** unticked: restic has already encrypted everything.
4. Click **Next**, then **Done**. Cloud Sync starts uploading. The first time, it uploads
   the size shown in Step 6, which can take a while on a slow connection.

When it's finished, Google Drive has a `Measure Station backup` folder containing
`config`, `data`, `index`, `keys` and `snapshots`. You can't open the files there: they're
encrypted, as they should be.

## Step 8: Try a Restore

A backup that has never been restored is only a hope. This step proves yours works: it
loads the latest copy into a separate **test** database, compares it with the real one,
then deletes the test database. **The real database isn't touched.** Do it now, and again
every few months.

1. **Check there's room.** The test database is as big as the real one:

   ```bash
   df -h /
   ```

   The **Avail** column should be larger than the backup's size from Step 6 (e.g.
   `2.1 GiB`).

2. **Let `sudo` remember your password for a few minutes.** Step 4 below has two `sudo`
   commands on one line, and they'd both ask for it at once:

   ```bash
   sudo true
   ```

3. **Make an empty test database,** ready for a restore. (`pre_restore` tells
   TimescaleDB that a restore is about to happen.)

   ```bash
   sudo -u postgres psql -c "CREATE DATABASE measure_station_db_restore_test OWNER ingest;"
   sudo -u postgres psql -d measure_station_db_restore_test -c "CREATE EXTENSION timescaledb;" -c "SELECT timescaledb_pre_restore();"
   ```

   You should see `CREATE DATABASE`, then `CREATE EXTENSION`, then a small table showing
   `t` (true).

4. **Take the latest copy out of the backup and load it.** The `|` passes it straight
   from restic into the database, so no temporary file is needed. It takes a few minutes:

   ```bash
   sudo measure-station-db-backup restic dump latest /measure_station_db.dump | sudo -u postgres pg_restore --dbname measure_station_db_restore_test
   ```

   Some lines saying `already exists` are normal (step 3 had already switched TimescaleDB
   on).

5. **Finish the restore:**

   ```bash
   sudo -u postgres psql -d measure_station_db_restore_test -c "SELECT timescaledb_post_restore();"
   ```

   You should see a small table showing `t`.

6. **Compare the two databases.** This counts the readings in each, and shows the newest:

   ```bash
   sudo -u postgres psql -d measure_station_db -c "SELECT count(*), max(ts) FROM reading;"
   sudo -u postgres psql -d measure_station_db_restore_test -c "SELECT count(*), max(ts) FROM reading;"
   ```

   The test copy should have nearly as many readings, and its newest (`max`) should be
   from around the time of the backup. The real database has a few more: the ones that
   arrived since.

7. **Delete the test database:**

   ```bash
   sudo -u postgres psql -c "DROP DATABASE measure_station_db_restore_test;"
   ```

   You should see `DROP DATABASE`.

## Which Copies Are Kept

After each backup, restic removes the copies that are no longer needed, keeping:

| Kept | How many | So you can go back |
|---|---|---|
| The last copy of each day | 14 | Any day in the last 2 weeks |
| The last copy of each week | 8 | Week by week, for 2 months |
| The last copy of each month | 24 | Month by month, for 2 years |

**Older readings are never lost by this.** Each copy is a complete copy of the whole
database: every reading from the first day up to that night. So when a copy from more
than 2 years ago is removed, the readings in it are still in every newer copy. Think of
it like a photo album, re-printed in full every night: throwing away an old print run
doesn't lose any photos, because the newest one has them all.

What *is* lost is the ability to go back to how the database looked more than 2 years
ago, e.g. to undo a mistake made back then. To keep copies for longer, raise
`BACKUP_KEEP_MONTHLY` (see [Settings](#settings)). Because restic only stores each piece
once, extra monthly copies take very little space.

## Everyday Tasks

Connect to the Pi with `ssh` first.

| Task | Command |
|---|---|
| List the copies | `sudo measure-station-db-backup restic snapshots` |
| Read last night's log | `sudo journalctl -u measure-station-db-backup --since yesterday` |
| Did the last backup work? | `systemctl status measure-station-db-backup` (press **q** to quit). `status=0/SUCCESS` means it did |
| When's the next one? | `systemctl list-timers measure-station-db-backup` |
| Back up now (e.g. before an update) | `sudo systemctl start measure-station-db-backup` |
| Space used by the backups | `sudo measure-station-db-backup restic stats --mode raw-data` |
| Check every stored piece (slow) | `sudo measure-station-db-backup restic check --read-data` |
| Switch backups off | Set `BACKUP_ENABLED` back to `False` in `sysconfig.py` (as in Step 6). Each night it then just logs that backups are off |

## Restoring After a Failure

This is for when the Pi's SSD (or the Pi) has failed, and the readings must come back
from the latest copy. It's rarely needed, and best done with the developer's help: send
them a message before you start. **You'll need the backup password from your password
manager:** without it, the backups can't be read.

1. **Set up a new SSD** with [Setting Up the Server Pi](setting-up-the-server-pi.md),
   Steps 1 to 8, as the first time. In Step 7, use the same passwords if you have them.
   - **The TimescaleDB version must match the backup's.** The copy's tags show it (e.g.
     `timescaledb-2.29.2`). If the setup guide installed a newer version, install the
     backup's one instead, then restart the database:

     ```bash
     sudo apt install -y --allow-downgrades timescaledb-2-postgresql-17='2.29.2*' timescaledb-2-loader-postgresql-17='2.29.2*'
     sudo systemctl restart postgresql
     ```

2. **Install the server's software** with [The Ingest Service](ingest-service.md), Steps 1
   to 3 (install and passwords), but **don't start it yet**: it would create empty
   tables, which the restore would clash with.
3. **Reconnect to the backups:** this guide's Steps 3 to 5, but **skip `init`** (the
   backup folder already exists). In Step 4, put the **same** backup password in the
   passwords file, from your password manager. Then
   `sudo measure-station-db-backup restic snapshots` should list your copies.
4. **Make the real database empty,** ready for the restore (it has no readings yet, so
   nothing is lost):

   ```bash
   sudo systemctl stop measure-station-db-ingest
   sudo -u postgres psql -c "DROP DATABASE measure_station_db;" -c "CREATE DATABASE measure_station_db OWNER ingest;"
   ```

5. **Restore:** [Step 8](#step-8-try-a-restore)'s steps 2 to 5, but with
   `measure_station_db` in place of `measure_station_db_restore_test`, and **without**
   step 3's first command (the database was made in step 4 above).
6. **Start the ingest service** (The Ingest Service guide, Step 4 onwards). The stations
   then send whatever they've kept since the failure.
7. **Update TimescaleDB,** if you installed an older version in step 1:

   ```bash
   sudo apt upgrade
   sudo -u postgres psql -d measure_station_db -X -c "ALTER EXTENSION timescaledb UPDATE;"
   ```

8. **Switch backups on again** (Step 6).

**If the NAS is lost too:** the copy in Google Drive is complete. Download the `Measure
Station backup` folder to a USB drive, plug it into the Pi, and set `BACKUP_REPOSITORY` to
that folder's path. The developer can help with this.

## Settings

In `/opt/measure-station-db/src/measure_station_db/sysconfig.py`. Changes apply from the
next backup; nothing needs restarting.

| Setting | Default | Meaning |
|---|---|---|
| `BACKUP_ENABLED` | `False` | `True` to back up every night |
| `BACKUP_REPOSITORY` | `sftp:measure-station-db@192.168.1.30:/backups/measure-station-db` | Where the copies go: the NAS (over SFTP), or a folder on the Pi such as a USB drive |
| `BACKUP_KEEP_DAILY`, `BACKUP_KEEP_WEEKLY`, `BACKUP_KEEP_MONTHLY` | `14`, `8`, `24` | How many daily, weekly and monthly copies to keep |
| `BACKUP_CHECK_WEEKDAY` | `6` (Sunday) | The night of the weekly check (0 = Monday) |
| `BACKUP_CHECK_READ` | `10%` | How much of the stored data the weekly check reads back |

The time of night (03:30) is set in `deploy/systemd/measure-station-db-backup.timer`
(`OnCalendar`).

## Troubleshooting

| Problem or message | What to do |
|---|---|
| Step 3's check says `Permission denied (publickey,password)` | The NAS didn't accept the key. Usually its home folder's permissions are too open for SSH to trust. See [Fixing the Key on the NAS](#fixing-the-key-on-the-nas) |
| `Host key verification failed` | The NAS's own key changed (e.g. the NAS was reset or replaced), or Step 3's second command was skipped. Run that command again |
| `Connection refused` or `Connection timed out` | SFTP isn't switched on (Step 1), or the address is wrong |
| `Configuration error: … is missing (docs/backups.md, Step 3)` | Step 3 wasn't finished: a key file isn't there |
| `Configuration error: MEASURE_STATION_DB_BACKUP_PASSWORD not set` | Add it to the passwords file (Step 4) |
| `wrong password or no key found` | The password in the passwords file isn't the backup folder's. Copy it again from your password manager |
| `Can't use the database (…)` | The database isn't running, or its password in the passwords file is wrong. See the ingest guide's [Troubleshooting](ingest-service.md#troubleshooting) |
| `The weekly check found a problem` | A stored piece is damaged. Run `sudo measure-station-db-backup restic check --read-data` to find which, and send the output to the developer. The nightly backups carry on |
| `repository is already locked` | Another restic command is running (e.g. one you started). The backup waits up to 30 minutes for it. If nothing else is running (e.g. after a power cut), run `sudo measure-station-db-backup restic unlock` |
| `sudo git pull` says `Your local changes would be overwritten` | Your edits to `sysconfig.py` clash with an update. Run `sudo git stash`, then `sudo git pull`, then `sudo git stash pop`. If that reports a conflict, ask the developer |

### Fixing the Key on the NAS

SSH refuses a key stored in a folder that other users could change, and Synology's home
folders sometimes look like that. You fix it by logging in to the NAS as its
administrator, once.

1. In DSM, open **Control Panel**, then **Terminal & SNMP**. Tick **Enable SSH service**,
   and click **Apply**.
2. On the Pi, show the public key:

   ```bash
   sudo cat /etc/measure-station-db/backup-key.pub
   ```

   It prints one long line starting with `ssh-ed25519` and ending with
   `measure-station-db-backup`. Copy the whole line (select it, then **Ctrl+Shift+C** or
   **Cmd+C**).
3. Log in to the NAS with your DSM administrator login (replace `your-dsm-admin` with
   its name):

   ```bash
   ssh your-dsm-admin@192.168.1.30
   ```

   Type `yes` if asked to continue connecting, then your DSM password. The prompt changes
   to the NAS's name.

4. On the NAS, run these one at a time. In the third line, paste the key line in place
   of `the-key-line`, keeping the single quotes around it. `sudo` asks for your DSM
   password.

   ```bash
   H=/var/services/homes/measure-station-db
   sudo mkdir -p $H/.ssh
   echo 'the-key-line' | sudo tee $H/.ssh/authorized_keys
   sudo chown -R measure-station-db:users $H
   sudo chmod 755 $H
   sudo chmod 700 $H/.ssh
   sudo chmod 600 $H/.ssh/authorized_keys
   exit
   ```

5. Back on the Pi, try [Step 3](#step-3-give-the-pi-a-key-to-the-nas)'s check (4) again.
   Once it works, switch SSH off again in DSM's **Terminal & SNMP** (the Pi uses SFTP,
   which stays on).
