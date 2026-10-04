"""灰度流量切分库（仅依赖 Python 3 标准库）。

判定规则（优先级从高到低）：
  1. 用户标识缺失（None / 空字符串）  -> 不放量（走旧版本），且不做哈希判定。
  2. 用户标识命中白名单               -> 放量（即使比例为 0 也放量）。
  3. 按比例分桶                       -> 稳定哈希取桶，桶号 < 阈值则放量。

稳定性保证：
  - 分桶只依赖 (salt, user_id)，同一用户多次判定、跨进程/跨实例判定结果一致。
  - 放量比例上调时阈值区间只扩大不收缩，已放量的用户不会被踢回旧版本
    （单调放量；前提是 salt 与分桶数不变）。
  - 生产环境务必固定 salt；更换 salt 等同于重新洗牌，全体用户的桶位会重排。
"""

from __future__ import annotations

import hashlib
from typing import Iterable, Optional, Union

DEFAULT_SALT = "gray-release:v1"
DEFAULT_BUCKETS = 10_000  # 万分桶，支持 0.01% 粒度的放量比例


class GrayRelease:
    def __init__(
        self,
        ratio: Union[int, float],
        whitelist: Optional[Iterable[str]] = None,
        salt: str = DEFAULT_SALT,
        buckets: int = DEFAULT_BUCKETS,
    ) -> None:
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
            raise TypeError("ratio 必须是 int 或 float")
        if not 0 <= ratio <= 100:
            raise ValueError("ratio 必须在 [0, 100] 区间内")
        if buckets <= 0:
            raise ValueError("buckets 必须为正整数")

        self.ratio = float(ratio)
        self.buckets = int(buckets)
        # 阈值：桶号落在 [0, threshold) 的用户放量
        self.threshold = round(self.ratio / 100.0 * self.buckets)
        self.whitelist = frozenset(whitelist or ())
        self.salt = salt

    def bucket_of(self, user_id: object) -> int:
        """返回用户所在桶号 [0, buckets)。结果只由 salt 和 user_id 决定。"""
        normalized = self._normalize_id(user_id)
        key = f"{self.salt}:{normalized}".encode("utf-8")
        digest = hashlib.sha256(key).digest()
        return int.from_bytes(digest[:8], "big") % self.buckets

    def is_gray(self, user_id: object) -> bool:
        """判断用户是否命中灰度（新版本）。缺失标识一律返回 False。"""
        normalized = self._normalize_id(user_id)
        if normalized is None:
            return False
        if normalized in self.whitelist:
            return True
        return self.bucket_of(normalized) < self.threshold

    @staticmethod
    def _normalize_id(user_id: object) -> Optional[str]:
        if user_id is None:
            return None
        if isinstance(user_id, str):
            stripped = user_id.strip()
            return stripped or None
        normalized = str(user_id)
        return normalized or None
