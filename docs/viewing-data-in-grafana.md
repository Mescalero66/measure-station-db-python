# Viewing the Data in Grafana

This guide builds a first Grafana dashboard, called **Live**, that shows the readings
arriving from a station: when each sensor last reported, its latest values, and charts
that update by themselves. Then it puts the dashboard on the TV and on phones. It takes
about 30 minutes.

You need the ingest service running and readings arriving (see
[The Ingest Service](ingest-service.md)), and Grafana's `admin` password from the setup
guide's [Step 8](setting-up-the-server-pi.md#step-8-install-grafana).

This is a hand-made starting point. Later build phases add finished dashboards, with
sensor pickers and weekly and monthly averages, kept as files in this repo. The Live
dashboard stays until you delete it.

**Grafana's menus change a little between versions.** If a button here has a slightly
different name, look for something similar in the same place.

Contents:

- [Grafana in Five Words](#grafana-in-five-words)
- [Step 1: Sign In](#step-1-sign-in)
- [Step 2: See What's Arriving](#step-2-see-whats-arriving)
- [Step 3: Create the Dashboard, With a "Last Heard" Table](#step-3-create-the-dashboard-with-a-last-heard-table)
- [Step 4: Add the Latest Values](#step-4-add-the-latest-values)
- [Step 5: Add a CO2 Chart](#step-5-add-a-co2-chart)
- [Step 6: Compare Temperatures From Every Sensor](#step-6-compare-temperatures-from-every-sensor)
- [Step 7: Make It Update by Itself, and Save](#step-7-make-it-update-by-itself-and-save)
- [Step 8: Show It on the TV and on Phones](#step-8-show-it-on-the-tv-and-on-phones)
- [Charting Any Other Value](#charting-any-other-value)
- [Troubleshooting](#troubleshooting)

## Grafana in Five Words

| Word | Meaning |
|---|---|
| **Dashboard** | A page of charts and tables, like a page in a scrapbook |
| **Panel** | One chart or table on a dashboard |
| **Query** | The question a panel asks the database, written in SQL. This guide gives each one in full: copy it exactly |
| **Time range** | The period the charts show, e.g. "Last 6 hours". Picked at the top right, it applies to the whole dashboard |
| **Refresh** | How often the dashboard asks the database again, e.g. every 30 seconds |

**Where the numbers come from.** Panels read the `reading_value` table: one row per value,
such as `co2_ppm` from the `co2` sensor. Each row has the station, the source (the
sensor), the section (`values` for measurements, `meta` for extra information such as
`warmup`), the key (the value's name), the time (`ts`), and the value itself: a number in
`num_value`, or text in `text_value`. See
[What's Stored](ingest-service.md#whats-stored).

## Step 1: Sign In

1. On your computer, open a web browser and go to `http://192.168.1.20:3000` (your
   server's address, then `:3000`).
2. Click **Sign in** (top right) and log in as `admin` with the Grafana admin password.

Anyone can *view* dashboards without signing in, but only `admin` can create or change
them.

## Step 2: See What's Arriving

Grafana's **Explore** page asks the database a question without building a dashboard.
Use it to check that Grafana sees the data, and to learn the names of your values.

1. Open the menu (☰, top left) and click **Explore**.
2. At the top left, the data source should be **Measure Station**. If not, choose it.
3. The query area shows **Builder** and **Code** (at its right-hand side). Click **Code**.
4. Paste this into the query box:

   ```sql
   -- Every value that arrived in the last hour, with how many readings and the newest.
   SELECT station, source, section, key,
          count(*) AS readings,
          max(ts) AS latest
   FROM reading_value
   WHERE ts > now() - INTERVAL '1 hour'
   GROUP BY station, source, section, key
   ORDER BY station, source, section, key
   ```

5. Set **Format** (below the query box) to **Table**, then click **Run query**.

You should see a table with a row for each value: e.g. `mstn-2b`, `co2`, `values`,
`co2_ppm`, about 60 readings. Keep this tab open: the `source` and `key` columns are the
names the charts below use.

If the table is empty, readings haven't arrived in the last hour: check the
[ingest service's log](ingest-service.md#everyday-tasks).

## Step 3: Create the Dashboard, With a "Last Heard" Table

The first panel shows when each sensor last reported. If one stops, you see it at once.

1. Open the menu (☰) and click **Dashboards**. Click **New** (top right), then **New
   dashboard**.
2. Click **Add visualization**. If Grafana asks for a data source, choose **Measure
   Station**.
3. The panel editor opens: a preview at the top, the query at the bottom, and the panel's
   options on the right. In the query area, click **Code**, and paste:

   ```sql
   -- When each sensor last sent a reading, and how many seconds ago that was.
   SELECT station, source,
          max(ts) AS last_reading,
          round(extract(epoch FROM now() - max(ts))) AS seconds_ago
   FROM reading
   WHERE ts > now() - INTERVAL '1 day'
   GROUP BY station, source
   ORDER BY station, source
   ```

4. Set **Format** to **Table**, and click **Run query**.
5. At the top of the options on the right, the visualization type says **Time series**.
   Click it and choose **Table**.
6. In the options on the right:
   - **Title** (under *Panel options*): `Last heard`.
   - **Cell type** (under *Cell options*): **Colored background**.
   - **Thresholds** (near the bottom): change the red step's number from `80` to `300`.

   Now `seconds_ago` is green while a sensor is keeping up, and turns red once it has
   been quiet for 5 minutes. (Most sensors report every 60 seconds; the gas sensors every
   150.)
7. Click **Back to dashboard** (top right).

A sensor that has been silent for more than a day drops off this list.

## Step 4: Add the Latest Values

This table shows the newest value of everything, including the `meta` information such as
`warmup`.

1. Click **Add** (at the top), then **Visualization**.
2. Click **Code**, and paste:

   ```sql
   -- The newest value of everything each station sent in the last 15 minutes.
   SELECT DISTINCT ON (station, source, section, key)
          station, source, section, key,
          coalesce(num_value::text, text_value) AS value,
          ts AS time
   FROM reading_value
   WHERE ts > now() - INTERVAL '15 minutes'
   ORDER BY station, source, section, key, ts DESC
   ```

3. Set **Format** to **Table**, and click **Run query**.
4. Change the visualization type to **Table**, and set the **Title** to `Latest values`.
5. Click **Back to dashboard**.

True/false values show as `1` or `0`. A table this long is easier to read if the panel is
made taller: drag its bottom-right corner.

## Step 5: Add a CO2 Chart

1. Click **Add**, then **Visualization**.
2. Click **Code**, and paste the query below. **Change `mstn-2b` to your station's ID**
   (as shown in Step 2):

   ```sql
   -- CO2 from one station, over the time range chosen at the top of the dashboard.
   SELECT ts AS time, num_value AS co2
   FROM reading_value
   WHERE $__timeFilter(ts)
     AND station = 'mstn-2b'
     AND source = 'co2' AND section = 'values' AND key = 'co2_ppm'
     AND ts_src <> 'estimated'
   ORDER BY ts
   ```

3. Leave **Format** as **Time series**, and click **Run query**. A line appears.
4. In the options on the right:
   - **Title**: `CO2`.
   - **Unit** (under *Standard options*): click the box, type `ppm`, and choose
     **parts-per-million (ppm)**.
5. Click **Back to dashboard**.

What the special lines mean:

- **`$__timeFilter(ts)`** is a placeholder. Grafana replaces it with "between the start
  and end of the time range chosen at the top", so the chart follows that setting.
- **`ts_src <> 'estimated'`** leaves out readings whose time the station had to guess
  (after it started without a network). They can be far out, so charts skip them.

## Step 6: Compare Temperatures From Every Sensor

Several of the station's sensors measure temperature. This chart draws one line per
sensor, so you can see how closely they agree.

1. Click **Add**, then **Visualization**.
2. Click **Code**, and paste (again with your station's ID):

   ```sql
   -- Temperature from every sensor on one station: one line per sensor.
   SELECT ts AS time, source AS metric, num_value
   FROM reading_value
   WHERE $__timeFilter(ts)
     AND station = 'mstn-2b'
     AND section = 'values' AND key = 'temperature_c'
     AND ts_src <> 'estimated'
   ORDER BY ts
   ```

   A column called **`metric`** tells Grafana to draw a separate line for each of its
   values, named after it (`co2`, `humidity`, `particulates` and so on). Keep exactly
   these three columns, or the lines get longer names.

3. Click **Run query**.
4. In the options on the right:
   - **Title**: `Temperature`.
   - **Unit**: type `Celsius` and choose **Celsius (°C)**.
   - **Connect null values** (under *Graph styles*): choose **Threshold**, and type
     `5m`.

   Why the last one: each sensor reports at a slightly different second, so at any one
   moment only one line has a value, and Grafana would draw separate dots. "Threshold
   5m" joins the dots when they're less than 5 minutes apart, so a real gap (a sensor
   that stopped) still shows as a gap.

5. Click **Back to dashboard**.

## Step 7: Make It Update by Itself, and Save

1. **Time range:** at the top right, click the clock (e.g. *Last 6 hours*) and choose
   **Last 6 hours**.
2. **Refresh:** next to the refresh button (two circling arrows) is a small arrow. Click
   it and choose **30s**. Stations send every 60 seconds, so refreshing more often
   would only make the Pi work harder.
3. **Arrange:** drag panels by their title to move them, and by their bottom-right corner
   to resize them. A good layout: the two tables side by side at the top, the charts
   below at full width.
4. **Save:** click **Save dashboard** (top right). Set the **Title** to `Live`. If it
   offers to save the current time range or refresh as the dashboard's defaults, tick
   them. Click **Save**.
5. Click **Exit edit** (top right), if it's there.

The dashboard now updates every 30 seconds. To change it later, open it and click
**Edit** (top right).

## Step 8: Show It on the TV and on Phones

**The TV doesn't need a login, or a keyboard.** Anyone on your network may view dashboards
without signing in (the setup guide's Grafana settings allow it), and the TV does just
that. Until you set a home dashboard, it shows Grafana's "Welcome to Grafana" page, which
has a **Sign in** button in the corner but doesn't ask you to use it. Setting Live as the
home dashboard makes the TV show Live instead. It's also what phones see first. (More in
the setup guide's
[What the TV Shows](setting-up-the-server-pi.md#what-the-tv-shows-no-login-needed).)

**Make Live the home dashboard:**

1. Open the menu (☰), then **Administration**, then **General**, then **Default
   preferences**.
2. Set **Home Dashboard** to **Live**, and click **Save**.

**Reload the TV.** Connect to the Pi with `ssh` and run:

```bash
pkill chromium
```

The TV goes blank, and within about 10 seconds the browser starts again and shows Live.
(The kiosk script restarts the browser whenever it closes; see the setup guide's
[Step 9](setting-up-the-server-pi.md#step-9-set-up-the-tv).)

**On a phone** on your home Wi-Fi, open `http://192.168.1.20:3000`. No login is needed.
Add `/?kiosk` to the address to hide Grafana's menus, and use the browser's "Add to Home
screen" to keep it like an app.

**Times are shown in each screen's own time zone:** your phone's, and on the TV, the
Pi's (set in the setup guide's Step 2). The database stores them in UTC.

## Charting Any Other Value

To chart another value, copy the CO2 chart and change it:

1. Click **Edit** (top right), then on the CO2 panel, click its menu (⋮, top right of the
   panel), then **More**, then **Duplicate**.
2. On the copy, click its menu (⋮), then **Edit**.
3. In the query, change the `source` and `key` to the value's names from Step 2, and the
   `AS co2` to a short name for the line. Click **Run query**.
4. Change the **Title** and **Unit**, click **Back to dashboard**, and save.

Some values to start with (yours may differ: check Step 2's list):

| To chart | `source` | `key` | Unit |
|---|---|---|---|
| Humidity | `humidity` | `humidity_pct` | Humidity (%H) |
| Air pressure | `barometer` | `pressure_hpa` | Hectopascals (hPa) |
| Light | `light` | `illuminance_lux` | Lux (lx) |
| PM2.5 (fine dust) | `particulates` | `pm25_ugm3` | µg/m³ |
| Air quality index | `air-quality` | `aqi` | (none) |
| Outbox backlog (readings waiting on the station) | `health` | `outbox_backlog` | (none) |

To chart one value from every sensor that measures it (like Step 6), copy the
temperature chart instead and change its `key`, e.g. to `humidity_pct`.

## Troubleshooting

| Problem | What to do |
|---|---|
| A panel says **No data** | Check the station ID in the query matches Step 2's list exactly, including capitals. Check the time range at the top covers a time when readings arrived |
| **Data does not have a time field** | A table's query has its **Format** set to *Time series*. Set it to **Table** |
| **db query error** with **does not exist** | A typing mistake in the query. Paste it again, changing only the station ID |
| The chart shows dots, not lines | Set **Connect null values** as in [Step 6](#step-6-compare-temperatures-from-every-sensor) |
| **Save dashboard** isn't there | You aren't signed in. Click **Sign in** (top right) and log in as `admin` |
| The TV still shows the old page | Run `pkill chromium` over `ssh`, as in [Step 8](#step-8-show-it-on-the-tv-and-on-phones) |
| The TV, or a phone, shows a login form (boxes for a username and password) | Viewing without signing in isn't switched on. Check with `systemctl show grafana-server -p Environment`: it should include `GF_AUTH_ANONYMOUS_ENABLED=true`. If not, check the *Settings* file in the setup guide's [Step 8](setting-up-the-server-pi.md#step-8-install-grafana), then `sudo systemctl daemon-reload` and `sudo systemctl restart grafana-server` |
| Times are hours out | Grafana shows each screen's own time zone. On the TV that's the Pi's: change it with `sudo raspi-config`, under *Localisation Options*, then *Timezone*, then restart the Pi |
| A value you expected isn't in Step 2's list | The station isn't sending it, or it arrived over an hour ago. Check with the [message checker](checking-station-messages.md) |
