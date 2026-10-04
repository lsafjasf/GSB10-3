"""记录层解析可能抛出的异常。

所有“缓冲区不够”的情形都通过异常上的 ``missing`` 属性精确报告
“还缺多少字节”，调用方可以据此等待更多数据，而不是拿到截断结果。
"""


class RecordLayerError(Exception):
    """记录层所有异常的基类。"""


class MalformedRecordError(RecordLayerError):
    """结构非法：版本号不支持、长度超过协议上限、告警体长度错误等。"""


class TruncatedRecordError(RecordLayerError):
    """记录声明的内容还没到齐（可能连 5 字节头都不完整）。

    Parameters
    ----------
    needed_total:
        这条记录从帧头算起一共需要多少字节。
    available:
        缓冲区里目前实际属于这条记录的字节数。
    missing:
        还差多少字节（``needed_total - available``，始终为正）。
    """

    def __init__(self, message, needed_total, available):
        self.needed_total = needed_total
        self.available = available
        self.missing = needed_total - available
        super().__init__(
            "%s（记录共需 %d 字节，已有 %d 字节，还缺 %d 字节）"
            % (message, needed_total, available, self.missing)
        )


class TruncatedHandshakeError(RecordLayerError):
    """握手消息头声明的消息体还没到齐（可能跨记录时仍未补齐）。

    ``missing`` 即还差多少字节才能凑齐这条握手消息。
    """

    def __init__(self, message, needed_total, available):
        self.needed_total = needed_total
        self.available = available
        self.missing = needed_total - available
        super().__init__(
            "%s（握手消息共需 %d 字节，已缓存 %d 字节，还缺 %d 字节）"
            % (message, needed_total, available, self.missing)
        )


class UnknownRecordTypeError(MalformedRecordError):
    """遇到未知记录类型（仅在 strict 模式下抛出）。"""

    def __init__(self, content_type):
        self.content_type = content_type
        super().__init__("未知记录类型: 0x%02x" % content_type)


class InterleavedRecordError(MalformedRecordError):
    """一条握手消息还没重组完，中间插入了非握手记录。"""

    def __init__(self, content_type):
        self.content_type = content_type
        super().__init__(
            "握手消息重组期间出现穿插记录，记录类型=0x%02x" % content_type
        )
