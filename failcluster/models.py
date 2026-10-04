"""失败用例与调用栈帧的数据模型（仅标准库）。"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Frame:
    """调用栈中的一帧。"""

    file: str
    function: str
    line: Optional[int] = None
    kind: str = "product"  # product / stdlib / thirdparty / framework


@dataclass
class FailureCase:
    """一条失败用例记录。

    traceback 为从外到内排列的 Frame 列表（最深一帧在最后）。
    """

    case_id: str
    test: str
    error_type: str
    message: str
    traceback: List[Frame] = field(default_factory=list)
    phase: Optional[str] = None  # setup / call / teardown / None(自动推断)

    @classmethod
    def from_dict(cls, data: dict) -> "FailureCase":
        frames = []
        for raw in data.get("traceback", []) or []:
            if isinstance(raw, str):
                # 允许 "path/to/file.py:function:123" 的简写
                parts = raw.split(":")
                file = parts[0]
                line = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
                func = parts[1] if len(parts) > 1 else ""
                frames.append(Frame(file=file, function=func, line=line))
            else:
                frames.append(
                    Frame(
                        file=str(raw["file"]),
                        function=str(raw.get("function", "")),
                        line=raw.get("line"),
                        kind=str(raw.get("kind", "product")),
                    )
                )
        return cls(
            case_id=str(data["case_id"]),
            test=str(data.get("test", data["case_id"])),
            error_type=str(data.get("error_type", "Error")),
            message=str(data.get("message", "")),
            traceback=frames,
            phase=data.get("phase"),
        )
