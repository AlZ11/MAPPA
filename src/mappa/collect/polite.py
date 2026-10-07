"""Politeness: at most one request per interval to each site, at most N sites at once.

Two separate mechanisms, because they protect against different things:

- ``DomainRateLimiter`` spaces out *every request* to a host, retries included, so no
  site sees more than ``rate_limit_per_domain_rps`` from us however the work is ordered.
- ``run_per_domain`` decides *which work runs concurrently*: items are grouped by host,
  each host's items run one after another, and at most ``max_parallel_domains`` hosts are
  worked on at the same time.
"""

import asyncio
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence

from mappa.log import get_logger

log = get_logger(__name__)

Sleep = Callable[[float], Awaitable[None]]


class DomainRateLimiter:
    def __init__(
        self,
        min_interval_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._interval = min_interval_s
        self._clock = clock
        self._sleep = sleep
        self._next_ok: dict[str, float] = {}
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, host: str) -> None:
        """Wait until ``host`` may receive another request, then claim the slot."""
        async with self._locks[host]:
            now = self._clock()
            ready = self._next_ok.get(host, now)
            if ready > now:
                await self._sleep(ready - now)
                now = self._clock()
            self._next_ok[host] = now + self._interval


async def run_per_domain[T](
    items: Sequence[T],
    *,
    host: Callable[[T], str],
    worker: Callable[[T], Awaitable[None]],
    max_parallel_domains: int,
) -> list[tuple[T, BaseException]]:
    """Run ``worker`` on every item, one host's items in sequence, N hosts in parallel.

    Workers record their own outcomes (statuses in the database). An exception escaping
    a worker is a bug, not a fetch result: it is logged, collected and returned so the
    command can fail loudly, and the item stays unrecorded so a re-run retries it.
    """
    groups: dict[str, list[T]] = {}
    for item in items:
        groups.setdefault(host(item), []).append(item)
    slots = asyncio.Semaphore(max_parallel_domains)
    crashed: list[tuple[T, BaseException]] = []

    async def drain(queue: list[T]) -> None:
        async with slots:
            for item in queue:
                try:
                    await worker(item)
                except Exception as exc:
                    log.exception("worker.crashed", item=repr(item))
                    crashed.append((item, exc))

    await asyncio.gather(*(drain(queue) for queue in groups.values()))
    return crashed
