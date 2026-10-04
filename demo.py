"""热加载演示：正常生效 -> 非法配置被拒绝 -> 回滚 -> 重做。"""

import json
import tempfile
import time
from pathlib import Path

from hotreload import ConfigStore, JsonFileWatcher, ValidationError

INITIAL = {
    "service_name": "order-svc",
    "port": 8080,
    "timeout_ms": 500,
    "features": ["read"],
    "rate_limit": {"qps": 100, "burst": 200},
}


def show(store, tag):
    cur = store.current
    print(f"[{tag}] v{cur.version} port={cur.get('port')} "
          f"features={cur.get('features')} qps={cur.get('rate_limit')['qps']}")


def main():
    tmp = tempfile.mkdtemp(prefix="hotreload-demo-")
    path = Path(tmp) / "config.json"
    path.write_text(json.dumps(INITIAL), encoding="utf-8")

    store = ConfigStore(INITIAL)
    show(store, "初始")

    with JsonFileWatcher(store, str(path), interval=0.2) as watcher:
        # 1. 合法变更：整体生效
        good = dict(INITIAL, port=9090, features=["read", "write"])
        path.write_text(json.dumps(good), encoding="utf-8")
        time.sleep(0.6)
        show(store, "合法热加载后")

        # 2. 非法变更：整体拒绝，旧配置保留，原因可见
        bad = dict(INITIAL, port=-1, timeout_ms="abc")
        path.write_text(json.dumps(bad), encoding="utf-8")
        time.sleep(0.6)
        show(store, "非法热加载后(应保持不变)")
        err = watcher.last_error
        if isinstance(err, ValidationError):
            for field, why in sorted(err.errors.items()):
                print(f"    拒绝原因 {field}: {why}")

    # 3. 回滚与重做
    store.rollback()
    show(store, "回滚后")
    store.redo()
    show(store, "重做后")


if __name__ == "__main__":
    main()
