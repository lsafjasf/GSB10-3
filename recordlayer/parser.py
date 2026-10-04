"""记录层解析核心：帧切分、类型分发、握手消息跨记录重组。

提供两种用法，二者共用同一套底层切分/分发逻辑，因此结果必然一致：

* :func:`iter_events` —— 一次性读取整段字节，立即得到全部事件，
  数据不完整时抛 :class:`TruncatedRecordError` /
  :class:`TruncatedHandshakeError`，异常上的 ``missing`` 精确给出
  还缺多少字节。
* :class:`RecordAssembler` —— 流式喂入任意大小的数据块（记录可能在
  任意位置被切断），完整一条事件就吐出一条；内部缓冲未完成的帧和
  未完成的握手消息。
"""

from .errors import (
    MalformedRecordError,
    TruncatedRecordError,
    TruncatedHandshakeError,
    UnknownRecordTypeError,
    InterleavedRecordError,
)
from .events import (
    Record,
    HandshakeMessage,
    Alert,
    ChangeCipherSpec,
    ApplicationData,
    UnknownRecord,
)

RECORD_HEADER_SIZE = 5
HANDSHAKE_HEADER_SIZE = 6  # u8 类型 + u24 长度 + u16 序号（本实现的变体）
MAX_RECORD_LENGTH = 0x4000  # 协议允许的单条记录分片上限

# TLS 1.0 / 1.1 / 1.2 的记录层版本号
SUPPORTED_VERSIONS = frozenset({(3, 1), (3, 2), (3, 3)})

CONTENT_CHANGE_CIPHER_SPEC = 20
CONTENT_ALERT = 21
CONTENT_HANDSHAKE = 22
CONTENT_APPLICATION_DATA = 23


def _u24(buf, offset):
    return (buf[offset] << 16) | (buf[offset + 1] << 8) | buf[offset + 2]


def _take_record(buf, offset, supported_versions):
    """从 ``buf[offset:]`` 切下一条完整记录。

    返回 ``(Record, 下一条记录的偏移)``。数据不够时抛
    :class:`TruncatedRecordError`；版本/长度非法时抛
    :class:`MalformedRecordError`。绝不返回被截断的记录。
    """
    available = len(buf) - offset
    if available < RECORD_HEADER_SIZE:
        raise TruncatedRecordError("记录头不完整", RECORD_HEADER_SIZE, available)

    content_type = buf[offset]
    version = (buf[offset + 1], buf[offset + 2])
    length = (buf[offset + 3] << 8) | buf[offset + 4]

    if version not in supported_versions:
        raise MalformedRecordError(
            "不支持的记录层版本号 0x%04x（%d.%d），允许: %s"
            % (
                (version[0] << 8) | version[1],
                version[0],
                version[1],
                ", ".join("%d.%d" % v for v in sorted(supported_versions)),
            )
        )
    if length > MAX_RECORD_LENGTH:
        raise MalformedRecordError(
            "记录长度字段声明 %d 字节，超过协议上限 %d 字节"
            % (length, MAX_RECORD_LENGTH)
        )

    needed_total = RECORD_HEADER_SIZE + length
    if available < needed_total:
        raise TruncatedRecordError("记录分片不完整", needed_total, available)

    body = bytes(buf[offset + RECORD_HEADER_SIZE : offset + needed_total])
    return Record(content_type, version, body), offset + needed_total


def _dispatch_non_handshake(record, strict_unknown):
    """把非握手记录按类型变成对应事件；握手记录交给调用方处理。"""
    content_type = record.content_type
    if content_type == CONTENT_CHANGE_CIPHER_SPEC:
        if len(record.body) != 1 or record.body[0] != 0x01:
            raise MalformedRecordError(
                "ChangeCipherSpec 载荷必须为单字节 0x01，实际为 %r" % record.body
            )
        return [ChangeCipherSpec(record.body)]
    if content_type == CONTENT_ALERT:
        if len(record.body) != 2:
            raise MalformedRecordError(
                "告警记录长度必须恰好为 2（级别+描述），实际为 %d"
                % len(record.body)
            )
        # 保留原始码值，不做“认识才放行”的白名单过滤
        return [Alert(record.body[0], record.body[1], record.body)]
    if content_type == CONTENT_APPLICATION_DATA:
        return [ApplicationData(record.body)]
    if strict_unknown:
        raise UnknownRecordTypeError(content_type)
    return [UnknownRecord(content_type, record.version, record.body)]


def _drain_handshake_buffer(hs_buf):
    """从已拼接的握手字节流里取出所有完整握手消息，剩余部分留在缓冲。"""
    events = []
    while len(hs_buf) >= HANDSHAKE_HEADER_SIZE:
        msg_type = hs_buf[0]
        length = _u24(hs_buf, 1)
        message_seq = (hs_buf[4] << 8) | hs_buf[5]
        needed_total = HANDSHAKE_HEADER_SIZE + length
        if len(hs_buf) < needed_total:
            break  # 消息还没到齐，继续等后续记录
        raw = bytes(hs_buf[:needed_total])
        events.append(
            HandshakeMessage(
                msg_type=msg_type,
                message_seq=message_seq,
                body=raw[HANDSHAKE_HEADER_SIZE:],
                raw=raw,
            )
        )
        del hs_buf[:needed_total]
    return events


def parse_records(data, supported_versions=SUPPORTED_VERSIONS):
    """一次性切分原始记录帧，不做类型分发。数据不完整即报错。"""
    records = []
    offset = 0
    while offset < len(data):
        record, offset = _take_record(data, offset, supported_versions)
        records.append(record)
    return records


def iter_events(data, strict_unknown=False, supported_versions=SUPPORTED_VERSIONS):
    """一次性读取并按类型分发，返回全部事件。

    握手消息跨记录自动重组；若流末尾握手消息仍不完整，抛
    :class:`TruncatedHandshakeError`。
    """
    events = []
    hs_buf = bytearray()
    offset = 0
    while offset < len(data):
        record, offset = _take_record(data, offset, supported_versions)
        if hs_buf and record.content_type != CONTENT_HANDSHAKE:
            raise InterleavedRecordError(record.content_type)
        if record.content_type == CONTENT_HANDSHAKE:
            hs_buf.extend(record.body)
            events.extend(_drain_handshake_buffer(hs_buf))
        else:
            events.extend(_dispatch_non_handshake(record, strict_unknown))
    if hs_buf:
        if len(hs_buf) < HANDSHAKE_HEADER_SIZE:
            raise TruncatedHandshakeError(
                "握手消息头不完整", HANDSHAKE_HEADER_SIZE, len(hs_buf)
            )
        length = _u24(hs_buf, 1)
        raise TruncatedHandshakeError(
            "握手消息体不完整", HANDSHAKE_HEADER_SIZE + length, len(hs_buf)
        )
    return events


def parse_handshake_messages(data, supported_versions=SUPPORTED_VERSIONS):
    """便捷函数：重组字节流中的全部握手消息（要求流中只有握手记录）。"""
    events = iter_events(data, supported_versions=supported_versions)
    messages = []
    for event in events:
        if not isinstance(event, HandshakeMessage):
            raise MalformedRecordError(
                "期望只有握手记录，却遇到 %s" % type(event).__name__
            )
        messages.append(event)
    return messages


class RecordAssembler:
    """流式记录重组器：可分任意次、任意块大小 :meth:`feed`。

    每次调用返回“这次新凑齐的事件”列表；未完成的帧和未完成的
    握手消息留在内部状态里，等下一批数据到达后继续。全部喂完后
    调用 :meth:`finish` 确认没有残留的半截数据。
    """

    def __init__(self, strict_unknown=False, supported_versions=SUPPORTED_VERSIONS):
        self._strict_unknown = strict_unknown
        self._supported = supported_versions
        self._frame_buf = bytearray()  # 尚未凑成完整记录帧的字节
        self._hs_buf = bytearray()     # 跨记录拼接中的握手消息字节

    def reset(self):
        self._frame_buf.clear()
        self._hs_buf.clear()

    @property
    def pending_record_bytes(self):
        """当前卡在半条记录帧上的字节数。"""
        return len(self._frame_buf)

    @property
    def pending_handshake_bytes(self):
        """当前卡在半条握手消息上的字节数。"""
        return len(self._hs_buf)

    def feed(self, data):
        self._frame_buf.extend(data)
        events = []
        offset = 0
        total = len(self._frame_buf)
        while True:
            try:
                record, next_offset = _take_record(
                    self._frame_buf, offset, self._supported
                )
            except TruncatedRecordError:
                break
            offset = next_offset
            if self._hs_buf and record.content_type != CONTENT_HANDSHAKE:
                raise InterleavedRecordError(record.content_type)
            if record.content_type == CONTENT_HANDSHAKE:
                self._hs_buf.extend(record.body)
                events.extend(_drain_handshake_buffer(self._hs_buf))
            else:
                events.extend(_dispatch_non_handshake(record, self._strict_unknown))
        del self._frame_buf[:offset]
        return events

    def finish(self):
        """声明数据已全部喂完；有任何半截残留都抛出带 ``missing`` 的异常。"""
        if self._frame_buf:
            if len(self._frame_buf) < RECORD_HEADER_SIZE:
                raise TruncatedRecordError(
                    "记录头不完整", RECORD_HEADER_SIZE, len(self._frame_buf)
                )
            length = (self._frame_buf[3] << 8) | self._frame_buf[4]
            raise TruncatedRecordError(
                "记录分片不完整",
                RECORD_HEADER_SIZE + length,
                len(self._frame_buf),
            )
        if self._hs_buf:
            if len(self._hs_buf) < HANDSHAKE_HEADER_SIZE:
                raise TruncatedHandshakeError(
                    "握手消息头不完整", HANDSHAKE_HEADER_SIZE, len(self._hs_buf)
                )
            length = _u24(self._hs_buf, 1)
            raise TruncatedHandshakeError(
                "握手消息体不完整",
                HANDSHAKE_HEADER_SIZE + length,
                len(self._hs_buf),
            )
        return []
