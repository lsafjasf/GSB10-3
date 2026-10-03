"""Demo: prints call-count data for merge / failure / expiry scenarios.

Run:  python3 demo.py
"""

import asyncio
import time

from singleflight import SingleFlight


async def main():
    # 1. Concurrent merge
    sf = SingleFlight()
    calls = 0

    async def slow_op():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.1)
        return "value"

    await asyncio.gather(*(sf.do("user:42", slow_op) for _ in range(20)))
    print("[merge]   20 concurrent requests -> real calls: %d" % calls)

    # 2. Failure fan-out, failure not reused
    sf2 = SingleFlight()
    fails = 0

    async def bad_op():
        nonlocal fails
        fails += 1
        await asyncio.sleep(0.05)
        raise RuntimeError("backend down")

    errs = await asyncio.gather(
        *(sf2.do("k", bad_op) for _ in range(5)), return_exceptions=True
    )
    print("[failure] 5 waiters, real calls: %d, all got same error: %s"
          % (fails, all(e is errs[0] for e in errs)))
    try:
        await sf2.do("k", bad_op)
    except RuntimeError:
        pass
    print("[failure] next request retried for real, total calls: %d" % fails)

    # 3. Expiry: before vs after TTL
    ttl = 0.2
    sf3 = SingleFlight(ttl=ttl)
    hits = 0

    async def fetch():
        nonlocal hits
        hits += 1
        return "fresh-%d" % hits

    t0 = time.monotonic()
    r1 = await sf3.do("k", fetch)
    n1 = hits
    r2 = await sf3.do("k", fetch)          # within TTL -> cache hit
    n2 = hits
    await asyncio.sleep(ttl + 0.05)        # let it expire
    r3 = await sf3.do("k", fetch)          # expired -> real re-call
    n3 = hits
    elapsed = time.monotonic() - t0
    print("[expiry]  ttl=%.1fs | first fetch: %r (calls=%d)"
          % (ttl, r1, n1))
    print("[expiry]  within TTL : %r (calls=%d, cache hit)"
          % (r2, n2))
    print("[expiry]  after %.2fs: %r (calls=%d, real re-call)"
          % (elapsed, r3, n3))


if __name__ == "__main__":
    asyncio.run(main())
