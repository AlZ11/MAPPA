"""Politeness: per-host spacing of requests, and at most N hosts worked on at once."""

import asyncio

import pytest

from mappa.collect.polite import DomainRateLimiter, run_per_domain


class FakeClock:
    """Time that only moves when the limiter sleeps, so tests are instant and exact."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 6))
        self.now += seconds


def test_limiter_spaces_requests_to_the_same_host() -> None:
    clock = FakeClock()
    limiter = DomainRateLimiter(1.0, clock=clock, sleep=clock.sleep)

    async def three_requests() -> list[float]:
        starts = []
        for _ in range(3):
            await limiter.acquire("play.google.com")
            starts.append(clock.now)
        return starts

    assert asyncio.run(three_requests()) == [0.0, 1.0, 2.0]


def test_limiter_does_not_slow_down_other_hosts() -> None:
    clock = FakeClock()
    limiter = DomainRateLimiter(1.0, clock=clock, sleep=clock.sleep)

    async def run() -> None:
        await limiter.acquire("a.example")
        await limiter.acquire("b.example")
        await limiter.acquire("c.example")

    asyncio.run(run())
    assert clock.sleeps == []


def test_at_most_n_hosts_run_at_once_and_each_host_runs_in_order() -> None:
    active: set[str] = set()
    peak = 0
    order: dict[str, list[int]] = {}

    async def worker(item: tuple[str, int]) -> None:
        nonlocal peak
        host, n = item
        active.add(host)
        peak = max(peak, len(active))
        order.setdefault(host, []).append(n)
        await asyncio.sleep(0.001)
        if n == 2:  # the host's last item: it stops being active
            active.discard(host)

    items = [(f"host{h}", n) for h in range(6) for n in range(3)]
    crashed = asyncio.run(
        run_per_domain(items, host=lambda i: i[0], worker=worker, max_parallel_domains=2)
    )
    assert crashed == []
    assert peak <= 2
    assert all(seq == [0, 1, 2] for seq in order.values())
    assert len(order) == 6


def test_a_crashing_item_is_reported_and_the_rest_still_run() -> None:
    done: list[int] = []

    async def worker(n: int) -> None:
        if n == 3:
            raise ValueError("bug")
        done.append(n)

    crashed = asyncio.run(
        run_per_domain(
            list(range(6)), host=lambda n: f"h{n % 2}", worker=worker, max_parallel_domains=4
        )
    )
    assert sorted(done) == [0, 1, 2, 4, 5]
    assert [(item, type(exc)) for item, exc in crashed] == [(3, ValueError)]


@pytest.mark.parametrize("interval", [0.5, 2.0])
def test_limiter_interval_follows_the_configured_rate(interval: float) -> None:
    clock = FakeClock()
    limiter = DomainRateLimiter(interval, clock=clock, sleep=clock.sleep)

    async def two() -> None:
        await limiter.acquire("x")
        await limiter.acquire("x")

    asyncio.run(two())
    assert clock.sleeps == [interval]
