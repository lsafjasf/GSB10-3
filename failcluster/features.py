"""特征提取：失败位置、错误特征、阶段，以及关键/噪声判定依据。

特征提取方式（文档化的核心规则）
================================

1. 失败位置（location）
   - 在调用栈中自底向上（从最深帧开始）找到第一帧“产品代码”帧；
     过滤掉标准库（stdlib）、第三方库（thirdparty）、测试框架（framework，
     如 pytest/unittest 自身）以及测试基建文件（conftest.py、tests/ 目录、
     以 test_ 开头或 _test.py 结尾的文件）。
   - 路径归一化：统一分隔符、去掉开头的 ./，只保留路径末尾最多 3 段，
     避免不同机器上的绝对路径差异导致同根因被拆开。
   - 位置 = "路径:函数:行号"；行号缺失时省略行号。

2. 错误特征（error signature）
   - error_type：异常/断言类型名，原样保留。
   - message_template：错误信息模板，依次把 ISO 时间戳、IP、UUID、
     十六进制串、引号包裹的字符串、数字替换为占位符 <TS>/<IP>/<UUID>/
     <HEX>/<STR>/<NUM>，并压缩空白。这样同一断言在不同数据下的失败
     （如 “expected 200 got 500” 与 “expected 200 got 404”）属于同一模板；
     防误并靠的是“同位置 + 同错误类型”的硬约束，以及模板中保留的
     字面文本骨架（如 “expected ... got ...” 与 “missing field ...” 不同）。

3. 阶段（phase）
   - 优先取记录自带的 phase；否则根据位置帧函数名推断：
     以 test_ 开头 -> call；含 setup/teardown/fixture 等 -> setup/teardown。

4. 关键 / 噪声判定依据（provisional label，聚类后还会按簇复核）
   - 噪声（noise）规则，命中任意一条即判噪声：
     N1 阶段为 setup/teardown（夹具/环境准备失败）；
     N2 错误信息命中环境类关键词（超时、连接失败、OOM、限流、5xx 等）；
     N3 位置帧落在 tests/ 目录或 conftest.py（测试基建自身问题）。
   - 关键强信号（key-strong）规则，命中任意一条即判关键：
     K1 断言类失败（AssertionError/Expect*/Verify* 等）且位置在产品代码；
     K2 非断言失败且位置在产品代码（产品代码抛出的异常）。
   - 兜底：无法判断时，单条且非噪声的簇按“疑似关键（待人工确认）”处理，
     避免关键失败被漏掉。
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .models import FailureCase, Frame

# ---------------------------------------------------------------------------
# 路径 / 帧分类
# ---------------------------------------------------------------------------

_FRAMEWORK_PATH_RE = re.compile(
    r"(^|/)(pytest|_pytest|pluggy|unittest|nose|nose2|tox)(/|\.py|$)"
)
_TEST_INFRA_PATH_RE = re.compile(
    r"(^|/)(conftest\.py|tests?/|testing/|test_utils?\.py)"
)
_TEST_FILE_RE = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.py)$")
_STDLIB_PREFIXES = ("lib/python", "python3.", "python2.")
_THIRDPARTY_MARKERS = ("site-packages/", "dist-packages/", "node_modules/")

_SETUP_RE = re.compile(r"(setup|set_up|before|fixture|prepare|arrange)", re.I)
_TEARDOWN_RE = re.compile(r"(teardown|tear_down|after|cleanup|dispose)", re.I)
_TEST_FUNC_RE = re.compile(r"^test_", re.I)

# ---------------------------------------------------------------------------
# 错误信息归一化
# ---------------------------------------------------------------------------

_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")
_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_HEX_RE = re.compile(r"\b0x[0-9a-fA-F]+\b")
_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
_NUM_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
_WS_RE = re.compile(r"\s+")

# ---------------------------------------------------------------------------
# 噪声 / 关键关键词
# ---------------------------------------------------------------------------

_INFRA_MSG_RE = re.compile(
    r"(timed?\s*out|timeout|connection\s*(refused|reset|aborted|closed|error)"
    r"|econnrefused|econnreset|etimedout|socket\s*error|network\s*(error|unreachable)"
    r"|out\s*of\s*memory|oom|no\s*space\s*left|disk\s*full"
    r"|rate\s*limit|too\s*many\s*requests|\b503\b|\b502\b|\b504\b"
    r"|service\s*unavailable|bad\s*gateway|gateway\s*time"
    r"|resource\s*exhausted|temporarily\s*unavailable| flaky )",
    re.I,
)
_ASSERT_TYPE_RE = re.compile(r"(assert|expect|verify|check|should)", re.I)


@dataclass
class Features:
    case_id: str
    location: str                      # "path:func:line" 或 ""
    location_kind: str                 # product / stdlib / thirdparty / framework / none
    error_type: str
    message_template: str
    phase: str                         # setup / call / teardown / unknown
    provisional_label: str             # key / noise / unknown
    reasons: List[str] = field(default_factory=list)


def normalize_path(path: str, keep: int = 3) -> str:
    """路径归一化：统一分隔符，只保留末尾 keep 段。"""
    path = (path or "").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    parts = [p for p in path.split("/") if p and p != "."]
    if not parts:
        return ""
    return "/".join(parts[-keep:])


def classify_frame(frame: Frame) -> str:
    """给未标注 kind 的帧分类。"""
    if frame.kind in ("stdlib", "thirdparty", "framework"):
        return frame.kind
    p = normalize_path(frame.file, keep=6).lower()
    if _FRAMEWORK_PATH_RE.search(p):
        return "framework"
    if any(m in p for m in _THIRDPARTY_MARKERS):
        return "thirdparty"
    if any(m in p for m in _STDLIB_PREFIXES):
        return "stdlib"
    return "product"


def _is_test_infra(path: str) -> bool:
    p = normalize_path(path, keep=6).lower()
    return bool(_TEST_INFRA_PATH_RE.search(p) or _TEST_FILE_RE.search(p))


def find_location(traceback: List[Frame]) -> Tuple[str, str]:
    """自底向上找第一帧产品代码帧，返回 (location, kind)。

    找不到产品代码帧时退化为最深一帧非 framework 帧；再不行返回 ("", "none")。
    """
    fallback: Optional[Frame] = None
    for frame in reversed(traceback):
        kind = classify_frame(frame)
        if kind == "product" and not _is_test_infra(frame.file):
            loc = normalize_path(frame.file)
            if frame.function:
                loc += ":" + frame.function
            if frame.line is not None:
                loc += ":" + str(frame.line)
            return loc, "product"
        if kind != "framework" and fallback is None:
            fallback = frame
    if fallback is not None:
        kind = classify_frame(fallback)
        loc = normalize_path(fallback.file)
        if fallback.function:
            loc += ":" + fallback.function
        if fallback.line is not None:
            loc += ":" + str(fallback.line)
        return loc, kind
    return "", "none"


def normalize_message(message: str) -> str:
    """错误信息 -> 模板（替换变量部分为占位符）。"""
    text = message or ""
    text = _TS_RE.sub("<TS>", text)
    text = _UUID_RE.sub("<UUID>", text)
    text = _IP_RE.sub("<IP>", text)
    text = _HEX_RE.sub("<HEX>", text)
    text = _QUOTED_RE.sub("<STR>", text)
    text = _NUM_RE.sub("<NUM>", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def infer_phase(case: FailureCase, location_func: str) -> str:
    if case.phase in ("setup", "call", "teardown"):
        return case.phase
    func = location_func or ""
    if _TEARDOWN_RE.search(func):
        return "teardown"
    if _SETUP_RE.search(func):
        return "setup"
    if _TEST_FUNC_RE.search(func) or case.test:
        return "call"
    return "unknown"


def extract_features(case: FailureCase) -> Features:
    location, kind = find_location(case.traceback)
    # 位置帧函数名（用于阶段推断）：取位置对应的帧函数
    location_func = ""
    if location:
        parts = location.split(":")
        if len(parts) >= 2:
            location_func = parts[1]
    phase = infer_phase(case, location_func)
    template = normalize_message(case.message)

    reasons: List[str] = []
    label = "unknown"

    if phase in ("setup", "teardown"):
        label = "noise"
        reasons.append(f"N1: 失败发生在 {phase} 阶段（夹具/环境准备），非被测代码")
    if _INFRA_MSG_RE.search(case.message or ""):
        label = "noise"
        reasons.append("N2: 错误信息命中环境类关键词（超时/连接/资源耗尽/5xx 等）")
    if location and _is_test_infra(location.split(":")[0]):
        label = "noise"
        reasons.append("N3: 失败位置在测试基建文件（tests/ 或 conftest.py）")

    if label != "noise":
        if _ASSERT_TYPE_RE.search(case.error_type) and kind == "product":
            label = "key"
            reasons.append("K1: 断言类失败且失败位置在产品代码")
        elif kind == "product":
            label = "key"
            reasons.append("K2: 产品代码抛出异常（非断言）")
        else:
            reasons.append("K0: 无明确信号，按疑似关键处理（待人工确认）")

    return Features(
        case_id=case.case_id,
        location=location,
        location_kind=kind,
        error_type=case.error_type,
        message_template=template,
        phase=phase,
        provisional_label=label,
        reasons=reasons,
    )
