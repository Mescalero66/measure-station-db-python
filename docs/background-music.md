# Background Music on the TV

This guide sets up background music that plays through the TV's speakers, all the time,
with no controls. The music is a folder of MP3s, one for each hour of the day in three
weathers, named like `03 pm (Rain).mp3`. It takes about half an hour, plus the time to
copy the music.

**This hasn't been tried on the Pi yet.** The program was tested on a PC with a stand-in
player. If a step doesn't do what it says, stop, and send the last 20 or so lines from
the terminal to the server's developer.

Contents:

- [How It Plays](#how-it-plays)
- [Words Used in This Guide](#words-used-in-this-guide)
- [Step 1: Update the Pi's Software](#step-1-update-the-pis-software)
- [Step 2: Check the TV's Sound](#step-2-check-the-tvs-sound)
- [Step 3: Copy the Music to the Pi](#step-3-copy-the-music-to-the-pi)
- [Step 4: Check the Music](#step-4-check-the-music)
- [Step 5: Choose the Weather Chances](#step-5-choose-the-weather-chances)
- [Step 6: Switch It On](#step-6-switch-it-on)
- [Adding or Changing Music](#adding-or-changing-music)
- [Everyday Tasks](#everyday-tasks)
- [Settings](#settings)
- [Troubleshooting](#troubleshooting)

## How It Plays

- **One track per hour, in clock order:** `12 am`, `01 am`, … `11 am`, `12 pm`, `01 pm`,
  … `11 pm`, then `12 am` again. The hours are only names: a track called `03 pm` can play
  at any time of day. An hour with no tracks is skipped.
- **The weather is picked at random for each hour,** with chances you set. The default is
  60% Sunny, 25% Rain and 15% Snow. If an hour has no file for one weather, its chance is
  shared among the others.
- **It always plays.** It starts by itself when the Pi starts, and carries on after a
  power cut: the track that was playing starts again from its beginning, and the hours
  carry on from there.
- **The volume is the TV's.** Use the TV's remote.
- **Between tracks there's a short gap,** a fraction of a second, while the next track
  starts.

## Words Used in This Guide

| Word | Meaning |
|---|---|
| **HDMI sound** | The HDMI cable carries sound as well as the picture, so the TV's speakers play the Pi's sound |
| **HDMI 0** | The Pi 4 has two small HDMI sockets. HDMI 0 is the one **next to the power socket**. The TV is on it |
| **mpg123** | A small program that plays MP3 files. The music service hands it one track at a time |
| **`scp`** | "Secure copy": copies files to the Pi over the network, using the same login as `ssh`. It's built into Windows |

## Step 1: Update the Pi's Software

Connect to the Pi with `ssh`, then:

```bash
cd /opt/measure-station-db
sudo git pull
sudo bash deploy/install.sh
```

This installs the music program and `mpg123`, and lets the server's programs use the
sound. The last lines should include:

```text
The background music is off until switched on: see docs/background-music.md.
```

## Step 2: Check the TV's Sound

1. Turn the TV on, on the Pi's HDMI input, with its volume at a normal level.
2. List the Pi's sound outputs:

   ```bash
   aplay -l
   ```

   You should see a line with **`vc4hdmi0`**, something like this (the card numbers may
   differ):

   ```text
   card 1: vc4hdmi0 [vc4-hdmi-0], device 0: MAI PCM i2s-hifi-0 [MAI PCM i2s-hifi-0]
   ```

3. Play a test sound on the TV:

   ```bash
   speaker-test -D hdmi:CARD=vc4hdmi0,DEV=0 -c 2 -t wav -l 1
   ```

   The TV should say **"Front left"**, then **"Front right"**, and the command ends by
   itself.

If there's no `vc4hdmi0` line, or no sound, see [Troubleshooting](#troubleshooting).

## Step 3: Copy the Music to the Pi

1. **On the Pi** (in `ssh`), make the music folder, owned by your login (`mstn` in these
   guides) so you can copy into it:

   ```bash
   sudo install -d -o mstn -g mstn -m 0755 /srv/music
   ```

   It prints nothing.

2. **On your PC,** open PowerShell (or VS Code's terminal), and copy the folder with your
   MP3s. Put your own folder's path between the quotes; `mstn-db` is the Pi's hostname:

   ```powershell
   scp -r "C:\Users\you\Music\Hourly Music" mstn@mstn-db.local:/srv/music/
   ```

   It asks for your Pi password (unless your PC has a key for it), then lists each file as
   it's copied, with a progress bar. 72 tracks take a minute or two.

   The folder arrives as `/srv/music/Hourly Music/`. That's fine: the music service also
   reads folders inside `/srv/music`.

## Step 4: Check the Music

On the Pi:

```bash
/opt/measure-station-db/.venv/bin/python -m measure_station_db.music check
```

This plays nothing. It lists how many tracks it found for each hour and weather:

```text
Music folder: /srv/music
Chances: Sunny 60%, Rain 25%, Snow 15%

Hour       Sunny    Rain    Snow
12 am          1       1       1
01 am          1       1       1
...
11 pm          1       1       1

72 track(s); 24 of 24 hours will play.
```

- A `-` means there's no track for that hour and weather. A whole row of `-` says
  `(skipped: no tracks)`.
- If some files are listed under **`Ignored, as the names aren't like '03 pm (Rain).mp3'`**,
  rename them to that pattern: the hour as 1 or 2 digits (`03` or `3`), a space, `am` or
  `pm`, a space, then the weather in brackets.
- If a weather is listed under **`Never picked`**, its name in the files doesn't match one
  in the chances (Step 5), e.g. `Sun` instead of `Sunny`. Capital letters don't matter.

**Hear one track** through the same route the service uses (as the service's own user, so
this also checks it's allowed to use the sound). Type the start of the path and press
**Tab** to complete it:

```bash
sudo -u measure-station-db mpg123 -o alsa -a hdmi:CARD=vc4hdmi0,DEV=0 "/srv/music/Hourly Music/01 am (Sunny).mp3"
```

The track plays on the TV, and the terminal shows its name and length. Press **Ctrl+C** to
stop it.

## Step 5: Choose the Weather Chances

Skip this step to keep 60% Sunny, 25% Rain and 15% Snow.

```bash
sudo nano /opt/measure-station-db/src/measure_station_db/sysconfig.py
```

Press **Ctrl+W**, type `MUSIC_WEATHER_CHANCES` and press Enter. You'll see:

```python
MUSIC_WEATHER_CHANCES: dict[str, float] = {"Sunny": 60, "Rain": 25, "Snow": 15}
```

Change the numbers. Each is a share of the total, so they're percentages when they add up
to 100 (they don't have to). `0` means that weather is never played. Keep the quotes,
colons and commas. Save with **Ctrl+O**, Enter, and exit with **Ctrl+X**.

## Step 6: Switch It On

1. In the same settings file, press **Ctrl+W**, type `MUSIC_ENABLED` and press Enter.
   Change `False` to `True`:

   ```python
   MUSIC_ENABLED: bool = True
   ```

   Save and exit.

2. Start the music:

   ```bash
   sudo systemctl restart measure-station-db-music
   ```

   The TV starts playing the `12 am` track within a few seconds.

3. Check its log:

   ```bash
   sudo journalctl -u measure-station-db-music -n 10
   ```

   You should see:

   ```text
   INFO [measure_station_db.music] Playing music from /srv/music on hdmi:CARD=vc4hdmi0,DEV=0
   INFO [measure_station_db.music] Found 72 track(s) for 24 hour(s) in /srv/music
   INFO [measure_station_db.music] Playing Hourly Music/12 am (Sunny).mp3
   ```

   Each new track adds a `Playing …` line.

From now on it starts by itself whenever the Pi starts, and carries on where it was.

## Adding or Changing Music

Copy, rename or delete files in `/srv/music` at any time (with `scp` as in
[Step 3](#step-3-copy-the-music-to-the-pi), or on the Pi). The service reads the folder
again before every track, so there's nothing to restart. Run the check in
[Step 4](#step-4-check-the-music) afterwards to see what it found.

## Everyday Tasks

| Task | Command |
|---|---|
| Is it playing? | `systemctl status measure-station-db-music` (press **q** to quit) |
| What's playing, and its log | `sudo journalctl -u measure-station-db-music -n 20` |
| Silence it until the Pi next starts | `sudo systemctl stop measure-station-db-music` |
| Start it again | `sudo systemctl start measure-station-db-music` |
| Restart it (after changing a setting) | `sudo systemctl restart measure-station-db-music` |
| Check the music folder | `/opt/measure-station-db/.venv/bin/python -m measure_station_db.music check` |
| Switch it off for good | Set `MUSIC_ENABLED` to `False` (Step 6), then restart it |

## Settings

In `/opt/measure-station-db/src/measure_station_db/sysconfig.py`. Restart the service
after a change.

| Setting | Default | Meaning |
|---|---|---|
| `MUSIC_ENABLED` | `False` | `True` to play the music |
| `MUSIC_DIR` | `"/srv/music"` | The music folder. Folders inside it count too |
| `MUSIC_WEATHER_CHANCES` | `{"Sunny": 60, "Rain": 25, "Snow": 15}` | Each weather's chance for the next hour ([Step 5](#step-5-choose-the-weather-chances)). Other weathers work too, e.g. `"Windy": 10`, if the files use that name |
| `MUSIC_AUDIO_DEVICE` | `"hdmi:CARD=vc4hdmi0,DEV=0"` | Where the sound goes: the TV on HDMI 0. `"hdmi:CARD=vc4hdmi1,DEV=0"` for HDMI 1 |
| `MUSIC_BOOKMARK_FILE` | `"/var/lib/measure-station-db-music/bookmark"` | Notes the track playing, so a restart carries on from it. Delete the file to start again from `12 am` |
| `MUSIC_RETRY_S` | `10` | Seconds to wait before trying again when a track can't be played |

## Troubleshooting

| Problem or message | What to do |
|---|---|
| `aplay -l` shows no `vc4hdmi0` | The Pi doesn't see the TV's sound. Check the TV is on **HDMI 0** (next to the power socket), and that it was on when the Pi started; then `sudo reboot` with the TV on. If it shows `vc4hdmi1` instead, the TV is on HDMI 1: set `MUSIC_AUDIO_DEVICE` to `"hdmi:CARD=vc4hdmi1,DEV=0"`, and use that in the Step 2 and 4 commands |
| `speaker-test` runs, but there's no sound | Check the TV isn't muted, and its sound output is set to its own speakers (not a soundbar that's off). Try the volume higher |
| `Device or resource busy` | Another program is using the TV's sound, usually the music service itself. Stop it (`sudo systemctl stop measure-station-db-music`), try again, then start it |
| `Permission denied` from the Step 4 `mpg123` command, or in the log | The service user isn't allowed to use the sound yet. Run `sudo bash /opt/measure-station-db/deploy/install.sh` again, then restart the service |
| `No music to play in /srv/music` in the log | No file names fit the pattern. Run the check in [Step 4](#step-4-check-the-music) |
| `Can't read the music folder` in the log | `/srv/music` is missing: Step 3 |
| `Can't play … (…); trying again every 10 s` in the log | The track couldn't be played; the message in brackets says why. If the TV was off or in standby, this is expected: it carries on by itself (`Playing again` in the log). If it repeats with the TV on, send the developer the log. A broken MP3 is skipped after 3 tries |
| `Configuration error: …` in the log | A mistake in the music settings; the message says which. Fix it, then restart the service |
| The music starts from `12 am` after a restart | The bookmark couldn't be read or written (the log says so), or the track playing last time was renamed or deleted |
