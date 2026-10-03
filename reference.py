"""逐字节参考实现：刻意朴素、与 midi_stream.py 不共享任何代码。

每个事件输出一个 9 元组，布局与 Event.signature() 完全一致：
    (delta, abs_time, track, order, kind, status, meta_type, data, raw)

合并使用朴素的插入排序，排序键同样是 (abs_time, track, order)。
"""

# 通道消息数据长度（按高半字节索引 0x8..0xE，0x0/0xF 不用）
_CH_LEN = [0] * 16
for _hi, _n in ((0x8, 2), (0x9, 2), (0xA, 2), (0xB, 2),
                (0xC, 1), (0xD, 1), (0xE, 2)):
    _CH_LEN[_hi] = _n

_SYS_LEN = {0xF1: 1, 0xF2: 2, 0xF3: 1, 0xF6: 0,
            0xF8: 0, 0xF9: 0, 0xFA: 0, 0xFB: 0, 0xFC: 0, 0xFE: 0}
_RT = (0xF8, 0xF9, 0xFA, 0xFB, 0xFC, 0xFE)


def parse_track_reference(data: bytes, track: int = 0) -> list:
    events = []
    i = 0
    t = 0
    rs = None  # 运行状态
    size = len(data)

    def push(delta, kind, status, mtype, payload, raw):
        events.append((delta, t, track, len(events),
                       kind, status, mtype, payload, raw))

    while i < size:
        # ---- 逐字节读增量时间 VLQ ----
        vlq_from = i
        v = 0
        complete = False
        while i < size:
            byte = data[i]
            i += 1
            v = v * 128 + (byte % 128)
            if byte < 128:
                complete = True
                break
        if not complete:
            push(None, 'truncated', None, None, b'', data[vlq_from:i])
            break
        t += v
        if i >= size:
            push(v, 'truncated', None, None, b'', b'')
            break

        # ---- 读一个事件 ----
        start = i
        byte = data[i]
        if byte < 0x80:
            if rs is None:
                push(v, 'unknown', None, None, b'', data[i:i + 1])
                i += 1
            else:
                need = _CH_LEN[rs >> 4]
                if i + need > size:
                    push(v, 'truncated', rs, None, data[i:size], data[i:size])
                    break
                push(v, 'channel', rs, None, data[i:i + need], data[i:i + need])
                i += need
            continue

        i += 1
        if byte < 0xF0:  # 通道消息
            rs = byte
            need = _CH_LEN[byte >> 4]
            if i + need > size:
                push(v, 'truncated', byte, None, data[i:size], data[start:size])
                break
            push(v, 'channel', byte, None, data[i:i + need], data[start:i + need])
            i += need
        elif byte == 0xF0 or byte == 0xF7:  # 系统专有
            rs = None
            ln = 0
            ok = False
            while i < size:
                b2 = data[i]
                i += 1
                ln = ln * 128 + (b2 % 128)
                if b2 < 128:
                    ok = True
                    break
            if not ok:
                push(v, 'truncated', byte, None, b'', data[start:size])
                break
            if i + ln > size:
                push(v, 'truncated', byte, None, data[i:size], data[start:size])
                break
            push(v, 'sysex', byte, None, data[i:i + ln], data[start:i + ln])
            i += ln
        elif byte == 0xFF:  # 元事件
            rs = None
            if i >= size:
                push(v, 'truncated', byte, None, b'', data[start:size])
                break
            mtype = data[i]
            i += 1
            ln = 0
            ok = False
            while i < size:
                b2 = data[i]
                i += 1
                ln = ln * 128 + (b2 % 128)
                if b2 < 128:
                    ok = True
                    break
            if not ok:
                push(v, 'truncated', byte, mtype, b'', data[start:size])
                break
            if i + ln > size:
                push(v, 'truncated', byte, mtype, data[i:size], data[start:size])
                break
            push(v, 'meta', byte, mtype, data[i:i + ln], data[start:i + ln])
            i += ln
        elif byte in _SYS_LEN:
            if byte not in _RT:
                rs = None
            need = _SYS_LEN[byte]
            if i + need > size:
                push(v, 'truncated', byte, None, data[i:size], data[start:size])
                break
            push(v, 'system', byte, None, data[i:i + need], data[start:i + need])
            i += need
        else:  # 未定义状态字节：原样保留
            rs = None
            push(v, 'unknown', byte, None, b'', data[start:i])
    return events


def merge_reference(parsed_tracks: list) -> list:
    """朴素合并：收集后插入排序，键 (abs_time, track, order)。"""
    merged = []
    for evs in parsed_tracks:
        merged.extend(evs)
    out = []
    for ev in merged:
        key = (ev[1], ev[2], ev[3])
        j = len(out)
        out.append(ev)
        while j > 0:
            pk = (out[j - 1][1], out[j - 1][2], out[j - 1][3])
            if pk <= key:
                break
            out[j] = out[j - 1]
            j -= 1
        out[j] = ev
    return out
