# Saving Dashboards in the Repo

You design dashboards in Grafana, in the browser. Grafana keeps them in its own small
database on the Pi, which the [nightly backup](backups.md) doesn't include (that copies
the readings). This guide keeps a copy of every dashboard and playlist in this repo, in
`deploy/grafana/`, so they're kept in git with the rest of the project: a backup, and a
history of every change.

**Grafana holds the master copy.** Carry on designing in the browser. After a design
session, one command copies everything into the repo, and you commit it as usual. Another
command puts the copies back into Grafana, e.g. after rebuilding the Pi.

Contents:

- [What's Saved](#whats-saved)
- [Saving](#saving)
- [Seeing What Changed](#seeing-what-changed)
- [Putting the Copies Back](#putting-the-copies-back)
- [Going Back to an Older Version](#going-back-to-an-older-version)
- [Troubleshooting](#troubleshooting)

## What's Saved

| Saved | Not saved |
|---|---|
| Every dashboard: its panels, queries (SQL), options, layout, time range and refresh | The home dashboard setting (☰, **Administration**, **General**, **Default preferences**) |
| Every playlist (the TV's rotation; see [Dashboards on the TV](tv-dashboards.md)) | The database connection: the setup guide's file creates it ([Step 8](setting-up-the-server-pi.md#step-8-install-grafana)) |
| The folders the dashboards are in | Logins and passwords, and Grafana's own settings |

Dashboards refer to the database by a fixed name (`measure-station-db`) that the setup
guide gives the connection, so the copies work on a rebuilt Pi without changes.

The files, inside `deploy/grafana/`:

| File | What it holds |
|---|---|
| `dashboards/<folder>/<title>.json` | One dashboard. Its folder and name come from Grafana's, in lower case with `-` for spaces: **CO2 & Temperature** in the **TV** folder is `dashboards/tv/co2-temperature.json`. Dashboards in no folder are in `dashboards/general/` |
| `playlists/<name>.json` | One playlist: its dashboards, in order, and how long each is shown |
| `folders.json` | Each folder's name and ID, so the same folders can be made again |

Each file is Grafana's own description of the dashboard (JSON), the same as Grafana's
**Export** gives. A panel's SQL is in its `rawSql` line.

## Saving

Do this after each design session, **on your Windows computer, not on the Pi**: the
copies belong in the repo you commit from. It reaches Grafana over your network. In VS
Code, open the repo's folder, then **Terminal**, **New Terminal**; the prompt should end
with `measure-station-db-python>`. Run (no `sudo`):

```powershell
.venv\Scripts\python -m measure_station_db.dashboards save
```

It needs no login: anyone on your network may view dashboards. You should see something
like:

```text
Saved 4 dashboards, 1 playlist and 1 folder from http://mstn-db.local:3000 into C:\...\deploy\grafana
  new: dashboards/tv/weather.json
  changed: dashboards/tv/co2.json
Commit these files to keep this version.
```

- **new**: a dashboard made since the last save.
- **changed**: one you changed since the last save.
- **removed (no longer in Grafana)**: one you deleted in Grafana. Its file is deleted
  too. Until you commit, you can still change your mind: git keeps every committed version.
- **Nothing has changed since the last save** means just that. Opening and saving a
  dashboard without changing it doesn't count as a change.

Then commit the files in `deploy/grafana/` as usual.

**If your server isn't called `mstn-db`,** the command can't find Grafana. Change
`GRAFANA_URL` in `src/measure_station_db/sysconfig.py` to your server's name plus
`.local:3000` (or its IP address plus `:3000`).

## Seeing What Changed

Before committing, VS Code's **Source Control** panel shows each changed file. Click one
to see the old and new side by side, e.g. a query's SQL in its `rawSql` line, or a
threshold's `value`. A panel's position and size are its `gridPos`.

## Putting the Copies Back

Use this after rebuilding the Pi (once Grafana is set up again), or to undo changes made
in the browser since the last save. Run it the same way as `save`: on your computer, in
VS Code's terminal in the repo's folder.

```powershell
.venv\Scripts\python -m measure_station_db.dashboards restore
```

It says what it's about to do, then asks for Grafana's `admin` password (the one from the
setup guide's [Step 8](setting-up-the-server-pi.md#step-8-install-grafana)). Nothing is
shown as you type it; press **Enter** after it, or **Ctrl+C** to cancel without changing
anything. Then:

- **folders** that don't exist yet are created;
- **dashboards and playlists** with the same ID as a copy are **replaced** by the copy,
  and missing ones are created;
- **anything else** in Grafana (e.g. a dashboard you made since the last save) is left
  alone.

You should see `Restored 4 dashboards, 1 playlist and 1 folder (1 folder created).`

**Afterwards, by hand:** set the home dashboard again, if you use one (☰,
**Administration**, **General**, **Default preferences**, **Home Dashboard**). The TV
needs nothing: the playlist keeps its ID, so the address in `~/kiosk.sh` still works.

**To undo one dashboard's recent changes,** Grafana's own history is quicker: open the
dashboard, click **Edit**, then **Settings**, then **Versions**, and restore an earlier
version there.

## Going Back to an Older Version

git has every version you committed. To put an older version of one dashboard back into
Grafana:

1. Find the commit: `git log --oneline -- deploy/grafana` lists the commits that changed
   the copies, newest first, e.g. `1a2b3c4 CO2 dashboard: bigger numbers`.
2. Take that commit's version of the file (here, the one just before it, `1a2b3c4~1`):

   ```powershell
   git restore --source 1a2b3c4~1 -- deploy/grafana/dashboards/tv/co2.json
   ```

3. Run `restore` (above). It replaces that dashboard in Grafana with the older version.
4. Commit the file: the older version is now the current one.

## Troubleshooting

| Problem | What to do |
|---|---|
| `.venvScriptspython: command not found` | You ran it on the Pi (over `ssh`). Run it on your computer instead, in VS Code's terminal: see [Saving](#saving) |
| `Can't reach Grafana at http://mstn-db.local:3000` | Your computer can't find the server by name, or Grafana isn't running. Open the same address in a browser. If that fails too, set `GRAFANA_URL` in `sysconfig.py` to the server's IP address (e.g. `http://192.168.1.20:3000`), and check Grafana on the Pi: `systemctl status grafana-server` |
| `Grafana asked for a login` | Viewing without signing in is switched off. See [Viewing the Data in Grafana](viewing-data-in-grafana.md#troubleshooting), the row about a login form |
| `Grafana refused the login for admin` | The password was wrong. Run `restore` again. If you've forgotten it, reset it as in the setup guide's Step 8 |
| `... isn't in folders.json` | A dashboard file is in a folder that `save` didn't make (e.g. moved by hand). Move it back, or into `dashboards/general/` |
| A dashboard says **Datasource not found** after a restore | The database connection on the new Pi doesn't have the ID `measure-station-db`. Check the setup guide's Step 8 file, then `sudo systemctl restart grafana-server` |
| An error with `406` or `v2` in it | That dashboard is in Grafana's newer dashboard format ("schema v2"), which this command doesn't read yet (your first dashboards were in the classic format, which it does read). The command needs updating: note which dashboard it was |
