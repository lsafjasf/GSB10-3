"""Demo: prints call-count evidence for the singleflight contract.

Run:  python3 demo.py
"""

import asyncio

from singleflight import Group


async def demo_coalescing():
    group = Group()
    calls = 0

    async def slow_op():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.1)  # the "slow operation"
        return "result"

    results = await asyncio.gather(*(group.do("k", slow_op) for _ in range(20)))
    assert all(r == "result" for r in results)
    print("[1] coalescing : 20 concurrent requests -> %d real call(s)" % calls)


async def demo_failure():
    group = Group()
    calls = 0
    boom = RuntimeError("db down")

    async def failing():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        raise boom

    outcomes = await asyncio.gather(
        *(group.do("k", failing) for _ in range(5)), return_exceptions=True
    )
    assert all(o is boom for o in outcomes)
    print("[2] failure    : 5 waiters all got the SAME exception object "
          "(id=%s), %d real call(s)" % (hex(id(boom)), calls))

    # Failure is NOT cached: the next call retries the real operation.
    value = await group.do("k", lambda: asyncio.sleep(0, result="recovered"))
    assert value == "recovered"
    print("    retry      : failure not cached -> next call re-ran and "
          "returned %r" % value)


async def demo_expiry():
    group = Group(ttl=0.2)
    calls = 0

    async def op():
        nonlocal calls
        calls += 1
        return calls

    await asyncio.gather(*(group.do("k", op) for _ in range(5)))
    before = calls
    await asyncio.sleep(0.25)  # let the cached result expire
    await asyncio.gather(*(group.do("k", op) for _ in range(5)))
    after = calls
    print("[3] expiry     : ttl=0.2s | before expiry: 5 requests -> %d "
          "real call(s) | after expiry: 5 more requests -> %d real call(s) "
          "total" % (before, after))


async def main():
    await demo_coalescing()
    await demo_failure()
    await demo_expiry()


if __name__ == "__main__":
    asyncio.run(main())
