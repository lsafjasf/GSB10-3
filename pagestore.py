"""pagestore — 带校验的多副本页存储与多数决修复（仅标准库）。

设计要点：
- 页格式：header(magic|page_no|version|payload_len|header_crc) + payload + 双份 payload_crc。
- 读页时逐副本校验，能定位到页号、损坏区域(header/payload/checksum)与文件偏移；
  存在修复源时还能给出页内逐字节差异位置。
- 修复采用多数决：有效票数必须超过副本总数的一半（严格多数），否则放弃并报告。
- 修复一律“追加新页”，绝不原地覆盖；旧（坏）数据保留在原偏移处。
- 扫描遇损坏页头时按 magic+header_crc 重新同步，保证修复后新页仍可被读到。
"""
from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass, field
from enum import Enum

MAGIC = b"PG01"
_HEADER_FMT = ">4sQQI"                      # magic | page_no | version | payload_len
_HEADER_BODY = struct.calcsize(_HEADER_FMT)  # 24 字节
HEADER_SIZE = _HEADER_BODY + 4               # 28 字节（含 header_crc）
TRAILER_SIZE = 8                             # 双份 payload_crc
MAX_PAYLOAD = 1 << 20                        # 单页 payload 上限 1 MiB
DIFF_REPORT_LIMIT = 32                       # 差异偏移最多报告个数


class Region(str, Enum):
    HEADER = "header"
    PAYLOAD = "payload"
    CHECKSUM = "checksum"
    TRUNCATED = "truncated"


class State(str, Enum):
    GOOD = "good"                              # 全部校验通过
    SUSPECT_CHECKSUM = "suspect_checksum"      # 校验值双份不一致，数据重算匹配其一
    CORRUPT = "corrupt"                        # 校验失败
    MISSING = "missing"                        # 该副本没有此页


class Decision(str, Enum):
    NO_OP = "no_op"            # 无需修复
    REPAIRED = "repaired"      # 已修复
    ABORTED = "aborted"        # 放弃修复


class PageNotFoundError(KeyError):
    """所有副本中都不存在该页。"""


class MajorityUnavailableError(Exception):
    """无法形成法定多数，读取/修复被放弃。report 属性携带完整报告。"""

    def __init__(self, report: "RepairReport"):
        self.report = report
        super().__init__(f"page {report.page_no}: {report.reason}")


@dataclass
class PageRecord:
    page_no: int
    version: int
    payload: bytes
    offset: int           # 页在副本文件中的起始偏移
    stored_crc1: int
    stored_crc2: int
    computed_crc: int


@dataclass
class CorruptRecord:
    offset: int
    region: Region
    detail: str


@dataclass
class ReplicaStatus:
    replica: int
    state: State
    version: int | None = None
    computed_crc: int | None = None
    stored_crc1: int | None = None
    stored_crc2: int | None = None
    region: Region | None = None
    offset: int | None = None          # 损坏位置（文件偏移）
    diff_offsets: list[int] = field(default_factory=list)  # 与修复源的页内差异偏移
    detail: str = ""


@dataclass
class ReplicaChange:
    replica: int
    before_version: int | None
    after_version: int
    before_crc: int | None
    after_crc: int
    note: str


@dataclass
class RepairReport:
    page_no: int
    decision: Decision
    reason: str
    quorum: int
    votes: dict[int, int]              # payload_crc -> 票数
    source_replicas: list[int]
    statuses: list[ReplicaStatus]
    changes: list[ReplicaChange] = field(default_factory=list)
    data: bytes | None = None          # 多数派认定的正确内容


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def encode_page(page_no: int, version: int, payload: bytes) -> bytes:
    body = struct.pack(_HEADER_FMT, MAGIC, page_no, version, len(payload))
    header = body + struct.pack(">I", _crc32(body))
    crc = _crc32(payload)
    return header + payload + struct.pack(">II", crc, crc)


class PageStore:
    """日志结构多副本页存储。每个副本一个文件，写/修复均为追加。"""

    def __init__(self, directory: str, replicas: int = 3):
        if replicas < 1:
            raise ValueError("replicas must be >= 1")
        self.directory = directory
        self.replicas = replicas
        os.makedirs(directory, exist_ok=True)
        self._paths = [os.path.join(directory, f"replica{i}.db") for i in range(replicas)]
        for path in self._paths:
            if not os.path.exists(path):
                open(path, "wb").close()

    # ------------------------------------------------------------------ 写

    def write(self, page_no: int, payload: bytes) -> int:
        """向全部副本追加新版本的页，返回新版本号。"""
        payload = bytes(payload)
        if len(payload) > MAX_PAYLOAD:
            raise ValueError("payload too large")
        version = self.latest_version(page_no) + 1
        for r in range(self.replicas):
            self._append(r, page_no, version, payload)
        return version

    def latest_version(self, page_no: int) -> int:
        version = 0
        for r in range(self.replicas):
            rec = self._scan(r)[0].get(page_no)
            if isinstance(rec, PageRecord):
                version = max(version, rec.version)
        return version

    def _append(self, replica: int, page_no: int, version: int, payload: bytes) -> None:
        with open(self._paths[replica], "ab") as f:
            f.write(encode_page(page_no, version, payload))
            f.flush()
            os.fsync(f.fileno())

    # ------------------------------------------------------------------ 扫描与校验

    @staticmethod
    def _resync(data: bytes, start: int) -> int | None:
        """页头损坏后，向后寻找下一个 magic 且 header_crc 合法的位置。"""
        n = len(data)
        for pos in range(start, n - HEADER_SIZE + 1):
            if data[pos:pos + 4] != MAGIC:
                continue
            body = data[pos:pos + _HEADER_BODY]
            (hcrc,) = struct.unpack(">I", data[pos + _HEADER_BODY:pos + HEADER_SIZE])
            if _crc32(body) != hcrc:
                continue
            plen = struct.unpack(">I", body[20:24])[0]
            if plen <= MAX_PAYLOAD and pos + HEADER_SIZE + plen + TRAILER_SIZE <= n:
                return pos
        return None

    def _scan(self, replica: int) -> tuple[dict[int, PageRecord | CorruptRecord], list[CorruptRecord]]:
        """扫描副本文件，返回 (按页号的最新记录, 无法归属页号的损坏记录)。"""
        with open(self._paths[replica], "rb") as f:
            data = f.read()
        pages: dict[int, PageRecord | CorruptRecord] = {}
        orphan_corrupt: list[CorruptRecord] = []
        pos, n = 0, len(data)
        while pos + HEADER_SIZE <= n:
            body = data[pos:pos + _HEADER_BODY]
            (hcrc,) = struct.unpack(">I", data[pos + _HEADER_BODY:pos + HEADER_SIZE])
            magic, page_no, version, plen = struct.unpack(_HEADER_FMT, body)
            if magic != MAGIC or _crc32(body) != hcrc or plen > MAX_PAYLOAD:
                orphan_corrupt.append(CorruptRecord(pos, Region.HEADER, "magic/header_crc 校验失败"))
                nxt = self._resync(data, pos + 1)
                if nxt is None:
                    break
                pos = nxt
                continue
            end = pos + HEADER_SIZE + plen + TRAILER_SIZE
            if end > n:
                pages[page_no] = CorruptRecord(pos, Region.TRUNCATED, "页被截断")
                break
            payload = data[pos + HEADER_SIZE:pos + HEADER_SIZE + plen]
            crc1, crc2 = struct.unpack(">II", data[pos + HEADER_SIZE + plen:end])
            pages[page_no] = PageRecord(
                page_no=page_no, version=version, payload=payload, offset=pos,
                stored_crc1=crc1, stored_crc2=crc2, computed_crc=_crc32(payload),
            )
            pos = end
        if pos < n and not orphan_corrupt and n - pos < HEADER_SIZE:
            orphan_corrupt.append(CorruptRecord(pos, Region.TRUNCATED, "文件尾部存在不完整页头"))
        return pages, orphan_corrupt

    def _evaluate(self, replica: int, record) -> ReplicaStatus:
        if isinstance(record, CorruptRecord):
            return ReplicaStatus(replica, State.CORRUPT, region=record.region,
                                 offset=record.offset, detail=record.detail)
        assert isinstance(record, PageRecord)
        base = dict(version=record.version, computed_crc=record.computed_crc,
                    stored_crc1=record.stored_crc1, stored_crc2=record.stored_crc2)
        c1, c2, comp = record.stored_crc1, record.stored_crc2, record.computed_crc
        if c1 == c2 == comp:
            return ReplicaStatus(replica, State.GOOD, **base)
        trailer = record.offset + HEADER_SIZE + len(record.payload)
        if c1 != c2 and comp in (c1, c2):
            bad_off = trailer if comp == c2 else trailer + 4
            return ReplicaStatus(replica, State.SUSPECT_CHECKSUM, region=Region.CHECKSUM,
                                 offset=bad_off, detail="校验值双份不一致，数据重算匹配另一份", **base)
        return ReplicaStatus(replica, State.CORRUPT, region=Region.PAYLOAD,
                             offset=record.offset + HEADER_SIZE,
                             detail="payload 与双份校验值均不匹配", **base)

    def verify(self, page_no: int) -> list[ReplicaStatus]:
        """逐副本校验某页，返回每个副本的状态（含损坏定位）。"""
        statuses = []
        for r in range(self.replicas):
            pages, orphans = self._scan(r)
            record = pages.get(page_no)
            if record is None:
                if orphans:
                    last = orphans[-1]
                    statuses.append(ReplicaStatus(r, State.CORRUPT, region=last.region,
                                                  offset=last.offset,
                                                  detail=f"页头损坏，无法确认是否为目标页: {last.detail}"))
                else:
                    statuses.append(ReplicaStatus(r, State.MISSING, detail="副本中不存在该页"))
            else:
                statuses.append(self._evaluate(r, record))
        return statuses

    # ------------------------------------------------------------------ 多数决与修复

    def _decide(self, page_no: int):
        statuses = self.verify(page_no)
        if all(st.state == State.MISSING for st in statuses):
            raise PageNotFoundError(page_no)
        quorum = self.replicas // 2 + 1
        votes: dict[int, list[int]] = {}
        payloads: dict[int, bytes] = {}
        for r, st in enumerate(statuses):
            if st.state in (State.GOOD, State.SUSPECT_CHECKSUM):
                votes.setdefault(st.computed_crc, []).append(r)
                if st.computed_crc not in payloads:
                    rec = self._scan(r)[0][page_no]
                    payloads[st.computed_crc] = rec.payload
        source_crc = None
        for crc, rs in votes.items():
            if len(rs) >= quorum:
                source_crc = crc
                break
        return statuses, votes, quorum, source_crc, payloads

    def repair(self, page_no: int) -> RepairReport:
        """按多数决修复某页。修复一律追加新页，不原地覆盖。"""
        statuses, votes, quorum, source_crc, payloads = self._decide(page_no)
        vote_summary = {crc: len(rs) for crc, rs in votes.items()}
        if source_crc is None:
            reason = ("全部副本校验失败，无有效修复来源" if not votes
                      else f"有效票数 {sorted(vote_summary.values(), reverse=True)} 未达法定多数 {quorum}，放弃修复")
            return RepairReport(page_no, Decision.ABORTED, reason, quorum, vote_summary,
                                [], statuses)
        source_payload = payloads[source_crc]
        source_replicas = votes[source_crc]
        source_version = max(st.version or 0 for st in statuses)

        targets = [st for st in statuses
                   if not (st.state == State.GOOD and st.computed_crc == source_crc)]
        if not targets:
            return RepairReport(page_no, Decision.NO_OP, "全部副本一致且校验通过", quorum,
                                vote_summary, source_replicas, statuses, data=source_payload)

        # 为 payload 损坏的副本计算页内差异偏移（损坏定位）
        for st in targets:
            if st.region == Region.PAYLOAD:
                rec = self._scan(st.replica)[0].get(page_no)
                if isinstance(rec, PageRecord):
                    bad, good = rec.payload, source_payload
                    diffs = [i for i in range(min(len(bad), len(good))) if bad[i] != good[i]]
                    st.diff_offsets = diffs[:DIFF_REPORT_LIMIT]
                    if len(bad) != len(good):
                        st.detail += f"；长度 {len(bad)} != {len(good)}"

        changes = []
        for st in targets:
            new_version = max(source_version, st.version or 0) + 1
            self._append(st.replica, page_no, new_version, source_payload)
            note = {State.CORRUPT: "重写损坏页", State.SUSPECT_CHECKSUM: "重写存疑校验值",
                    State.MISSING: "补齐缺失页"}.get(st.state, "对齐多数派内容")
            changes.append(ReplicaChange(
                replica=st.replica, before_version=st.version, after_version=new_version,
                before_crc=st.computed_crc if st.computed_crc is not None else st.stored_crc1,
                after_crc=source_crc, note=note))
        return RepairReport(page_no, Decision.REPAIRED,
                            f"多数派（{len(source_replicas)}/{self.replicas} 票）为修复来源，"
                            f"已追加新页修复 {len(changes)} 个副本",
                            quorum, vote_summary, source_replicas, statuses,
                            changes, data=source_payload)

    def read(self, page_no: int, *, repair: bool = True) -> bytes:
        """读取页内容。默认先按多数决修复；无法形成多数时抛 MajorityUnavailableError。"""
        report = self.repair(page_no) if repair else self._read_report(page_no)
        if report.decision == Decision.ABORTED:
            raise MajorityUnavailableError(report)
        return report.data

    def _read_report(self, page_no: int) -> RepairReport:
        statuses, votes, quorum, source_crc, payloads = self._decide(page_no)
        vote_summary = {crc: len(rs) for crc, rs in votes.items()}
        if source_crc is None:
            return RepairReport(page_no, Decision.ABORTED, "无法形成法定多数", quorum,
                                vote_summary, [], statuses)
        return RepairReport(page_no, Decision.NO_OP, "ok", quorum, vote_summary,
                            votes[source_crc], statuses, data=payloads[source_crc])

    # ------------------------------------------------------------------ 测试辅助

    def locate(self, page_no: int, replica: int) -> PageRecord:
        """返回某副本中该页最新记录（供测试/工具定位偏移）。"""
        rec = self._scan(replica)[0].get(page_no)
        if not isinstance(rec, PageRecord):
            raise PageNotFoundError((page_no, replica))
        return rec
