"""聚类核心：精确签名分组 + 受约束的软合并 + 关键/噪声簇标注。

聚类算法
========

第一阶段（精确分组）：
    签名 = (失败位置, 错误类型, 信息模板, 阶段)
    签名完全相同的用例一定同根因，直接同簇。

第二阶段（软合并，并查集）：
    两个精确组可合并，当且仅当：
      a) 失败位置相同（同文件同函数同行）；
      b) 错误类型相同；
      c) 信息模板相似度 >= MERGE_THRESHOLD（difflib 比率），
         覆盖“同一根因但错误信息里残留少量变量文本”的情形；
      d) 两边临时标签一致（key 只和 key 合并，noise 只和 noise 合并）——
         这是“关键失败不与噪声失败混簇”的硬保证。

第三阶段（簇标注）：
    - 簇内任意用例命中噪声规则 -> 整个簇标 noise（环境/基建问题）；
    - 否则标 key（产品缺陷嫌疑），并汇总命中规则作为判定依据；
    - 规模最大的 key 簇即“主要根因”（回归挂掉几十条的典型形态）。
"""

import difflib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .features import Features, extract_features
from .models import FailureCase

MERGE_THRESHOLD = 0.75

# 簇性质标签
NATURE_PRODUCT = "product-bug"       # 产品缺陷嫌疑（关键）
NATURE_ENV = "environmental"         # 环境/基建噪声
NATURE_UNKNOWN = "unknown"


@dataclass
class Cluster:
    cluster_id: str
    label: str                       # key / noise
    nature: str
    size: int
    location: str
    error_type: str
    message_template: str
    representative: str              # 代表用例 case_id
    representative_message: str
    members: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "label": self.label,
            "nature": self.nature,
            "size": self.size,
            "location": self.location,
            "error_type": self.error_type,
            "message_template": self.message_template,
            "representative": self.representative,
            "representative_message": self.representative_message,
            "members": list(self.members),
            "reasons": list(self.reasons),
        }


class _DSU:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _signature(feat: Features) -> Tuple[str, str, str, str]:
    return (feat.location, feat.error_type, feat.message_template, feat.phase)


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _pick_representative(cases: List[FailureCase]) -> FailureCase:
    """代表用例：原始错误信息在簇内出现次数最多；并列时取栈最深的；再并列取 case_id 最小的。"""
    freq: Dict[str, int] = {}
    for c in cases:
        freq[c.message] = freq.get(c.message, 0) + 1
    return sorted(
        cases,
        key=lambda c: (-freq[c.message], -len(c.traceback), c.case_id),
    )[0]


def cluster_failures(
    cases: List[FailureCase],
    merge_threshold: float = MERGE_THRESHOLD,
) -> List[Cluster]:
    """对失败用例聚类，返回按规模降序的簇列表。"""
    if not cases:
        return []

    feats = {c.case_id: extract_features(c) for c in cases}

    # 第一阶段：精确签名分组
    groups: Dict[Tuple[str, str, str, str], List[str]] = {}
    for c in cases:
        groups.setdefault(_signature(feats[c.case_id]), []).append(c.case_id)
    group_keys = list(groups.keys())

    # 第二阶段：软合并
    dsu = _DSU(len(group_keys))
    for i in range(len(group_keys)):
        for j in range(i + 1, len(group_keys)):
            loc_i, etype_i, tpl_i, _ = group_keys[i]
            loc_j, etype_j, tpl_j, _ = group_keys[j]
            if loc_i != loc_j or etype_i != etype_j:
                continue
            if not loc_i:  # 无位置信息时不做模糊合并，避免误并
                continue
            label_i = feats[groups[group_keys[i]][0]].provisional_label
            label_j = feats[groups[group_keys[j]][0]].provisional_label
            if label_i != label_j:
                continue
            if _similarity(tpl_i, tpl_j) >= merge_threshold:
                dsu.union(i, j)

    merged: Dict[int, List[str]] = {}
    for idx, key in enumerate(group_keys):
        merged.setdefault(dsu.find(idx), []).extend(groups[key])

    case_by_id = {c.case_id: c for c in cases}

    # 第三阶段：标注 + 组装
    clusters: List[Cluster] = []
    for root, member_ids in merged.items():
        member_ids = sorted(member_ids)
        member_feats = [feats[m] for m in member_ids]
        member_cases = [case_by_id[m] for m in member_ids]

        is_noise = any(f.provisional_label == "noise" for f in member_feats)
        label = "noise" if is_noise else "key"
        nature = NATURE_ENV if is_noise else NATURE_PRODUCT

        reasons: List[str] = []
        seen = set()
        for f in member_feats:
            for r in f.reasons:
                if r not in seen:
                    seen.add(r)
                    reasons.append(r)
        if label == "key" and len(member_ids) >= 3:
            reasons.append(f"K3: 簇规模 {len(member_ids)} 条，疑似同一根因大面积回归")

        rep = _pick_representative(member_cases)
        anchor = feats[rep.case_id]
        clusters.append(
            Cluster(
                cluster_id="",  # 排序后统一编号
                label=label,
                nature=nature,
                size=len(member_ids),
                location=anchor.location,
                error_type=anchor.error_type,
                message_template=anchor.message_template,
                representative=rep.case_id,
                representative_message=rep.message,
                members=member_ids,
                reasons=reasons,
            )
        )

    clusters.sort(key=lambda c: (-c.size, c.representative))
    for idx, c in enumerate(clusters, 1):
        c.cluster_id = f"C{idx}"
    return clusters
