"""对拍：验证「全量基线 + 增量」与「同一时刻的全量」严格等价。"""
from __future__ import annotations

import json
from typing import Dict, Optional


def _read_header(f) -> dict:
    return json.loads(f.readline())


def load_state(full_path: str) -> Dict[str, dict]:
    """读取全量导出，构造 {id: {version, payload}} 状态。"""
    state: Dict[str, dict] = {}
    with open(full_path, "r", encoding="utf-8") as f:
        header = _read_header(f)
        if header["type"] != "full":
            raise ValueError(f"{full_path} 不是全量导出文件")
        for line in f:
            rec = json.loads(line)
            if not rec["deleted"]:
                state[rec["id"]] = {
                    "version": rec["version"], "payload": rec["payload"]}
    return state


def apply_incremental(state: Dict[str, dict], inc_path: str) -> dict:
    """把增量导出幂等地应用到状态上（按 version 最后写入胜出）。"""
    with open(inc_path, "r", encoding="utf-8") as f:
        header = _read_header(f)
        if header["type"] != "incremental":
            raise ValueError(f"{inc_path} 不是增量导出文件")
        for line in f:
            rec = json.loads(line)
            if rec["deleted"]:
                state.pop(rec["id"], None)
            else:
                old = state.get(rec["id"])
                if old is None or rec["version"] >= old["version"]:
                    state[rec["id"]] = {
                        "version": rec["version"], "payload": rec["payload"]}
    return header


def diff_states(actual: Dict[str, dict], expected: Dict[str, dict],
                limit: int = 100) -> list:
    """actual = 基线应用增量后的状态；expected = 同一时刻的全量。"""
    problems = []
    for rid in sorted(set(actual) | set(expected)):
        if rid not in actual:
            problems.append((rid, "缺失（增量基线没有，全量有）"))
        elif rid not in expected:
            problems.append((rid, "多余（增量基线有，全量没有）"))
        elif actual[rid]["payload"] != expected[rid]["payload"]:
            problems.append((rid, f"内容不一致: {actual[rid]} != {expected[rid]}"))
        elif actual[rid]["version"] != expected[rid]["version"]:
            problems.append((rid, f"版本不一致: {actual[rid]} != {expected[rid]}"))
        if len(problems) >= limit:
            break
    return problems


def verify(full_path: str, inc_path: str,
           expected_full_path: Optional[str] = None,
           out_path: Optional[str] = None) -> dict:
    """对拍入口。expected_full_path 省略时与 full_path 自身比对。

    full_path: 基线全量导出；inc_path: 增量导出；
    expected_full_path: 变更后的全量导出（同一时刻）。
    """
    state = load_state(full_path)
    header = apply_incremental(state, inc_path)
    expected = load_state(expected_full_path or full_path)
    problems = diff_states(state, expected)
    if out_path is not None:
        with open(out_path, "w", encoding="utf-8") as f:
            for rid, msg in problems:
                f.write(f"{rid}\t{msg}\n")
    return {
        "base_full": full_path,
        "incremental": inc_path,
        "expected_full": expected_full_path or full_path,
        "to_version": header["to_version"],
        "expected_records": len(expected),
        "actual_records": len(state),
        "differences": len(problems),
        "problems": problems,
    }
