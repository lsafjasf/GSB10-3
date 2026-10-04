"""并发对比：旧版（全局变量）vs 重构版（显式上下文）。

运行：python3 compare_concurrency.py
"""
import pathlib
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

import app  # noqa: E402
import legacy_app  # noqa: E402


def run(handler, count=8):
    barrier = threading.Barrier(count)
    results, errors = [None] * count, []

    def worker(i):
        request = {"request_id": "req-%d" % i, "user": "user-%d" % i}
        try:
            if handler is app.handle_request:
                results[i] = handler(app.RequestContext(request), barrier)
            else:
                results[i] = handler(request, barrier)
        except Exception as exc:  # noqa: BLE001
            errors.append((i, repr(exc)))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    polluted = [
        i
        for i, r in enumerate(results)
        if r is None
        or r["request_id"] != "req-%d" % i
        or r["user"] != "user-%d" % i
        or r["g_request_id"] != "req-%d" % i
    ]
    return results, polluted, errors


def report(name, results, polluted, errors):
    print("== %s ==" % name)
    for i, r in enumerate(results):
        tag = "OK " if i not in polluted else "POLLUTED"
        print("  thread-%d [%s] got %s (expect req-%d / user-%d)"
              % (i, tag, r, i, i))
    print("  -> 污染请求数: %d / %d, 异常: %d\n" % (len(polluted), len(results), len(errors)))


if __name__ == "__main__":
    print()
    old = run(legacy_app.handle_request)
    new = run(app.handle_request)
    report("旧版 legacy_app（全局变量）", *old)
    report("重构版 app（显式上下文）", *new)
    sys.exit(1 if (old[1] == [] or new[1] != []) else 0)
