"""聚类结果组装与渲染（dict / JSON / 纯文本）。"""

import json
from dataclasses import dataclass
from typing import Dict, List

from .cluster import Cluster
from .models import FailureCase


@dataclass
class Report:
    total_failures: int
    cluster_count: int
    key_clusters: int
    noise_clusters: int
    primary_root_cause: str
    clusters: List[Cluster]
    representatives: List[dict]
    cases: List[FailureCase]

    def to_dict(self) -> dict:
        return {
            "total_failures": self.total_failures,
            "cluster_count": self.cluster_count,
            "key_clusters": self.key_clusters,
            "noise_clusters": self.noise_clusters,
            "primary_root_cause": self.primary_root_cause,
            "clusters": [c.to_dict() for c in self.clusters],
            "representatives": self.representatives,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_text(self) -> str:
        lines = []
        lines.append("=" * 72)
        lines.append(
            f"失败归因聚类：共 {self.total_failures} 条失败 -> "
            f"{self.cluster_count} 个簇（关键 {self.key_clusters} / 噪声 {self.noise_clusters}）"
        )
        if self.primary_root_cause:
            lines.append(f"主要根因簇（最大关键簇）：{self.primary_root_cause}")
        lines.append("=" * 72)
        for c in self.clusters:
            tag = "KEY " if c.label == "key" else "NOIS"
            lines.append(
                f"[{tag}] {c.cluster_id}  数量={c.size}  "
                f"{c.error_type} @ {c.location or '<无位置>'}"
            )
            lines.append(f"      信息模板: {c.message_template}")
            lines.append(f"      代表用例: {c.representative}")
            lines.append(f"      判定依据: {'; '.join(c.reasons)}")
            if c.size > 1:
                shown = ", ".join(c.members[:8])
                suffix = " ..." if c.size > 8 else ""
                lines.append(f"      簇内用例: {shown}{suffix}")
            lines.append("-" * 72)
        lines.append("代表用例清单：")
        for item in self.representatives:
            lines.append(
                f"  {item['cluster_id']} ({item['label']}, n={item['size']}) "
                f"{item['representative']} :: {item['representative_message']}"
            )
        return "\n".join(lines)


def build_report(cases: List[FailureCase], clusters: List[Cluster]) -> Report:
    key_clusters = [c for c in clusters if c.label == "key"]
    primary = key_clusters[0].cluster_id if key_clusters else ""
    representatives = [
        {
            "cluster_id": c.cluster_id,
            "label": c.label,
            "size": c.size,
            "representative": c.representative,
            "location": c.location,
            "error_type": c.error_type,
            "representative_message": c.representative_message,
        }
        for c in clusters
    ]
    return Report(
        total_failures=len(cases),
        cluster_count=len(clusters),
        key_clusters=len(key_clusters),
        noise_clusters=len(clusters) - len(key_clusters),
        primary_root_cause=primary,
        clusters=clusters,
        representatives=representatives,
        cases=cases,
    )
