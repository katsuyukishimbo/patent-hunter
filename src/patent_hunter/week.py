"""ISO week helpers.

We use ISO weeks (Monday-Sunday) so the "weekly batch" matches USPTO's
grant calendar conventions (grants are issued on Tuesdays, but we run the
report on Mondays for the prior week).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone


@dataclass(frozen=True)
class IsoWeek:
    """ISO 8601 week, e.g. ('2026-W19')."""

    year: int
    week: int

    @property
    def label(self) -> str:
        return f"{self.year:04d}-W{self.week:02d}"

    def start_date(self) -> date:
        """First day (Monday) of the ISO week."""
        return date.fromisocalendar(self.year, self.week, 1)

    def end_date(self) -> date:
        """Last day (Sunday) of the ISO week."""
        return date.fromisocalendar(self.year, self.week, 7)


def parse_iso_week(label: str) -> IsoWeek:
    """Parse 'YYYY-Www' into an IsoWeek.

    Raises ValueError if the label is malformed.
    """
    cleaned = label.strip().upper()
    if len(cleaned) != 8 or cleaned[4] != "-" or cleaned[5] != "W":
        raise ValueError(f"Bad ISO week '{label}', expected like '2026-W19'")
    try:
        year = int(cleaned[:4])
        week = int(cleaned[6:])
    except ValueError as exc:
        raise ValueError(f"Bad ISO week '{label}'") from exc
    if not 1 <= week <= 53:
        raise ValueError(f"Week out of range in '{label}'")
    # Round-trip through fromisocalendar to catch e.g. 2024-W53 in a 52-week year.
    date.fromisocalendar(year, week, 1)
    return IsoWeek(year=year, week=week)


def previous_iso_week(today: date | None = None) -> IsoWeek:
    """Return the ISO week immediately prior to `today` (defaults to date.today())."""
    today = today or date.today()
    # Step back 7 days from the current week's Monday.
    iso_year, iso_week, _ = today.isocalendar()
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    prev_monday = monday - timedelta(days=7)
    py, pw, _ = prev_monday.isocalendar()
    return IsoWeek(year=py, week=pw)


def format_date(d: date) -> str:
    """YYYY-MM-DD."""
    return d.strftime("%Y-%m-%d")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
