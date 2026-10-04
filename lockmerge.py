#!/usr/bin/env python3
"""lockmerge: 锁文件三方合并工具（仅标准库）。

按依赖项（而不是按行）做三方合并：
  - 只有一侧改动 -> 采用改动侧
  - 两侧相同改动 -> 采用任一侧（结果一致）
  - 两侧不同改动 -> 报告冲突，绝不静默选边
  - 一侧删除、另一侧未动 -> 采用删除
  - 一侧删除、另一侧改动 -> 冲突（delete vs modify）

锁文件格式（JSON）：
{
  "packages":    {"<name>": "<version>", ...},
  "constraints": {"<name>": ">=1.0.0, <2.0.0", ...}   // 可选
}

退出码：0 = 合并干净且校验通过；1 = 存在冲突；3 = 合并干净但可满足性校验失败；2 = 用法/IO 错误。
"""

from __future__ import annotations

import argparse
import json
import re
import sys

DELETED = None  # 哨兵：该侧已删除此依赖


# ---------------------------------------------------------------- 版本与约束

_VERSION_RE = re.compile(r"^\d+(?:\.\d+){0,3}(?:[-+][0-9A-Za-z.-]+)?$")


def parse_version(text):
    """把 '1.2.3' 解析为可比较的元组 (1, 2, 3, 0)。非法版本抛 ValueError。"""
    if not isinstance(text, str) or not _VERSION_RE.match(text):
        raise ValueError("非法版本号: %r" % (text,))
    core = re.split(r"[-+]", text, maxsplit=1)[0]
    parts = [int(p) for p in core.split(".")]
    parts += [0] * (4 - len(parts))
    return tuple(parts)


def _cmp(op, left, right):
    return {
        "==": left == right,
        "!=": left != right,
        ">=": left >= right,
        "<=": left <= right,
        ">": left > right,
        "<": left < right,
    }[op]


def satisfies(version, spec):
    """判断 version 是否满足约束 spec。

    支持：'1.2.3'（精确）、'>=1.2, <2.0'（逗号分隔的比较器，全部需满足）、
    '^1.2.3'（兼容到下一个主版本）、'~1.2.3'（兼容到下一个次版本）、'*'。
    """
    spec = (spec or "").strip()
    if spec in ("", "*"):
        return True
    ver = parse_version(version)
    for clause in spec.split(","):
        clause = clause.strip()
        if not clause:
            continue
        if clause.startswith("^"):
            base = parse_version(clause[1:])
            upper = (base[0] + 1, 0, 0, 0) if base[0] else (0, base[1] + 1, 0, 0)
            if not (base <= ver < upper):
                return False
        elif clause.startswith("~"):
            base = parse_version(clause[1:])
            if not (base <= ver < (base[0], base[1] + 1, 0, 0)):
                return False
        else:
            m = re.match(r"^(==|!=|>=|<=|>|<)?\s*(\S+)$", clause)
            if not m:
                raise ValueError("非法约束: %r" % (clause,))
            op = m.group(1) or "=="
            if not _cmp(op, ver, parse_version(m.group(2))):
                return False
    return True


# ---------------------------------------------------------------- 锁文件 IO

def load_lockfile(path):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("packages"), dict):
        raise ValueError("%s: 缺少对象类型的 'packages' 字段" % path)
    constraints = data.get("constraints", {})
    if not isinstance(constraints, dict):
        raise ValueError("%s: 'constraints' 必须是对象" % path)
    packages = {}
    for name, ver in data["packages"].items():
        if isinstance(ver, dict):  # 兼容 {"name": {"version": "..."}} 形态
            ver = ver.get("version")
        parse_version(ver)  # 提前校验，坏数据直接报错
        packages[name] = ver
    return packages, constraints


# ---------------------------------------------------------------- 三方合并

class Conflict:
    """一处依赖级冲突。"""

    def __init__(self, name, base, ours, theirs, reason):
        self.name = name
        self.base = base
        self.ours = ours
        self.theirs = theirs
        self.reason = reason

    def to_dict(self, ours_name, theirs_name):
        def side(value, source):
            return {"source": source, "version": "（已删除）" if value is DELETED else value}

        return {
            "dependency": self.name,
            "base": "（不存在）" if self.base is DELETED else self.base,
            "ours": side(self.ours, ours_name),
            "theirs": side(self.theirs, theirs_name),
            "reason": self.reason,
        }


def merge_packages(base, ours, theirs):
    """按依赖项做三方合并。返回 (merged, conflicts)。

    merged 为 None 表示存在冲突，此时 conflicts 非空。
    """
    merged = {}
    conflicts = []
    for name in sorted(set(base) | set(ours) | set(theirs)):
        b = base.get(name, DELETED)
        o = ours.get(name, DELETED)
        t = theirs.get(name, DELETED)

        if o == t:
            # 两侧一致：包含“都未改”“相同改动”“都删除”“同时新增同版本”
            if o is not DELETED:
                merged[name] = o
        elif b == o:
            # 只有 theirs 侧改动（含删除/新增）
            if t is not DELETED:
                merged[name] = t
        elif b == t:
            # 只有 ours 侧改动（含删除/新增）
            if o is not DELETED:
                merged[name] = o
        else:
            # 两侧都改了且不一样：必须报冲突
            if o is DELETED:
                reason = "ours 删除了该依赖，但 theirs 将其修改为 %s" % t
            elif t is DELETED:
                reason = "theirs 删除了该依赖，但 ours 将其修改为 %s" % o
            elif b is DELETED:
                reason = "两侧分别新增了不同版本（%s vs %s）" % (o, t)
            else:
                reason = "两侧分别修改为不同版本（%s vs %s，base 为 %s）" % (o, t, b)
            conflicts.append(Conflict(name, b, o, t, reason))
    if conflicts:
        return None, conflicts
    return merged, []


# ---------------------------------------------------------------- 可满足性校验

def validate(merged, constraints):
    """校验合并结果是可满足的版本集合。返回 (ok, report)。

    检查项：
      1. 每个锁定版本满足其声明的约束；
      2. 约束没有指向不存在的依赖（悬空约束）。
    """
    problems = []
    for name in sorted(merged):
        spec = constraints.get(name)
        if spec and not satisfies(merged[name], spec):
            problems.append(
                "依赖 %s: 锁定版本 %s 不满足约束 '%s'" % (name, merged[name], spec)
            )
    for name in sorted(set(constraints) - set(merged)):
        problems.append("约束指向了不存在的依赖: %s (%s)" % (name, constraints[name]))
    report = {
        "satisfiable": not problems,
        "checked_packages": len(merged),
        "checked_constraints": len([n for n in merged if n in constraints]),
        "problems": problems,
    }
    return not problems, report


# ---------------------------------------------------------------- 报告输出

def format_conflict_report(conflicts, ours_name, theirs_name):
    lines = ["发现 %d 处依赖冲突（按依赖项合并，未做任何自动选边）：" % len(conflicts), ""]
    for c in conflicts:
        d = c.to_dict(ours_name, theirs_name)
        lines.append("  依赖: %s" % d["dependency"])
        lines.append("    base:   %s" % d["base"])
        lines.append("    ours:   %s  (来源: %s)" % (d["ours"]["version"], d["ours"]["source"]))
        lines.append("    theirs: %s  (来源: %s)" % (d["theirs"]["version"], d["theirs"]["source"]))
        lines.append("    原因:   %s" % d["reason"])
        lines.append("")
    lines.append("请人工选定版本后重跑合并。")
    return "\n".join(lines)


def format_validation_report(report):
    if report["satisfiable"]:
        return (
            "可满足性校验通过：%d 个依赖、%d 条约束全部满足，冲突数 0，"
            "合并结果是可满足的版本集合。"
            % (report["checked_packages"], report["checked_constraints"])
        )
    lines = ["可满足性校验失败："]
    lines += ["  - " + p for p in report["problems"]]
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="lockmerge",
        description="锁文件三方合并：lockmerge BASE OURS THEIRS",
    )
    parser.add_argument("base", help="共同祖先锁文件")
    parser.add_argument("ours", help="我方锁文件")
    parser.add_argument("theirs", help="对方锁文件")
    parser.add_argument("-o", "--output", help="合并结果输出路径（默认标准输出）")
    parser.add_argument("--ours-name", default="ours", help="我方来源名（用于冲突报告）")
    parser.add_argument("--theirs-name", default="theirs", help="对方来源名（用于冲突报告）")
    parser.add_argument("--json", action="store_true", help="冲突/校验报告以 JSON 输出")
    args = parser.parse_args(argv)

    try:
        base_pkgs, base_cons = load_lockfile(args.base)
        ours_pkgs, ours_cons = load_lockfile(args.ours)
        theirs_pkgs, theirs_cons = load_lockfile(args.theirs)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("错误: %s" % exc, file=sys.stderr)
        return 2

    merged, conflicts = merge_packages(base_pkgs, ours_pkgs, theirs_pkgs)
    if conflicts:
        if args.json:
            payload = {
                "status": "conflict",
                "conflicts": [c.to_dict(args.ours_name, args.theirs_name) for c in conflicts],
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(format_conflict_report(conflicts, args.ours_name, args.theirs_name))
        return 1

    # 约束同样按依赖项做三方合并：两侧一致或单侧改动则采用；
    # 两侧都改且不同时取交集（两个约束必须同时成立），
    # 交集不可满足时由 validate 在校验阶段暴露，而不是静默选边。
    constraints = {}
    for name in set(base_cons) | set(ours_cons) | set(theirs_cons):
        b, o, t = base_cons.get(name), ours_cons.get(name), theirs_cons.get(name)
        if o == t:
            chosen = o
        elif b == o:
            chosen = t
        elif b == t:
            chosen = o
        else:
            chosen = "%s, %s" % (o, t)
        if chosen is not None:
            constraints[name] = chosen

    ok, report = validate(merged, constraints)
    result = {"packages": merged, "constraints": constraints}
    out = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
    else:
        sys.stdout.write(out)

    if args.json:
        print(json.dumps({"status": "merged", "validation": report}, ensure_ascii=False, indent=2))
    else:
        print(format_validation_report(report), file=sys.stderr if not ok else sys.stdout)
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
