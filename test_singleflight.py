"""Self-tests for singleflight.py (stdlib only).

Run:  python3 -m unittest -v test_singleflight.py
"""

import asyncio
import unittest

from singleflight import SingleFlight


class Counter:
    """Counts how many times the real (slow) operation actually ran."""

    def __init__(self, delay=0.05, value="ok"):
        self.calls = 0
        self.delay = delay
        self.value = value

    async def __call__(self):
        self.calls += 1
        await asyncio.sleep(self.delay)  # simulate the slow operation
        return self.value


class SingleFlightTest(unittest.IsolatedAsyncioTestCase):
    async def test_single_request(self):
        sf = SingleFlight()
        op = Counter()
        result = await sf.do("k", op)
        self.assertEqual(result, "ok")
        self.assertEqual(op.calls, 1)

    async def test_concurrent_merge(self):
        """50 concurrent requests on one key -> exactly 1 real call."""
        sf = SingleFlight()
        op = Counter()
        results = await asyncio.gather(*(sf.do("k", op) for _ in range(50)))
        self.assertEqual(results, ["ok"] * 50)
        self.assertEqual(op.calls, 1)  # call-count assertion

    async def test_staggered_arrivals_still_merge(self):
        """Requests arriving while the call is in flight also merge."""
        sf = SingleFlight()
        op = Counter(delay=0.1)

        async def delayed(i):
            await asyncio.sleep(i * 0.01)  # 0ms..40ms, all < 100ms flight
            return await sf.do("k", op)

        results = await asyncio.gather(*(delayed(i) for i in range(5)))
        self.assertEqual(results, ["ok"] * 5)
        self.assertEqual(op.calls, 1)

    async def test_failure_shared_to_all_waiters(self):
        """One real failure; every waiter receives the SAME exception."""
        sf = SingleFlight()
        calls = 0
        boom = ValueError("boom")

        async def failing():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            raise boom

        outcomes = await asyncio.gather(
            *(sf.do("k", failing) for _ in range(10)),
            return_exceptions=True,
        )
        self.assertEqual(calls, 1)  # only one real call
        for exc in outcomes:
            self.assertIs(exc, boom)  # same exception instance fanned out

    async def test_failure_is_not_reused(self):
        """Failure is not cached: the next call retries for real."""
        sf = SingleFlight()
        calls = 0

        async def flaky():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("first attempt fails")
            return "recovered"

        with self.assertRaises(RuntimeError):
            await sf.do("k", flaky)
        self.assertEqual(calls, 1)

        # Error was NOT stored: this triggers a fresh real call.
        result = await sf.do("k", flaky)
        self.assertEqual(result, "recovered")
        self.assertEqual(calls, 2)

    async def test_expiry_triggers_real_recall(self):
        """Within TTL: cached (no new call). After TTL: real re-call."""
        sf = SingleFlight(ttl=0.1)
        op = Counter(delay=0)

        await sf.do("k", op)               # t=0: real call
        before_expiry = op.calls
        await sf.do("k", op)               # cached, no new call
        await sf.do("k", op)               # cached, no new call
        within_ttl = op.calls

        await asyncio.sleep(0.15)          # let the result expire
        await sf.do("k", op)               # expired -> real re-call
        after_expiry = op.calls

        print(
            "\n[expiry data] calls at first fetch: %d, "
            "within TTL after 2 extra requests: %d, after expiry: %d"
            % (before_expiry, within_ttl, after_expiry)
        )
        self.assertEqual(before_expiry, 1)
        self.assertEqual(within_ttl, 1)    # TTL hits served from cache
        self.assertEqual(after_expiry, 2)  # expiry forced a real re-call

    async def test_no_ttl_means_no_caching(self):
        """ttl=None: sequential (non-overlapping) calls each run for real."""
        sf = SingleFlight()
        op = Counter(delay=0)
        await sf.do("k", op)
        await sf.do("k", op)
        self.assertEqual(op.calls, 2)

    async def test_waiter_cancellation_does_not_hurt_others(self):
        """Cancelling one waiter must not cancel the shared operation."""
        sf = SingleFlight()
        op = Counter(delay=0.05)

        waiter = asyncio.ensure_future(sf.do("k", op))
        bystander = asyncio.ensure_future(sf.do("k", op))
        await asyncio.sleep(0.01)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter

        self.assertEqual(await bystander, "ok")  # unaffected
        self.assertEqual(op.calls, 1)

    async def test_different_keys_are_independent(self):
        sf = SingleFlight()
        op = Counter(delay=0)
        await asyncio.gather(sf.do("a", op), sf.do("b", op))
        self.assertEqual(op.calls, 2)

    def test_invalid_ttl_rejected(self):
        with self.assertRaises(ValueError):
            SingleFlight(ttl=0)


if __name__ == "__main__":
    unittest.main()
