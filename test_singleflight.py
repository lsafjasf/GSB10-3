"""Self-tests for singleflight.py (standard library only).

Run:  python3 -m unittest -v test_singleflight
      python3 test_singleflight.py
"""

import asyncio
import unittest

from singleflight import Group


def run(coro):
    return asyncio.run(coro)


class CallCounter:
    """Counts how many times the real factory actually ran."""

    def __init__(self):
        self.calls = 0

    def __call__(self, value=None, delay=0.0, error=None):
        async def factory():
            self.calls += 1
            if delay:
                await asyncio.sleep(delay)
            if error is not None:
                raise error
            return value

        return factory


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestSingleRequest(unittest.TestCase):
    def test_single_request_returns_value_and_calls_once(self):
        counter = CallCounter()
        group = Group()

        async def main():
            return await group.do("k", counter(value=42))

        self.assertEqual(run(main()), 42)
        self.assertEqual(counter.calls, 1)

    def test_result_value_is_shared_object(self):
        group = Group()
        payload = {"rows": [1, 2, 3]}

        async def main():
            async def factory():
                await asyncio.sleep(0.01)
                return payload

            return await asyncio.gather(
                group.do("k", factory), group.do("k", factory)
            )

        first, second = run(main())
        self.assertIs(first, payload)
        self.assertIs(second, payload)


class TestConcurrentCoalescing(unittest.TestCase):
    def test_concurrent_same_key_calls_factory_once(self):
        counter = CallCounter()
        group = Group()
        fan_in = 50

        async def main():
            return await asyncio.gather(
                *(group.do("hot-key", counter(value="v", delay=0.05))
                  for _ in range(fan_in))
            )

        results = run(main())
        self.assertEqual(results, ["v"] * fan_in)
        # The core assertion: 50 concurrent callers, exactly 1 real call.
        self.assertEqual(counter.calls, 1)

    def test_distinct_keys_do_not_coalesce(self):
        counter = CallCounter()
        group = Group()

        async def main():
            return await asyncio.gather(
                group.do("a", counter(value=1, delay=0.02)),
                group.do("b", counter(value=2, delay=0.02)),
                group.do("c", counter(value=3, delay=0.02)),
            )

        self.assertEqual(run(main()), [1, 2, 3])
        self.assertEqual(counter.calls, 3)

    def test_sequential_calls_without_ttl_recall(self):
        counter = CallCounter()
        group = Group()  # ttl=None: coalescing only, no caching

        async def main():
            await group.do("k", counter(value=1))
            await group.do("k", counter(value=2))

        run(main())
        self.assertEqual(counter.calls, 2)


class TestFailureSemantics(unittest.TestCase):
    def test_failure_is_broadcast_to_all_waiters(self):
        counter = CallCounter()
        group = Group()
        boom = RuntimeError("backend exploded")
        fan_in = 10

        async def main():
            return await asyncio.gather(
                *(group.do("k", counter(delay=0.02, error=boom))
                  for _ in range(fan_in)),
                return_exceptions=True,
            )

        outcomes = run(main())
        # Every waiter receives the *same* exception object.
        for outcome in outcomes:
            self.assertIs(outcome, boom)
        self.assertEqual(counter.calls, 1)

    def test_failure_is_not_cached_next_call_retries(self):
        counter = CallCounter()
        group = Group(ttl=60.0)  # caching enabled, but failures must not stick

        async def main():
            with self.assertRaises(RuntimeError):
                await group.do("k", counter(error=RuntimeError("first fails")))
            # Retry: must trigger a fresh real call and succeed.
            return await group.do("k", counter(value="recovered"))

        self.assertEqual(run(main()), "recovered")
        self.assertEqual(counter.calls, 2)


class TestExpiry(unittest.TestCase):
    def test_result_cached_until_ttl_then_refetched(self):
        counter = CallCounter()
        clock = FakeClock()
        group = Group(ttl=10.0, clock=clock)

        async def main():
            await group.do("k", counter(value="fresh"))
            await group.do("k", counter(value="fresh"))
            await group.do("k", counter(value="fresh"))
            before = counter.calls

            clock.advance(9.9)  # still within TTL
            await group.do("k", counter(value="stale?"))
            within = counter.calls

            clock.advance(0.2)  # now past expiry (10.1s elapsed)
            value = await group.do("k", counter(value="refetched"))
            after = counter.calls
            return before, within, after, value

        before, within, after, value = run(main())
        self.assertEqual(before, 1)   # 3 calls inside TTL -> 1 real call
        self.assertEqual(within, 1)   # 9.9s: cache still valid
        self.assertEqual(after, 2)    # expired -> real refetch happened
        self.assertEqual(value, "refetched")

    def test_expiry_comparison_report(self):
        """Prints before/after expiry call counts (fake clock, instant)."""
        counter = CallCounter()
        clock = FakeClock()
        ttl = 0.5
        group = Group(ttl=ttl, clock=clock)

        async def main():
            for _ in range(5):
                await group.do("k", counter(value="cached"))
            before = counter.calls
            clock.advance(ttl + 0.01)
            for _ in range(5):
                await group.do("k", counter(value="refetched"))
            after = counter.calls
            return before, after

        before, after = run(main())
        print(
            "\n[expiry report] ttl={}s | 5 requests before expiry -> "
            "{} real call(s) | 5 requests after expiry -> "
            "{} real call(s) total".format(ttl, before, after)
        )
        self.assertEqual(before, 1)
        self.assertEqual(after, 2)

    def test_per_call_ttl_overrides_group_default(self):
        counter = CallCounter()
        clock = FakeClock()
        group = Group(ttl=100.0, clock=clock)

        async def main():
            await group.do("k", counter(value=1), ttl=5.0)
            clock.advance(6.0)  # past per-call ttl, within group ttl
            await group.do("k", counter(value=2))

        run(main())
        self.assertEqual(counter.calls, 2)


class TestEdgeCases(unittest.TestCase):
    def test_cancelling_one_waiter_does_not_cancel_shared_call(self):
        counter = CallCounter()
        group = Group()

        async def main():
            waiter_a = asyncio.ensure_future(
                group.do("k", counter(value="done", delay=0.05))
            )
            waiter_b = asyncio.ensure_future(
                group.do("k", counter(value="done", delay=0.05))
            )
            await asyncio.sleep(0.01)
            waiter_a.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter_a
            # The surviving waiter still gets the shared result.
            return await waiter_b

        self.assertEqual(run(main()), "done")
        self.assertEqual(counter.calls, 1)

    def test_invalidate_forces_refetch(self):
        counter = CallCounter()
        group = Group(ttl=60.0)

        async def main():
            await group.do("k", counter(value=1))
            group.invalidate("k")
            return await group.do("k", counter(value=2))

        self.assertEqual(run(main()), 2)
        self.assertEqual(counter.calls, 2)

    def test_non_hashable_key_raises_typeerror(self):
        group = Group()

        async def main():
            async def factory():
                return 1

            with self.assertRaises(TypeError):
                await group.do(["unhashable"], factory)

        run(main())

    def test_inflight_and_cache_bookkeeping(self):
        counter = CallCounter()
        group = Group(ttl=60.0)

        async def main():
            task = asyncio.ensure_future(
                group.do("k", counter(value=1, delay=0.05))
            )
            await asyncio.sleep(0.01)
            self.assertEqual(group.inflight_count, 1)
            self.assertEqual(group.cached_count, 0)
            await task
            self.assertEqual(group.inflight_count, 0)
            self.assertEqual(group.cached_count, 1)
            group.clear()
            self.assertEqual(group.cached_count, 0)

        run(main())


if __name__ == "__main__":
    unittest.main(verbosity=2)
