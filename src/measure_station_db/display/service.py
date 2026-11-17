"""The display service: the latest value of one series on the LED display; the button
moves to the next.

Runs as a systemd service (deploy/systemd/measure-station-db-display.service)::

    python -m measure_station_db.display

- **What it shows** is set in sysconfig.DISPLAY_ITEMS. After a press, the item's label
  shows on its own for DISPLAY_LABEL_S, then the value, with the label in front when both
  fit in 8 digits (e.g. ``co2  612``, ``t    22.4``: a dot shares its digit).
- **The value is the newest in the database,** read again every DISPLAY_REFRESH_S. One
  older than DISPLAY_STALE_S (the sensor stopped sending) shows as ``----``; a database
  problem shows ``Err``.
- **Threads:** the button is watched in its own thread (gpiod edge events, debounced),
  which hands presses to the main thread through a queue. The main thread reads the
  database and draws.
- **Faults don't stop it:** a display that stops answering (e.g. unplugged) is set up
  again once it answers. A button that can't be read is logged, and the display keeps
  showing the first item.
- **Switched off by default:** with DISPLAY_ENABLED False, it logs that and exits.
"""

from __future__ import annotations

import logging
import queue
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from measure_station_db import sysconfig
from measure_station_db.display.ht16k33 import HT16K33LED
from measure_station_db.logging_setup import configure_logging

log = logging.getLogger(__name__)

EX_CONFIG = 78  # a configuration error: the unit file doesn't restart the service
WIDTH = 8  # digits on the display
NO_VALUE = "----"
ERROR = "Err"
BUTTON_DEBOUNCE_S = 0.2  # presses closer together than this count once
_ITEM_OPTIONS = {"label", "source", "key", "decimals", "section", "station"}


class ConfigError(ValueError):
    """A problem in the display settings in sysconfig.py."""


@dataclass(frozen=True)
class Item:
    label: str
    station: str
    source: str
    key: str
    section: str = "values"
    decimals: int = 1


@dataclass(frozen=True)
class Reading:
    age_s: float
    num_value: float | None
    text_value: str | None


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def digits(text: str) -> int:
    """How many digits text takes on the display (a '.' joins the character before)."""
    return len(HT16K33LED.glyphs(text))


def parse_items(raw: list[dict[str, Any]], station: str) -> list[Item]:
    """Check DISPLAY_ITEMS and turn it into Items; raise ConfigError for any mistake."""
    if not raw:
        raise ConfigError("DISPLAY_ITEMS is empty")
    items = []
    for n, entry in enumerate(raw, 1):
        where = f"DISPLAY_ITEMS entry {n}"
        unknown = set(entry) - _ITEM_OPTIONS
        if unknown:
            raise ConfigError(f"{where}: unknown option(s) {', '.join(sorted(unknown))}")
        for option in ("label", "source", "key"):
            if not isinstance(entry.get(option), str) or not entry[option]:
                raise ConfigError(f"{where}: {option!r} must be some text")
        label = entry["label"]
        try:
            width = digits(label)
        except ValueError:
            raise ConfigError(
                f"{where}: the display can't show every character of the label {label!r}"
            ) from None
        if width > WIDTH:
            raise ConfigError(f"{where}: the label {label!r} is longer than {WIDTH} digits")
        decimals = entry.get("decimals", 1)
        if not isinstance(decimals, int) or isinstance(decimals, bool) or not 0 <= decimals <= 3:
            raise ConfigError(f"{where}: 'decimals' must be 0, 1, 2 or 3")
        section = entry.get("section", "values")
        if section not in ("values", "meta"):
            raise ConfigError(f'{where}: \'section\' must be "values" or "meta"')
        item_station = entry.get("station", station)
        if not isinstance(item_station, str) or not item_station:
            raise ConfigError(f"{where}: 'station' must be some text")
        items.append(Item(label, item_station, entry["source"], entry["key"], section, decimals))
    return items


def check_settings() -> list[Item]:
    """All the display settings, checked; raise ConfigError for any mistake."""
    if not 0 <= sysconfig.DISPLAY_BRIGHTNESS <= 15:
        raise ConfigError("DISPLAY_BRIGHTNESS must be 0-15")
    if not 0x03 <= sysconfig.DISPLAY_I2C_ADDRESS <= 0x77:
        raise ConfigError("DISPLAY_I2C_ADDRESS must be an I2C address, e.g. 0x70")
    for name in ("DISPLAY_LABEL_S", "DISPLAY_REFRESH_S", "DISPLAY_STALE_S"):
        if getattr(sysconfig, name) <= 0:
            raise ConfigError(f"{name} must be more than 0")
    return parse_items(sysconfig.DISPLAY_ITEMS, sysconfig.DISPLAY_STATION)


# --------------------------------------------------------------------------- #
# What to show
# --------------------------------------------------------------------------- #


def value_text(reading: Reading | None, decimals: int, stale_s: float) -> str:
    """The value as the display can show it, in at most WIDTH digits."""
    if reading is None or reading.age_s > stale_s:
        return NO_VALUE
    if reading.num_value is not None:
        # Fewer decimals if the number is too wide; a number too wide even then is an error.
        for d in range(decimals, -1, -1):
            text = f"{reading.num_value:.{d}f}"
            if digits(text) <= WIDTH:
                return text
        return ERROR
    if reading.text_value is not None:
        # Text, e.g. "warmup": characters the display can't show become "-".
        shown = "".join(c if c.lower() in HT16K33LED.CHARSET else "-" for c in reading.text_value)
        return shown[:WIDTH]
    return NO_VALUE


def compose(label: str, value: str) -> str:
    """The label on the left and the value on the right, when both fit with a space
    between; otherwise the value alone, on the right."""
    gap = WIDTH - digits(label) - digits(value)
    if gap >= 1:
        return label + " " * gap + value
    return " " * max(0, WIDTH - digits(value)) + value


class Screen:
    """Which item is shown, and the text for it at a given moment.

    Not thread-safe: the main thread calls press() and text().
    """

    def __init__(
        self,
        items: list[Item],
        fetch: Callable[[Item], Reading | str | None],
        *,
        label_s: float,
        refresh_s: float,
        stale_s: float,
        now: float,
    ) -> None:
        self.items = items
        self.fetch = fetch
        self.label_s = label_s
        self.refresh_s = refresh_s
        self.stale_s = stale_s
        self.index = 0
        self.label_until = now + label_s  # show the first item's label at start-up too
        self._value: str | None = None
        self._fetched_at = 0.0

    @property
    def item(self) -> Item:
        return self.items[self.index]

    def press(self, now: float) -> None:
        self.index = (self.index + 1) % len(self.items)
        self.label_until = now + self.label_s
        self._value = None

    def text(self, now: float) -> str:
        if now < self.label_until:
            return self.item.label
        if self._value is None or now - self._fetched_at >= self.refresh_s:
            reading = self.fetch(self.item)
            if reading == ERROR:
                self._value = ERROR
            else:
                assert not isinstance(reading, str)
                self._value = value_text(reading, self.item.decimals, self.stale_s)
            self._fetched_at = now
        return compose(self.item.label, self._value)


class Debouncer:
    """Counts a press only if the previous one was at least BUTTON_DEBOUNCE_S before."""

    def __init__(self, gap_s: float = BUTTON_DEBOUNCE_S) -> None:
        self.gap_s = gap_s
        self._last: float | None = None

    def accept(self, t: float) -> bool:
        if self._last is not None and t - self._last < self.gap_s:
            return False
        self._last = t
        return True


# --------------------------------------------------------------------------- #
# The database
# --------------------------------------------------------------------------- #

_LATEST = """
    SELECT extract(epoch FROM now() - ts)::float8, num_value, text_value
    FROM reading_value
    WHERE station = %s AND source = %s AND section = %s AND key = %s
      AND ts > now() - INTERVAL '1 day' AND ts_src <> 'estimated'
    ORDER BY ts DESC
    LIMIT 1
"""


class Latest:
    """Reads the newest value of an item, over one connection that's reopened after a
    failure. Returns ERROR (and logs once) while the database can't be used."""

    def __init__(self) -> None:
        self._conn: Any = None
        self._failing = False

    def __call__(self, item: Item) -> Reading | str | None:
        import psycopg

        try:
            if self._conn is None or self._conn.closed:
                self._conn = psycopg.connect(
                    host=sysconfig.DATABASE_HOST,
                    port=sysconfig.DATABASE_PORT,
                    dbname=sysconfig.DATABASE_NAME,
                    user=sysconfig.DATABASE_USER,
                    password=sysconfig.DATABASE_PASSWORD,
                    autocommit=True,
                    connect_timeout=10,
                    application_name="measure-station-db-display",
                )
            row = self._conn.execute(
                _LATEST, (item.station, item.source, item.section, item.key)
            ).fetchone()
        except psycopg.Error as exc:
            if not self._failing:
                log.error("Can't read the database (%s); showing %r until it's back", exc, ERROR)
            self._failing = True
            if self._conn is not None:
                self._conn.close()
            return ERROR
        if self._failing:
            log.info("The database is back")
            self._failing = False
        return Reading(*row) if row else None


# --------------------------------------------------------------------------- #
# The hardware
# --------------------------------------------------------------------------- #


def watch_button(presses: queue.Queue[float], stop: threading.Event) -> None:
    """Put the time of each press on presses, until stop is set. Runs in its own thread."""
    try:
        import gpiod
        from gpiod.line import Bias, Direction, Edge

        high = sysconfig.DISPLAY_BUTTON_PRESSED_HIGH
        settings = gpiod.LineSettings(
            direction=Direction.INPUT,
            edge_detection=Edge.RISING if high else Edge.FALLING,
            # Holds the input at "not pressed" if the button's signal wire is loose.
            bias=Bias.PULL_DOWN if high else Bias.PULL_UP,
            debounce_period=timedelta(milliseconds=20),
        )
        line = sysconfig.DISPLAY_BUTTON_GPIO
        with gpiod.request_lines(
            sysconfig.DISPLAY_BUTTON_GPIO_CHIP,
            consumer="measure-station-db-display",
            config={line: settings},
        ) as request:
            log.info("Watching the button on GPIO %d", line)
            debounce = Debouncer()
            while not stop.is_set():
                if request.wait_edge_events(timedelta(seconds=0.5)):
                    for event in request.read_edge_events():
                        t = event.timestamp_ns / 1e9
                        if debounce.accept(t):
                            presses.put(t)
    except Exception as exc:  # ImportError, OSError, gpiod's own errors
        log.error(
            "Can't read the button on GPIO %d (%s); the display stays on the first value",
            sysconfig.DISPLAY_BUTTON_GPIO,
            exc,
        )


class Panel:
    """The display, set up again whenever it stops answering."""

    def __init__(self, bus: Any) -> None:
        self.led = HT16K33LED(bus, sysconfig.DISPLAY_I2C_ADDRESS)
        if sysconfig.DISPLAY_ROTATED:
            self.led.rotate()
        self.ready = False
        self.shown: str | None = None

    def show(self, text: str) -> None:
        if self.ready and text == self.shown:
            return
        try:
            if not self.ready:
                self.led.begin()
                self.led.set_brightness(sysconfig.DISPLAY_BRIGHTNESS)
                if self.shown is not None:
                    log.info("The display is answering again")
                self.ready = True
            self.led.set_string(text, "l")
            self.shown = text
        except OSError as exc:
            if self.ready or self.shown is None:
                log.error("The display isn't answering (%s); retrying", exc)
            self.ready = False
            self.shown = text

    def off(self) -> None:
        if self.ready:
            try:
                self.led.clear()
                self.led.draw()
                self.led.power_off()
            except OSError:
                pass


def run() -> int:
    configure_logging(sysconfig.LOG_LEVEL)
    if not sysconfig.DISPLAY_ENABLED:
        log.info("The LED display is switched off (DISPLAY_ENABLED in sysconfig.py); stopping")
        return 0
    try:
        items = check_settings()
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return EX_CONFIG
    if not sysconfig.DATABASE_PASSWORD:
        log.error(
            "Configuration error: MEASURE_STATION_DB_DATABASE_PASSWORD not set "
            "(in /etc/measure-station-db/env)"
        )
        return EX_CONFIG

    from smbus2 import SMBus

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    presses: queue.Queue[float] = queue.Queue()
    threading.Thread(target=watch_button, args=(presses, stop), name="button", daemon=True).start()

    screen = Screen(
        items,
        Latest(),
        label_s=sysconfig.DISPLAY_LABEL_S,
        refresh_s=sysconfig.DISPLAY_REFRESH_S,
        stale_s=sysconfig.DISPLAY_STALE_S,
        now=time.monotonic(),
    )
    log.info(
        "Showing %d value(s) from %s on the display at I2C bus %d, address 0x%02x",
        len(items),
        sysconfig.DISPLAY_STATION,
        sysconfig.DISPLAY_I2C_BUS,
        sysconfig.DISPLAY_I2C_ADDRESS,
    )
    with SMBus(sysconfig.DISPLAY_I2C_BUS) as bus:
        panel = Panel(bus)
        try:
            while not stop.is_set():
                panel.show(screen.text(time.monotonic()))
                try:
                    presses.get(timeout=0.25)
                except queue.Empty:
                    continue
                screen.press(time.monotonic())
                log.debug("Button: now showing %s", screen.item.label)
        except KeyboardInterrupt:
            pass
        log.info("Stopping")
        stop.set()
        panel.off()
    return 0


# --------------------------------------------------------------------------- #
# The wiring check
# --------------------------------------------------------------------------- #


def wiring_test(wait_s: float = 15) -> int:
    """Light every segment, read the display's memory back, then count button presses.
    Needs no database."""
    from smbus2 import SMBus

    bus_number, address = sysconfig.DISPLAY_I2C_BUS, sysconfig.DISPLAY_I2C_ADDRESS
    print(f"Display: I2C bus {bus_number}, address 0x{address:02x}")
    with SMBus(sysconfig.DISPLAY_I2C_BUS) as bus:
        led = HT16K33LED(bus, sysconfig.DISPLAY_I2C_ADDRESS)
        try:
            led.begin()
        except OSError as exc:
            print(f"The display isn't answering: {exc}. Check the wiring (docs/led-display.md).")
            return 1
        led.set_brightness(sysconfig.DISPLAY_BRIGHTNESS)
        led.set_string("8.8.8.8.8.8.8.8.")
        ok = led.read_ram() == bytes(led.buffer)
        print("Every segment should be lit now. Memory read-back:", "PASS" if ok else "FAIL")
        time.sleep(2)

        presses: queue.Queue[float] = queue.Queue()
        stop = threading.Event()
        thread = threading.Thread(target=watch_button, args=(presses, stop), daemon=True)
        thread.start()
        print(f"Press the button a few times in the next {wait_s:.0f} seconds...")
        count = 0
        led.set_string(compose("PrESS", "0"))
        end = time.monotonic() + wait_s
        while (left := end - time.monotonic()) > 0:
            try:
                presses.get(timeout=left)
            except queue.Empty:
                break
            count += 1
            print(f"  Button pressed ({count})")
            led.set_string(compose("PrESS", str(count)))
        stop.set()
        thread.join(timeout=2)
        print(f"Button presses counted: {count}")
        led.clear()
        led.draw()
        led.power_off()
    return 0 if ok and count else 1


def main() -> None:
    args = sys.argv[1:]
    if args == ["test"]:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        sys.exit(wiring_test())
    if args:
        sys.exit("Usage: python -m measure_station_db.display [test]")
    sys.exit(run())
