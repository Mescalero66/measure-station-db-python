"""The music service: background music through the TV's speakers, hour by hour.

Runs as a systemd service (deploy/systemd/measure-station-db-music.service)::

    python -m measure_station_db.music          the service
    python -m measure_station_db.music check    lists what it would play, without playing

- **The music** is a folder of MP3s (sysconfig.MUSIC_DIR, and folders inside it), named by
  hour and weather, e.g. ``03 pm (Rain).mp3``. Other files are ignored.
- **Order:** one track per hour, in clock order from 12 am to 11 pm, then round again.
  The hours are only names: they don't follow the real time of day.
- **Weather:** for each hour, one weather is picked at random, with the chances in
  sysconfig.MUSIC_WEATHER_CHANCES (e.g. 60 Sunny, 25 Rain, 15 Snow). If an hour has no
  file for a weather, that weather's share goes to the others. If an hour has several
  files for one weather (e.g. in different folders), one of them is picked at random.
- **Playing:** mpg123 plays each track to the TV's HDMI sound (MUSIC_AUDIO_DEVICE).
- **Resuming:** the track being played is noted in a small file (MUSIC_BOOKMARK_FILE).
  After a restart or a power cut, that track starts again from its beginning, and the
  hours carry on from there.
- **Faults don't stop it:** a track that can't be played (e.g. the TV's sound isn't
  available) is tried again every MUSIC_RETRY_S, and skipped after 3 failures in a row.
  The folder is read again before every track, so added or removed files count at once.
- **Switched off by default:** with MUSIC_ENABLED False, it logs that and exits.
"""

from __future__ import annotations

import logging
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from measure_station_db import sysconfig
from measure_station_db.logging_setup import configure_logging

log = logging.getLogger(__name__)

EX_CONFIG = 78  # a configuration error: the unit file doesn't restart the service
HOURS = 24
MAX_FAILURES = 3  # a track that fails this many times in a row is skipped
MIN_PLAY_S = 2.0  # a track that ends sooner than this didn't really play
EMPTY_WAIT_S = 60.0  # how often to look again while there's nothing to play
# "03 pm (Rain).mp3": an hour 1-12, am or pm, and a weather in brackets.
_NAME = re.compile(r"^(\d{1,2}) ?(am|pm) ?\(([^()]+)\)\.mp3$", re.IGNORECASE)
NAME_EXAMPLE = "03 pm (Rain).mp3"


class ConfigError(ValueError):
    """A problem in the music settings in sysconfig.py."""


# --------------------------------------------------------------------------- #
# The music folder
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Track:
    name: str  # its path inside the music folder, e.g. "03 pm (Rain).mp3"
    path: Path
    hour: int  # 0 is 12 am, 1 is 01 am, ... 12 is 12 pm, ... 23 is 11 pm
    weather: str  # as written in the file name, e.g. "Rain"


@dataclass(frozen=True)
class Library:
    tracks: tuple[Track, ...]
    ignored: tuple[str, ...]  # MP3s whose names don't fit the pattern


def parse_name(filename: str) -> tuple[int, str] | None:
    """The hour (0-23) and weather in a name like '03 pm (Rain).mp3', or None."""
    match = _NAME.match(filename)
    if not match:
        return None
    hour = int(match.group(1))
    if not 1 <= hour <= 12:
        return None
    pm = match.group(2).lower() == "pm"
    return hour % 12 + (12 if pm else 0), match.group(3).strip()


def hour_name(hour: int) -> str:
    """'12 am', '01 am', ... '11 pm', as in the file names."""
    return f"{hour % 12 or 12:02d} {'pm' if hour >= 12 else 'am'}"


def scan(folder: Path) -> Library:
    """Every MP3 in folder and the folders inside it. Raises OSError if it can't be read."""
    if not folder.is_dir():
        raise FileNotFoundError(f"no folder {folder}")
    tracks, ignored = [], []
    for path in sorted(folder.rglob("*.[mM][pP]3")):
        # Skip hidden files, e.g. the "._" copies a Mac leaves on USB sticks.
        if path.name.startswith(".") or not path.is_file():
            continue
        name = path.relative_to(folder).as_posix()
        parsed = parse_name(path.name)
        if parsed is None:
            ignored.append(name)
        else:
            tracks.append(Track(name, path, *parsed))
    return Library(tuple(tracks), tuple(ignored))


# --------------------------------------------------------------------------- #
# Choosing the next track
# --------------------------------------------------------------------------- #


def check_chances(raw: Mapping[str, float]) -> dict[str, float]:
    """MUSIC_WEATHER_CHANCES, checked, keyed by the weather in lower case."""
    if not isinstance(raw, Mapping) or not raw:
        raise ConfigError("MUSIC_WEATHER_CHANCES is empty")
    chances: dict[str, float] = {}
    for weather, chance in raw.items():
        if not isinstance(weather, str) or not weather.strip():
            raise ConfigError("MUSIC_WEATHER_CHANCES: each weather must be some text")
        if isinstance(chance, bool) or not isinstance(chance, int | float) or not chance >= 0:
            raise ConfigError(
                f"MUSIC_WEATHER_CHANCES: the chance for {weather!r} must be a number, 0 or more"
            )
        key = weather.strip().casefold()
        if key in chances:
            raise ConfigError(f"MUSIC_WEATHER_CHANCES: {weather!r} is listed twice")
        chances[key] = float(chance)
    if not any(chances.values()):
        raise ConfigError("MUSIC_WEATHER_CHANCES: at least one chance must be more than 0")
    return chances


def playable(
    tracks: Iterable[Track], chances: Mapping[str, float]
) -> dict[int, dict[str, list[Track]]]:
    """The tracks that can be picked (their weather has a chance), by hour, then weather."""
    by_hour: dict[int, dict[str, list[Track]]] = {}
    for track in tracks:
        weather = track.weather.casefold()
        if chances.get(weather, 0) > 0:
            by_hour.setdefault(track.hour, {}).setdefault(weather, []).append(track)
    return by_hour


def next_hour(hours: Iterable[int], after: int | None) -> int | None:
    """The first of hours after `after`, going round from 11 pm to 12 am; with after None,
    the earliest. None if hours is empty."""
    available = sorted(set(hours))
    if not available:
        return None
    if after is not None:
        for hour in available:
            if hour > after:
                return hour
    return available[0]


def pick(
    choices: Mapping[str, Sequence[Track]], chances: Mapping[str, float], rng: random.Random
) -> Track:
    """One track of one hour: a weather by the chances, then one of its files."""
    weathers = sorted(choices)
    weather = rng.choices(weathers, weights=[chances[w] for w in weathers])[0]
    return rng.choice(choices[weather])


# --------------------------------------------------------------------------- #
# Remembering where it was
# --------------------------------------------------------------------------- #


class Bookmark:
    """The track being played, kept in a small file so that playing resumes after a
    restart. A file that can't be written is logged once; playing carries on."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._saved: str | None = None
        self._failing = False

    def read(self) -> str | None:
        try:
            text = self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("Can't read %s (%s); starting from the first hour", self.path, exc)
            return None
        self._saved = text or None
        return self._saved

    def write(self, name: str) -> None:
        if name == self._saved:
            return
        # Write a new file, then swap it in, so a power cut never leaves half a name.
        new = self.path.with_name(self.path.name + ".new")
        try:
            new.write_text(name + "\n", encoding="utf-8")
            os.replace(new, self.path)
        except OSError as exc:
            if not self._failing:
                log.error(
                    "Can't note the track in %s (%s); after a restart, playing will start "
                    "from the first hour",
                    self.path,
                    exc,
                )
            self._failing = True
            return
        self._saved = name
        self._failing = False


# --------------------------------------------------------------------------- #
# Playing
# --------------------------------------------------------------------------- #

# Plays one file; returns None once it has finished (or stop was set), or what went wrong.
Play = Callable[[Path, threading.Event], str | None]


class Player:
    """Plays one file at a time with mpg123."""

    def __init__(
        self, device: str, program: Sequence[str] = ("mpg123",), min_play_s: float = MIN_PLAY_S
    ) -> None:
        self.device = device
        self.program = list(program)
        self.min_play_s = min_play_s

    def __call__(self, path: Path, stop: threading.Event) -> str | None:
        command = [*self.program, "--quiet", "-o", "alsa", "-a", self.device, str(path)]
        started = time.monotonic()
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
            )
        except OSError as exc:
            return f"can't start {self.program[0]}: {exc}"
        while True:
            try:
                _, errors = proc.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if stop.is_set():
                    proc.terminate()
                    try:
                        proc.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.communicate()
                    return None
        if stop.is_set():
            return None
        said = " ".join(errors.split())[-300:]
        if proc.returncode != 0:
            return f"mpg123 stopped with code {proc.returncode}" + (f": {said}" if said else "")
        if time.monotonic() - started < self.min_play_s:
            return "it ended at once" + (f": {said}" if said else "")
        return None


class Jukebox:
    """Picks and plays tracks until stop is set."""

    def __init__(
        self,
        folder: Path,
        chances: Mapping[str, float],
        play: Play,
        bookmark: Bookmark,
        stop: threading.Event,
        *,
        rng: random.Random | None = None,
        retry_s: float = 10,
        empty_wait_s: float = EMPTY_WAIT_S,
    ) -> None:
        self.folder = folder
        self.chances = chances
        self.play = play
        self.bookmark = bookmark
        self.stop = stop
        self.rng = rng or random.Random()
        self.retry_s = retry_s
        self.empty_wait_s = empty_wait_s
        self._last_summary: str | None = None
        self._failing = False

    def _scan(self) -> Library | None:
        try:
            return scan(self.folder)
        except OSError as exc:
            self._summary(f"Can't read the music folder {self.folder} ({exc})", logging.ERROR)
            return None

    def _summary(self, text: str, level: int = logging.INFO) -> None:
        """Log what's in the folder, but only when it has changed."""
        if text != self._last_summary:
            log.log(level, "%s", text)
            self._last_summary = text

    def _describe(self, library: Library, hours: int) -> None:
        lines = [f"Found {len(library.tracks)} track(s) for {hours} hour(s) in {self.folder}"]
        if library.ignored:
            lines.append(
                f"; ignoring {len(library.ignored)} MP3(s) whose names aren't like "
                f"{NAME_EXAMPLE!r}: " + ", ".join(library.ignored[:5])
            )
            if len(library.ignored) > 5:
                lines.append(", …")
        unused = sorted(
            {t.weather for t in library.tracks if not self.chances.get(t.weather.casefold())}
        )
        if unused:
            lines.append(
                "; never picking weather(s) with no chance in MUSIC_WEATHER_CHANCES: "
                + ", ".join(unused)
            )
        self._summary("".join(lines))

    def run(self) -> None:
        resume = self.bookmark.read()
        after: int | None = None  # the hour last played
        current: Track | None = None
        failures = 0
        while not self.stop.is_set():
            library = self._scan()
            if library is None:
                self.stop.wait(self.empty_wait_s)
                continue
            by_hour = playable(library.tracks, self.chances)
            if not by_hour:
                self._summary(
                    f"No music to play in {self.folder}: put MP3s there named like "
                    f"{NAME_EXAMPLE!r}",
                    logging.WARNING,
                )
                self.stop.wait(self.empty_wait_s)
                continue
            self._describe(library, len(by_hour))

            if current is not None and current not in library.tracks:
                current, failures = None, 0  # removed while waiting to try it again
            if resume is not None:
                current = next((t for t in library.tracks if t.name == resume), None)
                if current is None:
                    log.info("%s, the track playing last time, has gone; starting afresh", resume)
                else:
                    log.info("Carrying on from last time")
                resume = None
            if current is None:
                hour = next_hour(by_hour, after)
                assert hour is not None
                current = pick(by_hour[hour], self.chances, self.rng)

            self.bookmark.write(current.name)
            log.log(logging.DEBUG if self._failing else logging.INFO, "Playing %s", current.name)
            problem = self.play(current.path, self.stop)
            if self.stop.is_set():
                break
            if problem is None:
                if self._failing:
                    log.info("Playing again")
                    self._failing = False
                after, current, failures = current.hour, None, 0
                continue

            failures += 1
            if not self._failing:
                log.error(
                    "Can't play %s (%s); trying again every %g s, and moving on to the next "
                    "hour after %d tries",
                    current.name,
                    problem,
                    self.retry_s,
                    MAX_FAILURES,
                )
                self._failing = True
            else:
                log.debug("Can't play %s (%s)", current.name, problem)
            if failures >= MAX_FAILURES:
                after, current, failures = current.hour, None, 0
            self.stop.wait(self.retry_s)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def check_settings() -> dict[str, float]:
    """All the music settings, checked; raise ConfigError for any mistake."""
    if not sysconfig.MUSIC_RETRY_S > 0:
        raise ConfigError("MUSIC_RETRY_S must be more than 0")
    if not sysconfig.MUSIC_DIR or not sysconfig.MUSIC_AUDIO_DEVICE:
        raise ConfigError("MUSIC_DIR and MUSIC_AUDIO_DEVICE must be set")
    return check_chances(sysconfig.MUSIC_WEATHER_CHANCES)


def run() -> int:
    configure_logging(sysconfig.LOG_LEVEL)
    if not sysconfig.MUSIC_ENABLED:
        log.info("The music is switched off (MUSIC_ENABLED in sysconfig.py); stopping")
        return 0
    try:
        chances = check_settings()
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return EX_CONFIG
    if shutil.which("mpg123") is None:
        log.error("Configuration error: mpg123 isn't installed (deploy/install.sh installs it)")
        return EX_CONFIG

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    log.info("Playing music from %s on %s", sysconfig.MUSIC_DIR, sysconfig.MUSIC_AUDIO_DEVICE)
    jukebox = Jukebox(
        Path(sysconfig.MUSIC_DIR),
        chances,
        Player(sysconfig.MUSIC_AUDIO_DEVICE),
        Bookmark(Path(sysconfig.MUSIC_BOOKMARK_FILE)),
        stop,
        retry_s=sysconfig.MUSIC_RETRY_S,
    )
    try:
        jukebox.run()
    except KeyboardInterrupt:
        stop.set()
    log.info("Stopping")
    return 0


def check() -> int:
    """Print what's in the music folder and how it would be played. Plays nothing."""
    folder = Path(sysconfig.MUSIC_DIR)
    try:
        chances = check_settings()
        library = scan(folder)
    except (ConfigError, OSError) as exc:
        print(f"Problem: {exc}")
        return 1
    total = sum(chances.values())
    names = {w.casefold(): w.strip() for w in sysconfig.MUSIC_WEATHER_CHANCES}
    print(f"Music folder: {folder}")
    print("Chances: " + ", ".join(f"{names[w]} {100 * c / total:.0f}%" for w, c in chances.items()))
    by_hour = playable(library.tracks, chances)
    columns = list(chances)
    print()
    print("Hour    " + "".join(f"{names[w]:>8}" for w in columns))
    for hour in range(HOURS):
        found = by_hour.get(hour, {})
        cells = "".join(f"{len(found.get(w, [])) or '-':>8}" for w in columns)
        note = "" if found else "   (skipped: no tracks)"
        print(f"{hour_name(hour)}   {cells}{note}")
    print()
    print(f"{len(library.tracks)} track(s); {len(by_hour)} of {HOURS} hours will play.")
    unused = sorted({t.weather for t in library.tracks if not chances.get(t.weather.casefold())})
    if unused:
        print("Never picked (no chance in MUSIC_WEATHER_CHANCES): " + ", ".join(unused))
    if library.ignored:
        print(f"Ignored, as the names aren't like {NAME_EXAMPLE!r}:")
        for name in library.ignored:
            print(f"  {name}")
    return 0 if by_hour else 1


def main() -> None:
    args = sys.argv[1:]
    if args == ["check"]:
        sys.exit(check())
    if args:
        sys.exit("Usage: python -m measure_station_db.music [check]")
    sys.exit(run())


if __name__ == "__main__":
    main()
