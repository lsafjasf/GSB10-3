"""Deterministic before/after concurrency comparison.

Runs the same interleaved two-request workload against:
  1. legacy_app  -- global mutable request context
  2. app         -- explicit RequestContext

A barrier forces both requests to install their context before either
consumes it, so any global-state pollution shows up deterministically.
"""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
import legacy_app

ITEMS_A = [("apple", 3.0), ("bread", 2.0)]          # subtotal 5.0
ITEMS_B = [("laptop", 100.0), ("mouse", 20.0)]     # subtotal 120.0


def run_legacy():
    legacy_app._audit_log.clear()
    barrier = threading.Barrier(2)
    legacy_app._inter_request_hook = barrier.wait
    results = {}

    def worker(slot, request_id, user_id, items, discount):
        results[slot] = legacy_app.handle_request(request_id, user_id, items, discount)

    t1 = threading.Thread(target=worker, args=(0, "req-A", "alice", ITEMS_A, 0.0))
    t2 = threading.Thread(target=worker, args=(1, "req-B", "bob", ITEMS_B, 0.5))
    t1.start(); t2.start()
    t1.join(); t2.join()
    legacy_app._inter_request_hook = None

    expected = {"req-A": 5.0, "req-B": 60.0}
    audit_by_req = {}
    for entry in legacy_app._audit_log:
        audit_by_req.setdefault(entry["request_id"], 0)
        audit_by_req[entry["request_id"]] += 1
    legacy_app._audit_log.clear()
    return results, expected, audit_by_req


def run_refactored():
    barrier = threading.Barrier(2)
    results = {}
    contexts = {}

    def worker(slot, ctx, items):
        barrier.wait()
        results[slot] = app.handle_request(ctx, items)
        contexts[slot] = ctx

    ctx_a = app.RequestContext("req-A", "alice", discount=0.0)
    ctx_b = app.RequestContext("req-B", "bob", discount=0.5)
    t1 = threading.Thread(target=worker, args=(0, ctx_a, ITEMS_A))
    t2 = threading.Thread(target=worker, args=(1, ctx_b, ITEMS_B))
    t1.start(); t2.start()
    t1.join(); t2.join()

    expected = {"req-A": 5.0, "req-B": 60.0}
    audit_by_req = {}
    for ctx in contexts.values():
        audit_by_req[ctx.request_id] = len(ctx.audit_log)
    return results, expected, audit_by_req


def report(name, results, expected, audit_by_req):
    print("== %s ==" % name)
    mismatches = 0
    for slot in sorted(results):
        r = results[slot]
        want_total = expected[r["request_id"]]
        ok_total = r["total"] == want_total
        if not ok_total:
            mismatches += 1
        print(
            "  result: request_id=%s user_id=%s total=%.2f (expected %.2f) %s"
            % (
                r["request_id"],
                r["user_id"],
                r["total"],
                want_total,
                "OK" if ok_total else "MISMATCH",
            )
        )
    print("  audit entries attributed by global context: %s" % audit_by_req)
    print("  total mismatches: %d" % mismatches)
    print()
    return mismatches


def main():
    legacy_mismatches = report("legacy (module-global context)", *run_legacy())
    new_mismatches = report("refactored (explicit RequestContext)", *run_refactored())

    print("summary: legacy mismatches=%d, refactored mismatches=%d"
          % (legacy_mismatches, new_mismatches))
    return 0 if new_mismatches == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
