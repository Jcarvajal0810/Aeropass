from typing import Any, Protocol


class PublishFailed(Exception):
    """The event could not be handed to the delivery channel."""


class EventPublisher(Protocol):
    async def publish(self, event_id: str, tipo: str, payload: dict[str, Any]) -> None:
        """Hand the event to the channel. ``event_id`` is the deduplication id."""
        ...
