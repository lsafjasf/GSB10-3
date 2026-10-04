"""从原始 pytest 风格文本日志解析失败用例（便于直接接入真实输出，格式不完整时退化为单帧）。"""

import re
from typing import List

from .models import FailureCase, Frame

_FRAME_RE = re.compile(
    r'^\s*File "?(?P<file>[^"]+?)"?, line (?P<line>\d+), in (?P<func>\S+)'
)
_HEADER_RE = re.compile(r"^_{5,}\s*(?P<test>\S+)\s*_{5,}")
_ERROR_RE = re.compile(r"^(?P<etype>[A-Za-z_][\w.]*(?:Error|Exception|Timeout|Fault))\s*:\s*(?P<msg>.*)$")


def parse_pytest_text(text: str) -> List[FailureCase]:
    """解析由 'FAILED/失败块' 组成的文本，块之间用空行或 ==== 分隔。"""
    cases: List[FailureCase] = []
    blocks = re.split(r"\n\s*\n", text)
    for idx, block in enumerate(blocks, 1):
        frames: List[Frame] = []
        test_name = ""
        error_type, message = "Error", ""
        for line in block.splitlines():
            m = _HEADER_RE.match(line)
            if m:
                test_name = m.group("test")
                continue
            m = _FRAME_RE.match(line)
            if m:
                frames.append(
                    Frame(file=m.group("file"), function=m.group("func"),
                          line=int(m.group("line")))
                )
                continue
            m = _ERROR_RE.match(line.strip())
            if m:
                error_type, message = m.group("etype"), m.group("msg")
        if frames or message:
            cases.append(
                FailureCase(
                    case_id=f"parsed-{idx}",
                    test=test_name or f"parsed-{idx}",
                    error_type=error_type,
                    message=message,
                    traceback=frames,
                )
            )
    return cases
