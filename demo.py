"""场景演示：生成续跑数据与幂等断言证据，产物写入 demo_artifacts/。

运行：python3 demo.py
"""

import json
import os
import shutil

from batch_job import (
    BatchJob,
    Checkpoint,
    CrashInjector,
    RecordSink,
    SimulatedCrash,
    make_shards,
)

ARTIFACTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_artifacts")


def fresh_workdir(name):
    path = os.path.join(ARTIFACTS, name)
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path)
    return path


def new_job(workdir, shards, injector=None, events=None):
    emit = events.append if events is not None else None
    ckpt = Checkpoint.load(os.path.join(workdir, "checkpoint.json"), on_event=emit)
    sink = RecordSink(os.path.join(workdir, "output.tsv"))
    return BatchJob(shards, ckpt, sink, injector=injector, on_event=emit)


def scenario_full_run(shards):
    workdir = fresh_workdir("1_full_run")
    events = []
    summary = new_job(workdir, shards, events=events).run()
    return {"workdir": workdir, "summary": summary, "events": events}


def scenario_interrupt_resume(shards):
    workdir = fresh_workdir("2_interrupt_resume")
    events_run1 = []
    try:
        new_job(workdir, shards, injector=CrashInjector(fail_after=7), events=events_run1).run()
    except SimulatedCrash as exc:
        crash_reason = str(exc)

    commits = [e for e in events_run1 if e["event"] == "record_commit"]
    interrupt_point = {"shard": commits[-1]["shard"], "position": commits[-1]["position"]}

    events_run2 = []
    summary = new_job(workdir, shards, events=events_run2).run()
    resume_event = next(e for e in events_run2 if e["event"] == "resume")
    resume_point = {"shard": resume_event["shard"], "position": resume_event["position"]}

    return {
        "workdir": workdir,
        "crash": crash_reason,
        "interrupt_point": interrupt_point,
        "resume_point": resume_point,
        "consistent": interrupt_point == resume_point,
        "resume_summary": summary,
        "run1_events": events_run1,
        "run2_events": events_run2,
    }


def scenario_duplicate_trigger(shards):
    workdir = fresh_workdir("3_duplicate_trigger")
    new_job(workdir, shards).run()
    with open(os.path.join(workdir, "output.tsv"), "rb") as f:
        bytes_after_first = f.read()

    events = []
    summary = new_job(workdir, shards, events=events).run()
    with open(os.path.join(workdir, "output.tsv"), "rb") as f:
        bytes_after_second = f.read()

    return {
        "workdir": workdir,
        "second_run_summary": summary,
        "output_unchanged": bytes_after_first == bytes_after_second,
        "commit_events_on_second_run": [e for e in events if e["event"] == "record_commit"],
        "events": events,
    }


def scenario_corrupt_checkpoint(shards):
    workdir = fresh_workdir("4_corrupt_checkpoint")
    new_job(workdir, shards).run()
    ckpt_path = os.path.join(workdir, "checkpoint.json")
    with open(ckpt_path, "wb") as f:
        f.write(b"\xff\xfe corrupted!")

    events = []
    summary = new_job(workdir, shards, events=events).run()
    recovered = next(e for e in events if e["event"] == "checkpoint_recovered")
    lines = RecordSink(os.path.join(workdir, "output.tsv")).lines()
    return {
        "workdir": workdir,
        "recovered_backup": recovered["backup"],
        "rerun_summary": summary,
        "output_lines": len(lines),
        "output_unique": len(set(lines)),
        "idempotent": len(lines) == len(set(lines)) == 12,
    }


def main():
    os.makedirs(ARTIFACTS, exist_ok=True)
    shards = make_shards(3, 4)  # 3 分片 x 4 条 = 12 条记录

    report = {
        "full_run": scenario_full_run(shards),
        "interrupt_resume": scenario_interrupt_resume(shards),
        "duplicate_trigger": scenario_duplicate_trigger(shards),
        "corrupt_checkpoint": scenario_corrupt_checkpoint(shards),
    }

    trace_path = os.path.join(ARTIFACTS, "resume_trace.json")
    with open(trace_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    ir = report["interrupt_resume"]
    dt = report["duplicate_trigger"]
    cc = report["corrupt_checkpoint"]
    print(f"完整跑完: written={report['full_run']['summary']['written']}")
    print(f"中断点:   {ir['interrupt_point']}  续跑起点: {ir['resume_point']}  一致={ir['consistent']}")
    print(f"重复触发: 第二次写入={dt['second_run_summary']['written']} 条, 输出不变={dt['output_unchanged']}")
    print(f"损坏恢复: 备份={os.path.basename(cc['recovered_backup'])}, 幂等={cc['idempotent']}")
    print(f"续跑数据已写入: {trace_path}")


if __name__ == "__main__":
    main()
