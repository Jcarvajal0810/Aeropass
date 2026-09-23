from dataclasses import dataclass
from typing import Any

from aeropass.ports.event_publisher import PublishFailed


@dataclass(frozen=True)
class PublishedMessage:
    deduplication_id: str
    tipo: str
    payload: dict[str, Any]


class InMemoryEventPublisher:
    """Records successfully published messages; ``down=True`` simulates a QStash outage."""

    def __init__(self) -> None:
        self.published: list[PublishedMessage] = []
        self.down = False
        self.attempts = 0

    async def publish(self, event_id: str, tipo: str, payload: dict[str, Any]) -> None:
        self.attempts += 1
        if self.down:
            raise PublishFailed("simulated outage")
        self.published.append(PublishedMessage(event_id, tipo, payload))
