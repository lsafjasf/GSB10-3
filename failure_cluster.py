"""
failure_cluster.py — 回归测试失败归因聚类库（仅标准库，Python 3.8+）

输入：失败用例列表（每条含 test_name / error_type / message / stack）。
输出：簇列表，每簇含代表用例、簇内数量、特征签名、关键失败标记。

聚类依据（特征提取）：
  1. 失败位置 location：取堆栈最深的“业务帧”（排除测试框架帧），
     定位到 文件 + 函数（忽略行号，行号会因相邻改动漂移，函数才是稳定根因点）。
  2. 错误特征 signature：error_type + 归一化后的 message 模板。
     归一化会抹平运行期噪声：
       0x7f12.. 内存地址、数字、UUID、引号串、绝对路径、时间戳、空白差异。
  3. 聚类键 = (error_type, message 模板, 位置文件, 位置函数)；
     键相同即同一根因簇。这是确定性的等价聚类，不依赖随机性算法。

关键失败隔离（绝不与噪声失败混入同簇）：
  命中以下任一规则即为关键失败，强制单独成簇（singleton，isolated=True），
  并在簇上记录命中依据 reasons：
    R1 用例显式标注 critical=True / severity="critical" / 标签含 critical；
    R2 error_type 属于关键错误类型（如 SecurityError、DataCorruptionError、
       SegmentationFault、OutOfMemoryError，可用参数扩展）；
    R3 用例名或标签命中关键模式（如 payment、security、auth、permission，
       表示资金/鉴权链路，必须被人工逐条确认，不能靠聚类消音）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_FRAMEWORK_PATTERNS = (
    "unittest", "pytest", "_pytest", "pluggy", "mock",
    "/site-packages/", "/dist-packages/", "conftest.py",
)

DEFAULT_CRITICAL_ERROR_TYPES = frozenset({
    "SecurityError", "DataCorruptionError", "SegmentationFault",
    "OutOfMemoryError", "FatalError", "PermissionDeniedError",
})

DEFAULT_CRITICAL_NAME_PATTERNS = (
    "payment", "billing", "refund", "security", "auth", "login",
    "permission", "acl", "rbac",
)

# Python 风格: File "/x/y.py", line 10, in func
_PY_FRAME_RE = re.compile(
    r'File\s+"(?P<file>[^"]+)", line\s+(?P<line>\d+)(?:, in\s+(?P<func>\S+))?'
)
# JS/Node 风格: at func (/x/y.js:10:5) 或 at /x/y.js:10:5
_JS_FRAME_RE = re.compile(
    r'\bat\s+(?:(?P<func>[^\s()]+)\s+\()?(?P<file>\S+?):(?P<line>\d+):(?P<col>\d+)\)?'
)


@dataclass
class Frame:
    file: str
    line: Optional[int]
    func: str = "<module>"


@dataclass
class Failure:
    test_name: str
    error_type: str
    message: str
    stack: str = ""
    critical: bool = False
    tags: Tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Failure":
        if "test_name" not in d:
            raise ValueError("failure item missing 'test_name'")
        stack = d.get("stack", "")
        if isinstance(stack, (list, tuple)):
            stack = "\n".join(str(x) for x in stack)
        return cls(
            test_name=str(d["test_name"]),
            error_type=str(d.get("error_type", "Error")),
            message=str(d.get("message", "")),
            stack=stack or "",
            critical=bool(d.get("critical", False))
            or str(d.get("severity", "")).lower() == "critical",
            tags=tuple(str(t) for t in d.get("tags", ())),
        )


@dataclass
class Cluster:
    cluster_id: str
    key: Tuple[str, str, str, str]
    error_type: str
    message_template: str
    location_file: str
    location_func: str
    members: List[Failure]
    representative: Failure
    isolated: bool = False
    critical: bool = False
    reasons: List[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.members)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "size": self.size,
            "error_type": self.error_type,
            "message_template": self.message_template,
            "location": f"{self.location_file}::{self.location_func}",
            "signature": "%s|%s@%s::%s" % (
                self.error_type, self.message_template,
                self.location_file, self.location_func),
            "isolated_critical": self.isolated,
            "critical": self.critical,
            "reasons": self.reasons,
            "representative": _failure_brief(self.representative),
            "members": [m.test_name for m in self.members],
        }


def _failure_brief(f: Failure) -> Dict[str, Any]:
    return {
        "test_name": f.test_name,
        "error_type": f.error_type,
        "message": f.message,
        "tags": list(f.tags),
    }


# ------------------------- 特征提取 -------------------------

def normalize_message(message: str) -> str:
    """把错误信息归一化为模板，抹平运行期噪声。"""
    s = message.strip()
    s = re.sub(r"0x[0-9a-fA-F]+", "<ADDR>", s)
    s = re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
               r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "<UUID>", s)
    s = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?\b",
               "<TIMESTAMP>", s)
    # 绝对路径 / Windows 路径
    s = re.sub(r"(?:[A-Za-z]:)?[\\/](?:[^:'\"\s|]+[\\/])+[^:'\"\s|]+",
               "<PATH>", s)
    # 引号包裹的动态值（'abc'、"xxx"、`yyy`）
    s = re.sub(r"'[^']*'", "'<V>'", s)
    s = re.sub(r'"[^"]*"', '"<V>"', s)
    s = re.sub(r"`[^`]*`", "`<V>`", s)
    # 纯数字（含负数、小数、带单位），单独的数字 token
    s = re.sub(r"(?<![A-Za-z_])-?\d+(?:\.\d+)?", "<N>", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def parse_frames(stack: str) -> List[Frame]:
    """从原始堆栈文本解析帧（支持 Python 与 Node 两种常见格式）。

    返回列表统一按调用顺序排列（外层在前、最深帧在最后）：
    Python traceback 本身就是这个顺序；Node 堆栈最深帧在前，需反转。
    """
    py_frames: List[Frame] = []
    js_frames: List[Frame] = []
    for line in stack.splitlines():
        m = _PY_FRAME_RE.search(line)
        if m:
            py_frames.append(Frame(m.group("file"),
                                   int(m.group("line")),
                                   m.group("func") or "<module>"))
            continue
        m = _JS_FRAME_RE.search(line)
        if m:
            js_frames.append(Frame(m.group("file"),
                                   int(m.group("line")),
                                   m.group("func") or "<anonymous>"))
    if py_frames:
        return py_frames
    js_frames.reverse()
    return js_frames


def locate_failure(stack: str,
                   framework_patterns: Sequence[str]
                   = DEFAULT_FRAMEWORK_PATTERNS) -> Tuple[str, str]:
    """返回 (文件, 函数)：取最深的非框架帧；解析不到时返回 ('<unknown>', '<unknown>')。"""
    frames = parse_frames(stack)
    for fr in reversed(frames):
        if any(p in fr.file for p in framework_patterns):
            continue
        return fr.file, fr.func
    if frames:
        return frames[-1].file, frames[-1].func
    return "<unknown>", "<unknown>"


def critical_reasons(
        f: Failure,
        critical_error_types: Iterable[str] = DEFAULT_CRITICAL_ERROR_TYPES,
        critical_name_patterns: Sequence[str] = DEFAULT_CRITICAL_NAME_PATTERNS) -> List[str]:
    reasons: List[str] = []
    if f.critical or "critical" in [t.lower() for t in f.tags]:
        reasons.append("R1:用例显式标注为关键失败")
    if f.error_type in critical_error_types:
        reasons.append("R2:错误类型属于关键错误集合(%s)" % f.error_type)
    haystack = (f.test_name + " " + " ".join(f.tags)).lower()
    for p in critical_name_patterns:
        if p in haystack:
            reasons.append("R3:用例名/标签命中关键链路模式(%r)" % p)
            break
    return reasons


# ------------------------- 聚类 -------------------------

def cluster_failures(
        failures: Sequence[Failure],
        critical_error_types: Iterable[str] = DEFAULT_CRITICAL_ERROR_TYPES,
        critical_name_patterns: Sequence[str] = DEFAULT_CRITICAL_NAME_PATTERNS,
        framework_patterns: Sequence[str] = DEFAULT_FRAMEWORK_PATTERNS,
) -> List[Cluster]:
    buckets: Dict[Tuple[str, str, str, str], List[Failure]] = {}
    criticals: List[Tuple[Failure, List[str]]] = []

    for f in failures:
        reasons = critical_reasons(f, critical_error_types,
                                   critical_name_patterns)
        if reasons:
            # 关键失败：强制 singleton，哪怕与其他失败特征完全一致也不合并
            criticals.append((f, reasons))
            continue
        loc_file, loc_func = locate_failure(f.stack, framework_patterns)
        key = (f.error_type, normalize_message(f.message), loc_file, loc_func)
        buckets.setdefault(key, []).append(f)

    clusters: List[Cluster] = []
    for key, members in buckets.items():
        rep = _pick_representative(members)
        error_type, template, loc_file, loc_func = key
        clusters.append(Cluster(
            cluster_id="", key=key, error_type=error_type,
            message_template=template, location_file=loc_file,
            location_func=loc_func, members=members, representative=rep))

    for f, reasons in criticals:
        loc_file, loc_func = locate_failure(f.stack, framework_patterns)
        clusters.append(Cluster(
            cluster_id="", key=(), error_type=f.error_type,
            message_template=normalize_message(f.message),
            location_file=loc_file, location_func=loc_func,
            members=[f], representative=f, isolated=True,
            critical=True, reasons=reasons))

    # 排序：关键失败优先，其次按簇规模降序，便于人工先看重点/大头
    clusters.sort(key=lambda c: (not c.isolated, -c.size, c.error_type))
    for i, c in enumerate(clusters, 1):
        c.cluster_id = "C%02d" % i
    return clusters


def _pick_representative(members: List[Failure]) -> Failure:
    """代表用例：取簇内出现次数最多的原始 message（最典型），并列取用例名字典序最小（稳定）。"""
    counts: Dict[str, int] = {}
    for m in members:
        counts[m.message] = counts.get(m.message, 0) + 1
    top = max(counts.values())
    candidates = [m for m in members if counts[m.message] == top]
    return sorted(candidates, key=lambda m: m.test_name)[0]


def load_failures(path: str) -> List[Failure]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        data = data.get("failures", [])
    return [Failure.from_dict(d) for d in data]


def render_markdown(clusters: Sequence[Cluster]) -> str:
    total = sum(c.size for c in clusters)
    lines = [
        "# 失败归因聚类报告",
        "",
        "- 失败用例总数: %d" % total,
        "- 聚类簇数: %d" % len(clusters),
        "- 关键失败（强制单独成簇）: %d" % sum(1 for c in clusters if c.isolated),
        "",
        "| 簇ID | 数量 | 关键 | 错误类型 | 失败位置 | 消息模板 | 代表用例 |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in clusters:
        lines.append("| %s | %d | %s | %s | %s::%s | %s | %s |" % (
            c.cluster_id, c.size, "★" if c.isolated else "",
            c.error_type, c.location_file, c.location_func,
            c.message_template.replace("|", "\\|"),
            c.representative.test_name))
    lines.append("")
    for c in clusters:
        lines.append("## %s%s — %s（%d 条）" % (
            c.cluster_id, " [关键-隔离]" if c.isolated else "",
            c.error_type, c.size))
        lines.append("")
        lines.append("- 失败位置: `%s::%s`" % (c.location_file, c.location_func))
        lines.append("- 消息模板: `%s`" % c.message_template)
        lines.append("- 代表用例: `%s` — %s" % (
            c.representative.test_name, c.representative.message))
        if c.isolated:
            lines.append("- 隔离依据: " + "；".join(c.reasons))
        lines.append("- 簇内用例: " + ", ".join(
            "`%s`" % m.test_name for m in c.members))
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="回归失败归因聚类")
    parser.add_argument("input", help="失败用例 JSON 文件")
    parser.add_argument("--json-out", help="输出聚类结果 JSON")
    parser.add_argument("--md-out", help="输出聚类报告 Markdown")
    args = parser.parse_args(argv)

    failures = load_failures(args.input)
    clusters = cluster_failures(failures)
    result = {
        "total_failures": len(failures),
        "cluster_count": len(clusters),
        "critical_isolated": sum(1 for c in clusters if c.isolated),
        "clusters": [c.to_dict() for c in clusters],
    }
    out = json.dumps(result, ensure_ascii=False, indent=2)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            fh.write(out + "\n")
    else:
        print(out)
    if args.md_out:
        with open(args.md_out, "w", encoding="utf-8") as fh:
            fh.write(render_markdown(clusters))
    return 0


if __name__ == "__main__":
    sys.exit(main())
