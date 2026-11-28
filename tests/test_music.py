"""Tests for the music service: names, order, weather chances, resuming and playing."""

from __future__ import annotations

import json
import logging
import random
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import pytest

from measure_station_db import music, sysconfig
from measure_station_db.music import (
    Bookmark,
    ConfigError,
    Jukebox,
    Player,
    check_chances,
    hour_name,
    next_hour,
    parse_name,
    pick,
    playable,
    scan,
)

WEATHERS = ("Sunny", "Rain", "Snow")
CHANCES = {"sunny": 60.0, "rain": 25.0, "snow": 15.0}


def make_music(folder: Path, hours=range(24), weathers=WEATHERS) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for hour in hours:
        for weather in weathers:
            (folder / f"{hour_name(hour)} ({weather}).mp3").write_bytes(b"")
    return folder


class StopAfter(threading.Event):
    """A stop event that sets itself after a number of waits (so loops end in tests)."""

    def __init__(self, waits: int = 100) -> None:
        super().__init__()
        self.waits_left = waits

    def wait(self, timeout: float | None = None) -> bool:
        self.waits_left -= 1
        if self.waits_left <= 0:
            self.set()
        return self.is_set()


class FakePlay:
    """Records what's played; stops after `plays` tracks; returns the given problems."""

    def __init__(self, plays: int, problems: list[str | None] | None = None) -> None:
        self.played: list[str] = []
        self.plays = plays
        self.problems = list(problems or [])

    def __call__(self, path: Path, stop: threading.Event) -> str | None:
        self.played.append(path.name)
        if len(self.played) >= self.plays:
            stop.set()
        return self.problems.pop(0) if self.problems else None


def jukebox(folder: Path, play, tmp_path: Path, chances=CHANCES, seed: int = 1, stop=None):
    stop = stop or StopAfter()
    return Jukebox(
        folder,
        chances,
        play,
        Bookmark(tmp_path / "bookmark"),
        stop,
        rng=random.Random(seed),
        retry_s=0,
        empty_wait_s=0,
    )


def hours_of(names: list[str]) -> list[int]:
    return [parse_name(name)[0] for name in names]


# --------------------------------------------------------------------------- #
# Names and the folder
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("01 am (Rain).mp3", (1, "Rain")),
        ("12 am (Snow).mp3", (0, "Snow")),
        ("12 pm (Sunny).mp3", (12, "Sunny")),
        ("01 pm (Sunny).mp3", (13, "Sunny")),
        ("11 pm (Rain).mp3", (23, "Rain")),
        ("1 AM (rain).MP3", (1, "rain")),
        ("03pm(Snow).mp3", (15, "Snow")),
        ("13 am (Rain).mp3", None),
        ("00 am (Rain).mp3", None),
        ("03 am Rain.mp3", None),
        ("03 am (Rain).wav", None),
        ("notes.mp3", None),
    ],
)
def test_file_names_give_the_hour_and_weather(name, expected):
    assert parse_name(name) == expected


def test_hours_are_named_as_in_the_files():
    assert [hour_name(h) for h in (0, 1, 11, 12, 13, 23)] == [
        "12 am",
        "01 am",
        "11 am",
        "12 pm",
        "01 pm",
        "11 pm",
    ]


def test_the_folder_and_folders_inside_it_are_read(tmp_path):
    make_music(tmp_path / "music", hours=[3], weathers=["Rain"])
    make_music(tmp_path / "music" / "more", hours=[4], weathers=["Snow"])
    (tmp_path / "music" / "cover.jpg").write_bytes(b"")
    (tmp_path / "music" / "._03 am (Rain).mp3").write_bytes(b"")
    (tmp_path / "music" / "theme.mp3").write_bytes(b"")
    library = scan(tmp_path / "music")
    assert [(t.name, t.hour, t.weather) for t in library.tracks] == [
        ("03 am (Rain).mp3", 3, "Rain"),
        ("more/04 am (Snow).mp3", 4, "Snow"),
    ]
    assert library.ignored == ("theme.mp3",)


def test_a_missing_folder_is_an_error(tmp_path):
    with pytest.raises(OSError):
        scan(tmp_path / "nowhere")


# --------------------------------------------------------------------------- #
# Choosing
# --------------------------------------------------------------------------- #


def test_chances_are_checked_and_keyed_in_lower_case():
    assert check_chances({"Sunny": 60, " Rain ": 25.5, "Snow": 0}) == {
        "sunny": 60.0,
        "rain": 25.5,
        "snow": 0.0,
    }


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"Sunny": -1},
        {"Sunny": True},
        {"Sunny": "60"},
        {"Sunny": float("nan")},
        {"Sunny": 0, "Rain": 0},
        {"Sunny": 60, "sunny": 10},
        {"": 60},
    ],
)
def test_bad_chances_are_configuration_errors(raw):
    with pytest.raises(ConfigError):
        check_chances(raw)


def test_hours_go_round_the_clock_skipping_missing_ones():
    assert next_hour([5, 0, 23], None) == 0
    assert next_hour([0, 5, 23], 0) == 5
    assert next_hour([0, 5, 23], 6) == 23
    assert next_hour([0, 5, 23], 23) == 0
    assert next_hour([], 3) is None


def test_weathers_are_picked_with_their_chances(tmp_path):
    by_hour = playable(scan(make_music(tmp_path)).tracks, CHANCES)
    rng = random.Random(7)
    counts = Counter(pick(by_hour[9], CHANCES, rng).weather for _ in range(20_000))
    assert counts["Sunny"] / 20_000 == pytest.approx(0.60, abs=0.015)
    assert counts["Rain"] / 20_000 == pytest.approx(0.25, abs=0.015)
    assert counts["Snow"] / 20_000 == pytest.approx(0.15, abs=0.015)


def test_a_missing_weather_shares_its_chance_with_the_others(tmp_path):
    by_hour = playable(scan(make_music(tmp_path, weathers=["Sunny", "Rain"])).tracks, CHANCES)
    rng = random.Random(7)
    counts = Counter(pick(by_hour[9], CHANCES, rng).weather for _ in range(20_000))
    assert counts["Sunny"] / 20_000 == pytest.approx(60 / 85, abs=0.015)
    assert counts["Snow"] == 0


def test_a_weather_with_no_chance_is_never_picked(tmp_path):
    chances = {"sunny": 0.0, "rain": 1.0}
    by_hour = playable(
        scan(make_music(tmp_path, weathers=["Sunny", "Rain", "Fog"])).tracks, chances
    )
    assert {w for hour in by_hour.values() for w in hour} == {"rain"}


# --------------------------------------------------------------------------- #
# The jukebox
# --------------------------------------------------------------------------- #


def test_it_plays_one_track_an_hour_round_the_clock(tmp_path):
    play = FakePlay(plays=26)
    jukebox(make_music(tmp_path / "music"), play, tmp_path).run()
    assert hours_of(play.played) == [*range(24), 0, 1]


def test_the_weathers_follow_the_chances_over_many_hours(tmp_path):
    play = FakePlay(plays=3000)
    jukebox(make_music(tmp_path / "music"), play, tmp_path, seed=3).run()
    counts = Counter(parse_name(name)[1] for name in play.played)
    assert counts["Sunny"] / 3000 == pytest.approx(0.60, abs=0.03)
    assert counts["Snow"] / 3000 == pytest.approx(0.15, abs=0.03)


def test_hours_with_no_music_are_skipped(tmp_path):
    play = FakePlay(plays=4)
    jukebox(make_music(tmp_path / "music", hours=[2, 7, 20]), play, tmp_path).run()
    assert hours_of(play.played) == [2, 7, 20, 2]


def test_it_carries_on_from_the_track_playing_last_time(tmp_path):
    folder = make_music(tmp_path / "music")
    (tmp_path / "bookmark").write_text("05 pm (Snow).mp3\n", encoding="utf-8")
    play = FakePlay(plays=3)
    jukebox(folder, play, tmp_path).run()
    assert play.played[0] == "05 pm (Snow).mp3"
    assert hours_of(play.played) == [17, 18, 19]


def test_the_bookmark_follows_the_track_being_played(tmp_path):
    folder = make_music(tmp_path / "music")
    first = FakePlay(plays=5)
    jukebox(folder, first, tmp_path).run()
    # Stopped (e.g. a power cut) while the fifth track played: it plays again next time.
    assert (tmp_path / "bookmark").read_text(encoding="utf-8") == first.played[-1] + "\n"
    second = FakePlay(plays=2)
    jukebox(folder, second, tmp_path, seed=99).run()
    assert second.played[0] == first.played[-1]
    assert hours_of(second.played) == [4, 5]


def test_a_bookmarked_track_that_has_gone_starts_from_the_first_hour(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    (tmp_path / "bookmark").write_text("05 pm (Hail).mp3\n", encoding="utf-8")
    play = FakePlay(plays=2)
    jukebox(make_music(tmp_path / "music"), play, tmp_path).run()
    assert hours_of(play.played) == [0, 1]
    assert "has gone" in caplog.text


def test_a_track_that_fails_is_tried_again_then_skipped(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    play = FakePlay(plays=6, problems=["no sound", None, "no sound", "no sound", "no sound"])
    jukebox(make_music(tmp_path / "music"), play, tmp_path).run()
    hours = hours_of(play.played)
    # 12 am failed once, then played; 01 am failed 3 times, so it moved on to 02 am.
    assert hours == [0, 0, 1, 1, 1, 2]
    assert play.played[0] == play.played[1]
    assert caplog.text.count("Can't play") == 2  # once per run of failures
    assert "Playing again" in caplog.text


def test_an_empty_folder_waits_for_music(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    (tmp_path / "music").mkdir()
    play = FakePlay(plays=1)
    stop = StopAfter(waits=3)
    jukebox(tmp_path / "music", play, tmp_path, stop=stop).run()
    assert play.played == []
    assert caplog.text.count("No music to play") == 1


def test_a_missing_folder_is_logged_once(tmp_path, caplog):
    stop = StopAfter(waits=3)
    jukebox(tmp_path / "nowhere", FakePlay(plays=1), tmp_path, stop=stop).run()
    assert caplog.text.count("Can't read the music folder") == 1


def test_a_bookmark_that_cant_be_written_doesnt_stop_the_music(tmp_path, caplog):
    folder = make_music(tmp_path / "music")
    play = FakePlay(plays=3)
    box = Jukebox(folder, CHANCES, play, Bookmark(tmp_path / "no" / "bookmark"), StopAfter())
    box.run()
    assert len(play.played) == 3
    assert caplog.text.count("Can't note the track") == 1


# --------------------------------------------------------------------------- #
# The player
# --------------------------------------------------------------------------- #


def fake_mpg123(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_the_player_hands_mpg123_the_device_and_file(tmp_path):
    record = tmp_path / "args.json"
    code = f"import json, sys; open({str(record)!r}, 'w').write(json.dumps(sys.argv[1:]))"
    player = Player("hdmi:CARD=vc4hdmi0,DEV=0", fake_mpg123(code), min_play_s=0)
    track = tmp_path / "03 pm (Rain).mp3"
    assert player(track, threading.Event()) is None
    assert json.loads(record.read_text()) == [
        "--quiet",
        "-o",
        "alsa",
        "-a",
        "hdmi:CARD=vc4hdmi0,DEV=0",
        str(track),
    ]


def test_mpg123_failing_is_reported_with_its_message(tmp_path):
    code = "import sys; sys.stderr.write('Unable to open the audio device'); sys.exit(1)"
    problem = Player("dev", fake_mpg123(code), min_play_s=0)(tmp_path / "x.mp3", threading.Event())
    assert "code 1" in problem
    assert "Unable to open the audio device" in problem


def test_a_track_that_ends_at_once_counts_as_a_failure(tmp_path):
    problem = Player("dev", fake_mpg123("pass"), min_play_s=30)(
        tmp_path / "x.mp3", threading.Event()
    )
    assert "ended at once" in problem


def test_a_missing_mpg123_is_reported(tmp_path):
    problem = Player("dev", [str(tmp_path / "no-such-program")])(
        tmp_path / "x.mp3", threading.Event()
    )
    assert "can't start" in problem


def test_stopping_stops_the_track_at_once(tmp_path):
    stop = threading.Event()
    player = Player("dev", fake_mpg123("import time; time.sleep(30)"))
    threading.Timer(0.3, stop.set).start()
    started = time.monotonic()
    assert player(tmp_path / "x.mp3", stop) is None
    assert time.monotonic() - started < 10


# --------------------------------------------------------------------------- #
# The service and the check command
# --------------------------------------------------------------------------- #


@pytest.fixture
def service_logs(monkeypatch, caplog):
    """Keep pytest's log capture: run() would otherwise replace the root log handlers."""
    monkeypatch.setattr(music, "configure_logging", lambda level: None)
    caplog.set_level(logging.INFO)
    return caplog


def test_the_service_stops_at_once_when_switched_off(monkeypatch, service_logs):
    monkeypatch.setattr(sysconfig, "MUSIC_ENABLED", False)
    assert music.run() == 0
    assert "switched off" in service_logs.text


def test_bad_chances_stop_the_service_with_a_configuration_error(monkeypatch, service_logs):
    monkeypatch.setattr(sysconfig, "MUSIC_ENABLED", True)
    monkeypatch.setattr(sysconfig, "MUSIC_WEATHER_CHANCES", {"Sunny": -5})
    assert music.run() == music.EX_CONFIG
    assert "MUSIC_WEATHER_CHANCES" in service_logs.text


def test_mpg123_is_needed(monkeypatch, service_logs):
    monkeypatch.setattr(sysconfig, "MUSIC_ENABLED", True)
    monkeypatch.setattr(music.shutil, "which", lambda name: None)
    assert music.run() == music.EX_CONFIG
    assert "mpg123 isn't installed" in service_logs.text


def test_check_lists_each_hour_and_the_ignored_files(monkeypatch, tmp_path, capsys):
    folder = make_music(tmp_path / "music", hours=[0, 13], weathers=["Sunny", "Rain", "Fog"])
    (folder / "theme.mp3").write_bytes(b"")
    monkeypatch.setattr(sysconfig, "MUSIC_DIR", str(folder))
    assert music.check() == 0
    out = capsys.readouterr().out
    assert "Chances: Sunny 60%, Rain 25%, Snow 15%" in out
    assert "12 am          1       1       -" in out
    assert "02 am          -       -       -   (skipped: no tracks)" in out
    assert "2 of 24 hours will play" in out
    assert "Never picked (no chance in MUSIC_WEATHER_CHANCES): Fog" in out
    assert "  theme.mp3" in out


def test_check_fails_when_nothing_would_play(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sysconfig, "MUSIC_DIR", str(tmp_path / "nowhere"))
    assert music.check() == 1
    assert "Problem" in capsys.readouterr().out
