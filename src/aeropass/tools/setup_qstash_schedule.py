"""Create (idempotently) the QStash schedule that drives the outbox dispatcher.

Usage::

    uv run python -m aeropass.tools.setup_qstash_schedule [--consumer URL ...]

Reads ``QSTASH_TOKEN`` and ``PUBLIC_BASE_URL`` from the environment / .env. The fixed schedule id
makes re-runs update the same schedule. ``--consumer`` registers event consumers (audit,
escalation…) in the ``QSTASH_EVENTS_URL_GROUP`` URL group; normally each consuming team does this.
"""

from __future__ import annotations

import argparse
import asyncio

from qstash import AsyncQStash

from aeropass.config import get_settings

SCHEDULE_ID = "aeropass-outbox-dispatch"
EVERY_MINUTE = "* * * * *"


async def run(consumers: list[str]) -> None:
    settings = get_settings()
    if not settings.qstash_token or not settings.public_base_url:
        raise SystemExit("QSTASH_TOKEN and PUBLIC_BASE_URL are required")
    client = AsyncQStash(settings.qstash_token)
    destination = f"{settings.public_base_url.rstrip('/')}/internal/outbox/dispatch"
    schedule_id = await client.schedule.create(
        destination=destination,
        cron=EVERY_MINUTE,
        method="POST",
        schedule_id=SCHEDULE_ID,
        retries=0,  # the next minute's run is the retry
        timeout="25s",
        label="aeropass-outbox",
    )
    print(f"schedule {schedule_id}: POST {destination} cada minuto")

    if consumers:
        await client.url_group.upsert_endpoints(
            url_group=settings.qstash_events_url_group,
            endpoints=[{"url": url} for url in consumers],
        )
        print(f"URL group {settings.qstash_events_url_group}: {len(consumers)} consumer(s)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consumer", action="append", default=[], help="URL of a consumer")
    args = parser.parse_args()
    asyncio.run(run(args.consumer))


if __name__ == "__main__":
    main()
