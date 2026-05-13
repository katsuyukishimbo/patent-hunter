from datetime import date

import pytest

from patent_hunter.week import IsoWeek, parse_iso_week, previous_iso_week


def test_parse_iso_week_basic():
    w = parse_iso_week("2026-W19")
    assert w == IsoWeek(2026, 19)
    assert w.label == "2026-W19"


def test_parse_iso_week_pads_label():
    assert parse_iso_week("2026-W01").label == "2026-W01"


@pytest.mark.parametrize(
    "bad",
    ["2026W19", "2026-19", "2026-W", "abcd-W19", "2026-W54"],
)
def test_parse_iso_week_rejects_bad(bad):
    with pytest.raises(ValueError):
        parse_iso_week(bad)


def test_iso_week_start_and_end():
    w = IsoWeek(2026, 19)
    assert w.start_date().isoweekday() == 1  # Monday
    assert w.end_date().isoweekday() == 7  # Sunday
    assert (w.end_date() - w.start_date()).days == 6


def test_previous_iso_week_steps_back_seven_days():
    today = date(2026, 5, 13)  # a Wednesday in W20
    prev = previous_iso_week(today)
    # The week containing 2026-05-13 is W20, so the previous one is W19.
    assert prev == IsoWeek(2026, 19)


def test_previous_iso_week_year_boundary():
    today = date(2026, 1, 1)  # 2026-W01 (Thursday)
    prev = previous_iso_week(today)
    # The week before W01 is the last week of the prior ISO year.
    assert prev.year == 2025
    assert prev.week in (52, 53)
