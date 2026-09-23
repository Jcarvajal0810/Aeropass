from datetime import UTC, date, datetime, timedelta


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def today(self) -> date:
        return self.now().date()


class FakeClock:
    """Settable clock for tests."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def today(self) -> date:
        return self._now.date()

    def set(self, value: datetime) -> None:
        self._now = value

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
