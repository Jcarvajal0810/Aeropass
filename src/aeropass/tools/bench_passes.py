"""Measure pass-emission latency against a deployment (SC-002: p95 < 1 s).

Usage::

    uv run python -m aeropass.tools.bench_passes --base-url https://<deploy> \
        --tokens tokens.txt --n 200

``tokens.txt`` holds one Clerk session token per line, each for an already VERIFIED test
passenger. Requests go round-robin across accounts, sequential per account and ≤ 25/min per
account, so the 30/min rate limit is never hit. Exit code 1 when p95 ≥ 1 s.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from pathlib import Path

import httpx

PER_ACCOUNT_PER_MINUTE = 25
SLO_P95_SECONDS = 1.0


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(pct / 100 * len(ordered)) - 1))
    return ordered[index]


async def _account(
    client: httpx.AsyncClient, token: str, count: int, flight: str, out: list[float]
) -> list[int]:
    spacing = 60 / PER_ACCOUNT_PER_MINUTE
    failures: list[int] = []
    for _ in range(count):
        started = time.perf_counter()
        response = await client.post(
            "/v1/passes",
            json={"codigo_vuelo": flight},
            headers={"Authorization": f"Bearer {token}"},
        )
        elapsed = time.perf_counter() - started
        if response.status_code == 201:
            out.append(elapsed)
        else:
            failures.append(response.status_code)
        await asyncio.sleep(max(0.0, spacing - elapsed))
    return failures


async def run(base_url: str, tokens: list[str], n: int, flight: str) -> int:
    latencies: list[float] = []
    per_account = [n // len(tokens) + (1 if i < n % len(tokens) else 0) for i in range(len(tokens))]
    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        await client.get("/.well-known/jwks.json")  # warm the instance: SC-002 is for warm calls
        results = await asyncio.gather(
            *(
                _account(client, t, c, flight, latencies)
                for t, c in zip(tokens, per_account, strict=True)
            )
        )
    failures = [status for r in results for status in r]
    if not latencies:
        print(f"no successful responses; codes: {failures}")
        return 1
    p95 = percentile(latencies, 95)
    print(f"ok={len(latencies)} errores={len(failures)} {sorted(set(failures)) or ''}")
    print(
        f"p50={statistics.median(latencies):.3f}s p95={p95:.3f}s "
        f"p99={percentile(latencies, 99):.3f}s max={max(latencies):.3f}s"
    )
    return 0 if p95 < SLO_P95_SECONDS and not failures else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--tokens", required=True, type=Path)
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--flight", default="AV9380")
    args = parser.parse_args()
    tokens = [t.strip() for t in args.tokens.read_text().splitlines() if t.strip()]
    if not tokens:
        raise SystemExit("the tokens file is empty")
    raise SystemExit(asyncio.run(run(args.base_url, tokens, args.n, args.flight)))


if __name__ == "__main__":
    main()
