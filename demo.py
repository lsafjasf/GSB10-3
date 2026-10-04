"""热加载演示：正常加载 / 校验失败保旧 / 原子切换 / 回滚。

运行：python3 demo.py
"""

from hotconfig import ConfigError, Field, HotConfig, Schema

SCHEMA = Schema([
    Field("host", str),
    Field("port", int, min_value=1, max_value=65535),
    Field("mode", str, choices=("dev", "prod")),
])


def show(hot, tag):
    print(f"[{tag}] version={hot.version} 读取结果={dict(hot.current.data)}")


def main():
    hot = HotConfig(schema=SCHEMA, initial={"host": "10.0.0.1", "port": 80, "mode": "prod"})
    show(hot, "初始 v1")

    # 1) 正常热加载 v2
    hot.reload_from_dict({"host": "10.0.0.2", "port": 8080, "mode": "prod"})
    show(hot, "生效 v2")

    # 2) 非法配置：整体校验失败，v2 保留
    try:
        hot.reload_from_dict({"host": 10, "port": 99999, "mode": "weird"})
    except ConfigError as exc:
        print("[校验失败] 原因:", *exc.reasons, sep="\n  - ")
    show(hot, "失败后仍 v2")

    # 3) 再加载 v3，然后演示回滚
    hot.reload_from_dict({"host": "10.0.0.3", "port": 9090, "mode": "dev"})
    show(hot, "生效 v3（回滚前）")

    snap = hot.rollback()
    print(f"[回滚] 已恢复到 version={snap.version}")
    show(hot, "回滚后")


if __name__ == "__main__":
    main()
