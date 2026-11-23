# Dashboards on the TV

The TV shows several dashboards, one after another, changing every minute or so. Grafana
does this itself with a **playlist**: a list of dashboards, and how long to show each.
This guide makes dashboards for the TV, puts them in a playlist, and points the TV at it.

You need a working dashboard first: [Viewing the Data in Grafana](viewing-data-in-grafana.md)
makes one and explains the words used here (panel, query, time range, refresh). Sign in as
`admin` before you start.

Contents:

- [Step 1: Make a TV Folder](#step-1-make-a-tv-folder)
- [Step 2: Design Each Dashboard for the TV](#step-2-design-each-dashboard-for-the-tv)
- [Step 3: Make the Playlist](#step-3-make-the-playlist)
- [Step 4: Try It on Your Computer](#step-4-try-it-on-your-computer)
- [Step 5: Show It on the TV](#step-5-show-it-on-the-tv)
- [Step 6: Save Copies in the Repo](#step-6-save-copies-in-the-repo)
- [Changing It Later](#changing-it-later)
- [Dashboards for Phones (Later)](#dashboards-for-phones-later)
- [Troubleshooting](#troubleshooting)

## Step 1: Make a TV Folder

Keeping the TV's dashboards in their own folder keeps them apart from the ones for phones,
and from experiments. Their copies in the repo go in a matching folder too
(`deploy/grafana/dashboards/tv/`).

1. Open the menu (☰), then **Dashboards**.
2. Click **New** (top right), then **New folder**. Name it `TV`, and click **Create**.

To move an existing dashboard into it: tick the dashboard's box in the **Dashboards**
list, click **Move**, and choose **TV**.

## Step 2: Design Each Dashboard for the TV

Make each dashboard as in [Viewing the Data in Grafana](viewing-data-in-grafana.md), and
save it in the **TV** folder (the **Folder** box when saving). Some things make a
dashboard work well on a TV across the room:

- **Fewer, bigger panels.** Four to six on a screen. For a single current value (CO2 now,
  the temperature now), use a **Stat** panel: one big number, which can change colour
  with thresholds. Charts suit trends over hours or days.
- **Everything on one screen.** Nobody can scroll the TV. The playlist's "auto fit panels"
  mode (Step 4) stretches or shrinks the panels to fill the screen exactly, so arrange
  them to fit your computer's screen and the TV will match.
- **Its own time range and refresh.** Each dashboard is shown with the time range and
  refresh it was saved with (Step 7 of the Grafana guide), e.g. "Last 24 hours" for a
  daily chart, "Last 6 hours" for live ones. Use a **30s** refresh: the stations send
  every 60 seconds.
- **Clear panel titles.** The dashboard's own title isn't shown in kiosk mode, so give
  each panel a title that says what it is, e.g. `CO2 (ppm)`.

**See it as the TV will:** open the dashboard, add `&kiosk` to the end of the address in
the address bar (`?kiosk` if the address has no `?` yet), press **Enter**, then **F11**
for full screen. Press **Esc** to get Grafana's menus back, and **F11** again to leave
full screen.

## Step 3: Make the Playlist

1. Open the menu (☰), then **Dashboards**, then **Playlists**. Click **New playlist**.
2. **Name:** `TV`.
3. **Interval:** how long each dashboard stays on screen, e.g. `1m` (one minute). Other
   examples: `30s`, `2m`.
4. Under **Add dashboards**, use **Add by title** to add each TV dashboard, in the order
   you want them shown.

   (The other choice, **Add by tag**, shows every dashboard with a chosen tag, e.g.
   `tv`, so a new dashboard joins once it has that tag. But you can't choose their
   order, so this guide adds them by title.)
5. Click **Save**.

## Step 4: Try It on Your Computer

1. On the **Playlists** page, click **Start playlist** next to **TV**.
2. Choose **Kiosk mode (with auto fit panels)**, and click **Start TV**.

The dashboards take turns filling the window. Press **F11** for full screen, to see it as
the TV will. Press **Esc** to stop the playlist (and **F11** to leave full screen).

If something doesn't fit, or a number is too small to read from across the room, change
that dashboard (Step 2) and try again.

## Step 5: Show It on the TV

The TV opens whatever address is in the kiosk script (`~/kiosk.sh`, from the setup
guide's [Step 9](setting-up-the-server-pi.md#step-9-set-up-the-tv)). Change it to the
playlist's address.

1. **Get the address.** On the **Playlists** page, click the share button next to **TV**
   (not **Start playlist**). Choose the same mode as in Step 4, **Kiosk mode (with auto
   fit panels)**, and click **Copy** next to the link. Paste it somewhere to look at: it
   starts with your server's address, e.g.
   `http://mstn-db.local:3000/playlists/play/...`.

   (Don't copy the address bar while the playlist plays: it shows each dashboard's own
   address in turn, not the playlist's.)
2. **Change it to the Pi's own address.** The TV's browser runs on the Pi, so replace the
   part before `/playlists` with `http://localhost:3000`, and keep the rest exactly. For
   example (yours has a different ID, and its ending may differ a little):

   ```text
   http://localhost:3000/playlists/play/abc123?kiosk=true&autofitpanels=true
   ```

3. **Put it in the kiosk script.** Connect to the Pi with `ssh`, then:

   ```bash
   nano ~/kiosk.sh
   ```

   Change the `export URL=` line to your address, keeping the double quotes:

   ```bash
   export URL="http://localhost:3000/playlists/play/abc123?kiosk=true&autofitpanels=true"
   ```

   Save with **Ctrl+O**, **Enter**, and exit with **Ctrl+X**.
4. **Restart the Pi:**

   ```bash
   sudo reboot
   ```

   (`pkill chromium` isn't enough this time: the kiosk script that's already running
   doesn't notice that its file changed.) After a minute or so, the TV shows the first
   dashboard, and moves on to the next one after the interval.

## Step 6: Save Copies in the Repo

Save copies of the dashboards and the playlist in the repo, and commit them: see
[Saving Dashboards in the Repo](saving-dashboards.md). Do it after every design session.

## Changing It Later

| To | Do this |
|---|---|
| Change a dashboard | Edit and save it in Grafana. The TV shows the new version the next time that dashboard comes round |
| Add, remove or reorder dashboards, or change the interval | ☰, **Dashboards**, **Playlists**, click **TV**, change it, **Save**. Then reload the TV: `pkill chromium` over `ssh` (the TV goes blank, and within about 10 seconds starts the playlist again) |
| Show one dashboard only, without rotating | Change `~/kiosk.sh`'s `export URL=` line to that dashboard's address with `localhost:3000` and `?kiosk` (as in Step 5), then `sudo reboot` |

The playlist's address doesn't change when you change the playlist, so `~/kiosk.sh` only
needs changing to show something else entirely.

**Later (build phase 4),** the remote app's TV page takes over the TV, and phones choose
what it shows. It can still show the playlist.

## Dashboards for Phones (Later)

Phones get their own dashboards, in a **Phone** folder made as in Step 1. On a narrow
screen, Grafana puts the panels in a single column automatically, so they need fewer and
simpler panels rather than a different layout. Phones open Grafana at
`http://mstn-db.local:3000` (or the server's IP address), and the home dashboard is what
they see first.

## Troubleshooting

| Problem | What to do |
|---|---|
| The TV still shows the old page after changing `~/kiosk.sh` | Restart the Pi with `sudo reboot`. Check the line with `grep URL ~/kiosk.sh`: it should start `export URL="http://localhost:3000/playlists/play/` |
| The TV shows a Grafana error such as **Playlist not found** | The address was copied wrongly. Copy it again (Step 5), and keep everything after `localhost:3000` exactly |
| The TV shows a login form | Viewing without signing in isn't working: see [Viewing the Data in Grafana](viewing-data-in-grafana.md#troubleshooting) |
| A dashboard doesn't fill the screen, or needs scrolling | The address is missing auto fit. Copy it again with **Kiosk mode (with auto fit panels)** chosen |
| A dashboard never comes round | It isn't in the playlist, or it was added after the TV started the playlist. Check the playlist, then `pkill chromium` |
| Text is too small from across the room | Use fewer panels per dashboard, and **Stat** panels for single values (Step 2). In a Stat panel's options, **Text size** sets the size of the number |
