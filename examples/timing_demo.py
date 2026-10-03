"""时序演示：读方不退出，回收不完成；读方一退出，回收立即跟进。"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rcuref import Domain

t0 = time.monotonic()


def stamp(msg):
    print(f"[{time.monotonic() - t0:6.3f}s] {msg}")


def main():
    dom = Domain()
    obj = dom.register("payload", reclaim=lambda o: stamp("对象已回收"))
    entered, leave = threading.Event(), threading.Event()

    def reader():
        with dom.read_lock():
            stamp("读方进入临界区")
            entered.set()
            leave.wait(10)
            stamp("读方退出临界区")

    threading.Thread(target=reader).start()
    entered.wait(5)
    time.sleep(0.2)
    obj.release()
    stamp("计数归零 -> 进入待回收队列（未销毁）")
    time.sleep(0.3)
    stamp(f"读方仍在读：pending={dom.pending_count}, state={obj.state}")
    leave.set()
    dom.drain(timeout=5)
    stamp(f"drain 返回：state={obj.state}")


if __name__ == "__main__":
    main()
