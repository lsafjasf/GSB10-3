"""事件数据结构：数据结构形状 + 取值范围类约定的可执行断言。"""

from .contracts import check

REQUIRED_KEYS = frozenset(
    ("event_id", "type", "symbol", "amount", "occurred_at", "tags", "source", "trace_id")
)
EVENT_TYPES = frozenset(("trade", "quote"))
ALLOWED_SYMBOLS = frozenset(("AAPL", "GOOG", "TSLA"))
MIN_AMOUNT = 0
MAX_AMOUNT = 1_000_000_000
MAX_TAGS = 3


def _check_shape(event):
    # [S1] event 必须恰好拥有 REQUIRED_KEYS 这 8 个键（不多不少），值不能为 None。
    check(isinstance(event, dict), "S1", "event 必须是 dict")
    check(frozenset(event.keys()) == REQUIRED_KEYS, "S1",
          f"event 的键必须恰好是 {sorted(REQUIRED_KEYS)}，实际 {sorted(event.keys())}")
    check(all(value is not None for key, value in event.items() if key != "symbol"),
          "S1", "除 symbol 外的字段值不允许为 None")


def _check_event_id(event_id):
    # [R1] event_id 必须是非空字符串。
    check(isinstance(event_id, str) and len(event_id) > 0, "R1",
          f"event_id 必须是非空 str，实际 {event_id!r}")


def _check_type(event_type):
    # [R2] type 只能是 'trade' 或 'quote'。
    check(event_type in EVENT_TYPES, "R2",
          f"type 必须是 {sorted(EVENT_TYPES)} 之一，实际 {event_type!r}")


def _check_amount(amount):
    # [R3] amount 必须是 int（禁止 bool/float）且在 [0, 1_000_000_000]。
    check(type(amount) is int and MIN_AMOUNT <= amount <= MAX_AMOUNT, "R3",
          f"amount 必须是 [{MIN_AMOUNT},{MAX_AMOUNT}] 内的 int，实际 {amount!r}")


def _check_occurred_at(occurred_at):
    # [R4] occurred_at 必须是非负 int（禁止 bool/float）。
    check(type(occurred_at) is int and occurred_at >= 0, "R4",
          f"occurred_at 必须是非负 int，实际 {occurred_at!r}")


def _check_tags(tags):
    # [R5] tags 必须是 0..3 个 str 的 tuple，元素可哈希且不重复。
    check(isinstance(tags, tuple) and len(tags) <= MAX_TAGS
          and all(type(tag) is str for tag in tags), "R5",
          f"tags 必须是至多 {MAX_TAGS} 个 str 的 tuple，实际 {tags!r}")
    check(all(tag.__hash__ is not None for tag in tags)
          and len(set(tags)) == len(tags), "R5",
          f"tags 元素必须可哈希且不重复，实际 {tags!r}")


def _check_symbol_rule(event_type, symbol):
    # [S2] trade 必须带非空 symbol；quote 的 symbol 必须为 None。
    check(not (event_type == "trade" and not (isinstance(symbol, str) and symbol))
          and not (event_type == "quote" and symbol is not None), "S2",
          f"trade 必须带非空 symbol、quote 的 symbol 必须为 None，"
          f"实际 type={event_type!r} symbol={symbol!r}")


def _check_symbol_value(symbol):
    # [R6] symbol 非空时必须在白名单内。
    check(symbol is None or symbol in ALLOWED_SYMBOLS, "R6",
          f"symbol 必须属于 {sorted(ALLOWED_SYMBOLS)}，实际 {symbol!r}")


def validate_event(event):
    """对单个事件执行全部形状与取值断言，返回原 event。"""
    _check_shape(event)
    _check_event_id(event["event_id"])
    _check_type(event["type"])
    _check_amount(event["amount"])
    _check_occurred_at(event["occurred_at"])
    _check_tags(event["tags"])
    _check_symbol_rule(event["type"], event["symbol"])
    _check_symbol_value(event["symbol"])
    return event


def new_event(event_id, event_type, symbol, amount, occurred_at,
              tags=(), source="test", trace_id="trace"):
    """构造一个保证合法的事件（便于测试与调用方使用）。"""
    return {
        "event_id": event_id,
        "type": event_type,
        "symbol": symbol,
        "amount": amount,
        "occurred_at": occurred_at,
        "tags": tags,
        "source": source,
        "trace_id": trace_id,
    }
