from datetime import date, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Current instant, timezone-aware UTC."""
        ...

    def today(self) -> date: ...
