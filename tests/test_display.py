"""The LED display: its driver (against a fake I2C bus) and what the service shows."""

from __future__ import annotations

import logging

import pytest

from measure_station_db import sysconfig
from measure_station_db.display import service
from measure_station_db.display.ht16k33 import HT16K33LED
from measure_station_db.display.service import (
    ERROR,
    NO_VALUE,
    ConfigError,
    Debouncer,
    Item,
    Reading,
    Screen,
    compose,
    digits,
    parse_items,
    value_text,
)

G = HT16K33LED.CHARSET


class FakeBus:
    """Stands in for smbus2.SMBus and the HT16K33A: records commands, keeps the RAM."""

    def __init__(self) -> None:
        self.log: list[tuple[str, int]] = []  # ("cmd", byte) or ("ram", 0)
        self.ram = bytearray(16)
        self.fail = False

    def _check(self, addr: int) -> None:
        assert addr == 0x70
        if self.fail:
            raise OSError(121, "Remote I/O error")

    def write_byte(self, addr: int, value: int) -> None:
        self._check(addr)
        self.log.append(("cmd", value))

    def write_i2c_block_data(self, addr: int, register: int, data: list[int]) -> None:
        self._check(addr)
        assert register == 0x00 and len(data) == 16
        self.ram[:] = bytes(data)
        self.log.append(("ram", register))

    def read_i2c_block_data(self, addr: int, register: int, length: int) -> list[int]:
        self._check(addr)
        return list(self.ram[register : register + length])

    @property
    def commands(self) -> list[int]:
        return [v for kind, v in self.log if kind == "cmd"]

    def digits(self) -> list[int]:
        """The glyph on each of the 8 digits (even RAM bytes)."""
        return [self.ram[i << 1] for i in range(8)]


@pytest.fixture
def bus() -> FakeBus:
    return FakeBus()


# --------------------------------------------------------------------------- #
# The driver
# --------------------------------------------------------------------------- #


def test_begin_follows_the_datasheet_order(bus):
    HT16K33LED(bus).begin()
    # Oscillator on, ROW/INT as row output, full brightness, RAM cleared, then display on.
    assert bus.log == [
        ("cmd", 0x21),
        ("cmd", 0xA0),
        ("cmd", 0xEF),
        ("ram", 0x00),
        ("cmd", 0x81),
    ]
    assert bus.ram == bytearray(16)


def test_text_is_aligned_with_dots_joining_the_digit_before(bus):
    led = HT16K33LED(bus)
    led.set_string("-12.5°C", "r")
    assert bus.digits() == [0, 0, G["-"], G["1"], G["2"] | 0x80, G["5"], G["°"], G["c"]]
    led.set_string("co2", "l")
    assert bus.digits() == [G["c"], G["o"], G["2"], 0, 0, 0, 0, 0]


def test_every_segment_and_dot(bus):
    HT16K33LED(bus).set_string("8.8.8.8.8.8.8.8.")
    assert bus.digits() == [0xFF] * 8


def test_a_leading_or_second_dot_takes_its_own_digit():
    assert HT16K33LED.glyphs(".5") == [G["."], G["5"]]
    assert HT16K33LED.glyphs("1..") == [G["1"] | 0x80, G["."]]


def test_text_longer_than_the_display_is_cut_off(bus):
    HT16K33LED(bus).set_string("123456789")
    assert bus.digits() == [G[c] for c in "12345678"]


def test_rotation_reverses_the_digits_and_turns_each_upside_down(bus):
    led = HT16K33LED(bus).rotate()
    led.set_string("1", "l")
    # "1" lights b and c (0x06); upside down that's e and f (0x30), on the last digit.
    assert bus.digits() == [0, 0, 0, 0, 0, 0, 0, 0x30]
    assert led.buffer[0] == G["1"]  # the buffer itself stays unrotated


def test_standby_and_wake(bus):
    led = HT16K33LED(bus)
    led.begin()
    led.set_string("12")
    bus.log.clear()
    led.power_off()
    assert bus.commands == [0x80, 0x20]  # display off, then oscillator off
    bus.log.clear()
    led.power_on()
    assert bus.log == [("cmd", 0x21), ("ram", 0x00), ("cmd", 0x81)]


def test_brightness_and_blinking(bus):
    led = HT16K33LED(bus)
    led.begin()
    bus.log.clear()
    led.set_brightness(3)
    led.set_blink_rate(2)
    led.set_blink_rate(0.5)
    assert bus.commands == [0xE3, 0x83, 0x87]
    with pytest.raises(ValueError):
        led.set_brightness(16)
    with pytest.raises(ValueError):
        led.set_blink_rate(3)


def test_reading_the_ram_back(bus):
    led = HT16K33LED(bus)
    led.set_string("8.8.8.8.8.8.8.8.")
    assert led.read_ram() == bytes(led.buffer)


def test_characters_the_display_cant_show_are_refused(bus):
    with pytest.raises(ValueError, match="no glyph"):
        HT16K33LED(bus).set_string("hi!")


# --------------------------------------------------------------------------- #
# What the service shows
# --------------------------------------------------------------------------- #


def reading(value: float | None = None, text: str | None = None, age_s: float = 5) -> Reading:
    return Reading(age_s, value, text)


@pytest.mark.parametrize(
    ("value", "decimals", "shown"),
    [
        (612.0, 0, "612"),
        (22.4375, 1, "22.4"),
        (-12.55, 1, "-12.6"),
        (1006.53, 1, "1006.5"),
        (12345678.9, 1, "12345679"),  # one decimal won't fit in 8 digits: none
        (123456789.0, 0, ERROR),  # nothing fits
    ],
)
def test_numbers_are_rounded_to_fit(value, decimals, shown):
    assert value_text(reading(value), decimals, stale_s=300) == shown


def test_text_values_show_what_the_display_can():
    assert value_text(reading(text="warmup"), 1, 300) == "warmup"
    assert value_text(reading(text="ok!"), 1, 300) == "ok-"
    assert value_text(reading(text="much-too-long"), 1, 300) == "much-too"


def test_old_or_missing_values_show_dashes():
    assert value_text(reading(612.0, age_s=301), 0, stale_s=300) == NO_VALUE
    assert value_text(None, 0, stale_s=300) == NO_VALUE
    assert value_text(reading(), 0, stale_s=300) == NO_VALUE  # a null value


@pytest.mark.parametrize(
    ("label", "value", "shown"),
    [
        ("co2", "612", "co2  612"),
        ("t", "22.4", "t    22.4"),  # a dot shares its digit: 1 + 4 spaces + 3 = 8
        ("Pm2.5", "4.1", "Pm2.5  4.1"),
        ("P", "1006.5", "P  1006.5"),
        ("co2", "12345", "   12345"),  # no room for the label: the value alone
        ("co2", NO_VALUE, "co2 ----"),
    ],
)
def test_label_and_value_share_the_display_when_they_fit(label, value, shown):
    assert compose(label, value) == shown
    assert digits(compose(label, value)) == 8


CO2 = Item("co2", "mstn-2b", "co2", "co2_ppm", decimals=0)
TEMP = Item("t", "mstn-2b", "temperature", "temperature_c", decimals=1)


class Fetcher:
    def __init__(self, result: Reading | str | None) -> None:
        self.result = result
        self.calls: list[Item] = []

    def __call__(self, item: Item) -> Reading | str | None:
        self.calls.append(item)
        return self.result


def make_screen(fetch: Fetcher) -> Screen:
    return Screen([CO2, TEMP], fetch, label_s=1.5, refresh_s=10, stale_s=300, now=0)


def test_the_label_shows_first_then_the_value():
    fetch = Fetcher(reading(612.0))
    screen = make_screen(fetch)
    assert screen.text(0.0) == "co2"
    assert screen.text(1.4) == "co2"
    assert screen.text(1.5) == "co2  612"
    assert fetch.calls == [CO2]


def test_the_value_is_read_again_only_after_the_refresh_interval():
    fetch = Fetcher(reading(612.0))
    screen = make_screen(fetch)
    screen.text(2.0)
    screen.text(11.9)
    assert len(fetch.calls) == 1
    fetch.result = reading(640.0)
    assert screen.text(12.0) == "co2  640"
    assert len(fetch.calls) == 2


def test_a_press_moves_to_the_next_item_and_wraps_round():
    fetch = Fetcher(reading(22.44))
    screen = make_screen(fetch)
    screen.text(5.0)
    screen.press(5.0)
    assert screen.text(5.1) == "t"
    assert screen.text(6.6) == "t    22.4"
    assert fetch.calls[-1] == TEMP
    screen.press(7.0)
    assert screen.item == CO2


def test_a_database_problem_shows_err():
    screen = make_screen(Fetcher(ERROR))
    assert screen.text(2.0) == "co2  Err"


def test_bounces_count_as_one_press():
    debounce = Debouncer(0.2)
    assert [debounce.accept(t) for t in (1.0, 1.05, 1.19, 1.25, 2.0)] == [
        True,
        False,
        False,
        True,
        True,
    ]


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def test_the_default_items_are_valid():
    items = parse_items(sysconfig.DISPLAY_ITEMS, sysconfig.DISPLAY_STATION)
    assert len(items) == len(sysconfig.DISPLAY_ITEMS)
    assert all(digits(item.label) <= 8 for item in items)


def test_item_options_have_defaults():
    (item,) = parse_items([{"label": "rh", "source": "humidity", "key": "humidity_pct"}], "s1")
    assert item == Item("rh", "s1", "humidity", "humidity_pct", "values", 1)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"colour": "red"}, "unknown option"),
        ({"label": "hi!"}, "can't show"),
        ({"label": "much-too-long"}, "longer than 8"),
        ({"decimals": 5}, "'decimals'"),
        ({"decimals": True}, "'decimals'"),
        ({"section": "value"}, "'section'"),
        ({"key": ""}, "'key'"),
    ],
)
def test_mistakes_in_the_items_are_reported(change, message):
    entry = {"label": "co2", "source": "co2", "key": "co2_ppm", **change}
    with pytest.raises(ConfigError, match=message):
        parse_items([entry], "mstn-2b")


def test_no_items_is_a_mistake():
    with pytest.raises(ConfigError, match="empty"):
        parse_items([], "mstn-2b")


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #


@pytest.fixture
def service_logs(monkeypatch, caplog):
    """Keep pytest's log capture: run() would otherwise replace the root log handlers."""
    monkeypatch.setattr(service, "configure_logging", lambda level: None)
    caplog.set_level(logging.INFO)
    return caplog


def test_the_service_stops_at_once_when_switched_off(monkeypatch, service_logs):
    monkeypatch.setattr(sysconfig, "DISPLAY_ENABLED", False)
    assert service.run() == 0
    assert "switched off" in service_logs.text


def test_bad_settings_stop_the_service_with_a_configuration_error(monkeypatch, service_logs):
    monkeypatch.setattr(sysconfig, "DISPLAY_ENABLED", True)
    monkeypatch.setattr(sysconfig, "DISPLAY_BRIGHTNESS", 20)
    assert service.run() == service.EX_CONFIG
    assert "DISPLAY_BRIGHTNESS" in service_logs.text


def test_the_database_password_is_needed(monkeypatch, service_logs):
    monkeypatch.setattr(sysconfig, "DISPLAY_ENABLED", True)
    monkeypatch.setattr(sysconfig, "DATABASE_PASSWORD", None)
    assert service.run() == service.EX_CONFIG
    assert "MEASURE_STATION_DB_DATABASE_PASSWORD" in service_logs.text


def test_the_panel_draws_only_changes(bus):
    panel = service.Panel(bus)
    panel.show("co2  612")
    panel.show("co2  612")
    assert [kind for kind, _ in bus.log].count("ram") == 2  # begin's clear, then the text
    assert bus.digits()[0] == G["c"]


def test_the_panel_recovers_when_the_display_answers_again(bus, caplog):
    caplog.set_level(logging.INFO)
    panel = service.Panel(bus)
    panel.show("co2  612")
    bus.fail = True
    panel.show("co2  640")
    panel.show("co2  640")
    assert caplog.text.count("isn't answering") == 1  # logged once, not on every retry
    bus.fail = False
    panel.show("co2  640")
    assert "answering again" in caplog.text
    assert bus.digits()[-1] == G["0"]
    # Set up again from scratch, keeping the chosen brightness.
    dim = 0xE0 | sysconfig.DISPLAY_BRIGHTNESS
    assert bus.commands[-5:] == [0x21, 0xA0, dim, 0x81, dim]
