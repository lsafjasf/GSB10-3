"""End-to-end demo of the four required scenarios.

Run: python3 run_demo.py
All state is written to a temporary directory that is printed at the top.
"""

import json
import os
import tempfile

from repl.log_source import LogSource
from repl.puller import GapError, Puller
from repl.server import LogServer

RECORDS = ["log-%02d" % i for i in range(30)]
EXPECTED = list(enumerate(RECORDS))


def make_puller(server, workdir, consumed, **kwargs):
    kwargs.setdefault("batch_size", 8)
    kwargs.setdefault("timeout", 0.3)
    kwargs.setdefault("backoff", 0.01)
    return Puller(
        server.host, server.port,
        os.path.join(workdir, "checkpoint.json"),
        lambda pos, rec: consumed.append((pos, rec)),
        gap_report_path=os.path.join(workdir, "gaps.jsonl"),
        **kwargs,
    )


def scenario_dir(workdir, name):
    path = os.path.join(workdir, name)
    os.makedirs(path, exist_ok=True)
    return path


def scenario_resume(server, workdir):
    workdir = scenario_dir(workdir, "1-resume")
    print("== 1. 正常续传（拉 2 批后“重启”） ==")
    consumed = []
    first = make_puller(server, workdir, consumed)
    first.run_until_caught_up(max_batches=2)
    print("重启前 confirmed =", first.confirmed)
    del first

    second = make_puller(server, workdir, consumed)
    second.run_until_caught_up()
    print("重启后 confirmed =", second.confirmed)
    print("重启前后消费序列与源日志一致:", consumed == EXPECTED)
    assert consumed == EXPECTED
    print("序列头尾:", consumed[:2], "...", consumed[-2:])
    print()


def scenario_gap(server, workdir):
    workdir = scenario_dir(workdir, "2-gap")
    print("== 2. 位点已被清理（显式缺口报告，不静默跳转） ==")
    consumed = []
    puller = make_puller(server, workdir, consumed)
    puller.run_until_caught_up(max_batches=2)  # confirmed = 16
    server.source.purge_before(20)             # [16, 20) 被清理
    try:
        puller.pull_batch()
    except GapError as exc:
        print("捕获:", exc)
    print("confirmed 未被移动，仍为", puller.confirmed)
    with open(os.path.join(workdir, "gaps.jsonl")) as fh:
        print("缺口报告文件内容:", fh.read().strip())
    print()


def scenario_duplicate(server, workdir):
    workdir = scenario_dir(workdir, "3-duplicate")
    print("== 3. 批次重复投递（去重） ==")
    consumed = []
    puller = make_puller(server, workdir, consumed)
    puller.run_until_caught_up(max_batches=2)  # confirmed = 16
    server.source.redeliver_last = True        # 服务端重放上一批 [8, 16)
    puller.pull_batch()
    server.source.redeliver_last = True        # 再重放一次
    puller.pull_batch()
    print("检测到重复批次数:", puller.duplicate_batches)
    print("重放批次被丢弃，已消费序列与源日志前缀一致:",
          consumed == EXPECTED[:16])
    assert consumed == EXPECTED[:16]
    puller.run_until_caught_up()
    print("继续拉取后消费序列与源日志一致:", consumed == EXPECTED)
    assert consumed == EXPECTED
    print()


def scenario_network(server, workdir):
    workdir = scenario_dir(workdir, "4-network")
    print("== 4. 网络中断（超时 + 重试） ==")
    consumed = []
    puller = make_puller(server, workdir, consumed)
    puller.run_until_caught_up(max_batches=1)  # confirmed = 8
    server.source.drop_connections = 2         # 连续两次断连
    server.source.hang_connections = 1         # 再挂起一次触发读超时
    puller.run_until_caught_up()
    print("重试次数:", puller.retries, "(2 次断连 + 1 次超时)")
    print("消费序列与源日志一致:", consumed == EXPECTED)
    assert consumed == EXPECTED
    print()


def main():
    workdir = tempfile.mkdtemp(prefix="repl-demo-")
    print("状态目录:", workdir)
    print()
    scenarios = (scenario_resume, scenario_gap, scenario_duplicate,
                 scenario_network)
    for scenario in scenarios:
        # Each scenario gets a pristine source log and server.
        with LogServer(LogSource(RECORDS)) as server:
            scenario(server, workdir)
    print("全部场景通过。")


if __name__ == "__main__":
    main()
