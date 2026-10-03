"""时钟回拨处理样例（虚拟时钟，输出确定；仅标准库）。

运行: python3 rollback_demo.py
"""

from idgen import (
    ClockMovedBackwardsError,
    IdGenerator,
    decode,
)


class ScriptedClock:
    """按脚本返回时间：调用几次后让时钟跳变。"""

    def __init__(self, start_ms):
        self.now = start_ms

    def time_ms(self):
        return self.now

    def sleep(self, seconds):
        # 样例中 sleep 让虚拟时钟推进，模拟“等待真实时钟恢复”
        self.now += 1


def main():
    clock = ScriptedClock(start_ms=1_000_000)
    gen = IdGenerator(
        node_id=1,
        time_ms=clock.time_ms,
        sleep=clock.sleep,
        epoch_ms=0,
        max_backward_ms=5,   # <=5ms 的回拨等待恢复；>5ms 拒绝
    )

    first = gen.next_id()
    print("正常发号      :", first, "->", decode_epoch0(first))

    # 场景 1：小幅回拨（2ms），等待时钟追平后继续
    clock.now -= 2
    second = gen.next_id()
    print("回拨 2ms 后  : 等待时钟恢复后发号", second, "->", decode_epoch0(second))
    assert second > first

    # 场景 2：大幅回拨（100ms），拒绝发号
    clock.now -= 100
    try:
        gen.next_id()
    except ClockMovedBackwardsError as exc:
        print(f"回拨 {exc.drift_ms}ms 后 : 拒绝发号 -> {exc}")

    # 场景 3：把容忍度设为 0，任何回拨都拒绝（最安全配置）
    strict = IdGenerator(
        node_id=1,
        time_ms=clock.time_ms,
        epoch_ms=0,
        max_backward_ms=0,
    )
    strict.next_id()
    clock.now -= 1
    try:
        strict.next_id()
    except ClockMovedBackwardsError as exc:
        print(f"严格模式回拨 {exc.drift_ms}ms: 拒绝发号 -> {exc}")


def decode_epoch0(value):
    p = decode(value)
    return f"t={p.timestamp_ms - 1704067200000}, node={p.node_id}, seq={p.sequence}"


if __name__ == "__main__":
    main()
