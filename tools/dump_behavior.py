"""行为快照工具：对指定包运行固定场景矩阵并输出 JSON。

用法:
    python3 tools/dump_behavior.py legacy   # 重构前
    python3 tools/dump_behavior.py app      # 重构后
"""

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def snapshot(prefix):
    api_client = importlib.import_module(prefix + ".api_client")
    middleware = importlib.import_module(prefix + ".middleware")
    worker = importlib.import_module(prefix + ".worker")
    scheduler = importlib.import_module(prefix + ".scheduler")
    exporter = importlib.import_module(prefix + ".exporter")
    auth = importlib.import_module(prefix + ".auth")
    session = importlib.import_module(prefix + ".session")
    pricing = importlib.import_module(prefix + ".pricing")
    cart = importlib.import_module(prefix + ".cart")
    notifier = importlib.import_module(prefix + ".notifier")
    report = importlib.import_module(prefix + ".report")
    helpers = importlib.import_module(prefix + ".test_helpers")

    return {
        "api_client.http_get": [
            api_client.http_get("https://internal/api", minute, fails)
            for minute in (0, 99, 100, 101)
            for fails in (0, 1, 3, 4)
        ],
        "middleware.check_request": [
            middleware.check_request(minute) for minute in (0, 99, 100, 101)
        ],
        "worker.run_job": [worker.run_job(fails) for fails in (0, 1, 3, 4)],
        "worker.job_timed_out": [
            worker.job_timed_out(elapsed) for elapsed in (0, 299, 300, 301)
        ],
        "scheduler.plan_batches": [
            scheduler.plan_batches(n) for n in (0, 1, 499, 500, 501, 1200)
        ],
        "scheduler.batch_timeout": scheduler.batch_timeout(),
        "exporter.export_pages": [
            exporter.export_pages(n) for n in (0, 1, 999, 1000, 1001, 2500)
        ],
        "exporter.download_timeout": exporter.download_timeout(),
        "auth.session_valid": [
            auth.session_valid(minutes) for minutes in (0, 29, 30, 31, 1440)
        ],
        "auth.login_allowed": [
            auth.login_allowed(n) for n in (0, 4, 5, 6)
        ],
        "session.lockout_remaining": [
            session.lockout_remaining(n) for n in (0, 4, 5, 6)
        ],
        "pricing.price": [
            pricing.price(qty, 10.0, discount)
            for qty in (1, 19, 20, 21)
            for discount in (0.0, 0.2, 0.3, 0.35, 0.5)
        ],
        "cart.cart_summary": [
            cart.cart_summary(qs) for qs in ([0], [5, 5, 9], [10, 10], [20])
        ],
        "notifier.send_sms": [
            notifier.send_sms("x" * length, fails)
            for length in (159, 160, 161, 320)
            for fails in (0, 4)
        ],
        "report.build_report": [
            report.build_report(n) for n in (0, 1, 500, 501, 1000)
        ],
        "test_helpers.make_long_message_len": len(helpers.make_long_message()),
        "test_helpers.discount_cap": helpers.discount_cap(),
        "test_helpers.fixture_rate_limit": helpers.FIXTURE_RATE_LIMIT,
    }


if __name__ == "__main__":
    prefix = sys.argv[1] if len(sys.argv) > 1 else "app"
    json.dump(snapshot(prefix), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")

