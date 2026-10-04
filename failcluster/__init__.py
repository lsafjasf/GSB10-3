"""failcluster：回归失败归因聚类库（仅标准库）。"""

from .cluster import Cluster, cluster_failures
from .features import extract_features, normalize_message
from .models import FailureCase, Frame
from .parser import parse_pytest_text
from .report import Report, build_report

__all__ = [
    "Cluster",
    "FailureCase",
    "Frame",
    "Report",
    "build_report",
    "cluster_failures",
    "extract_features",
    "normalize_message",
    "parse_pytest_text",
    "cluster_from_dicts",
]

__version__ = "0.1.0"


def cluster_from_dicts(records, merge_threshold=0.75):
    """便捷入口：dict 列表 -> Report。"""
    cases = [FailureCase.from_dict(r) for r in records]
    clusters = cluster_failures(cases, merge_threshold=merge_threshold)
    return build_report(cases, clusters)
