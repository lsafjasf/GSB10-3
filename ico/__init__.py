"""ICO 图标容器读写库（纯标准库，不依赖任何图像库）。"""
from .icoformat import (
    IconFile,
    IconImage,
    RankItem,
    IconFormatError,
    pick_best,
    rank,
)

__all__ = [
    "IconFile",
    "IconImage",
    "RankItem",
    "IconFormatError",
    "pick_best",
    "rank",
]
