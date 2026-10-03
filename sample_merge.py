"""合并顺序样例：3 条轨道（元事件/旋律/和声），展示确定的合并规则。

运行：python3 sample_merge.py
"""

from midi_stream import encode_vlq, merge_tracks, parse_track


def track(*events):
    """events: (delta, raw_event_bytes)，拼成轨道字节流。"""
    out = bytearray()
    for delta, body in events:
        out += encode_vlq(delta)
        out += body
    return bytes(out)


def note(on_status, key, vel=0x40):
    return bytes([on_status, key, vel])


def note_off(key, ch=0):
    return bytes([0x80 | ch, key, 0x40])


def meta(mtype, payload=b''):
    return bytes([0xFF, mtype]) + encode_vlq(len(payload)) + payload


def tempo(us_per_quarter):
    return meta(0x51, us_per_quarter.to_bytes(3, 'big'))


def describe(e):
    if e.kind == 'meta':
        names = {0x51: 'SetTempo', 0x2F: 'EndOfTrack', 0x03: 'TrackName'}
        return 'meta  %-11s type=0x%02X data=%s' % (
            names.get(e.meta_type, '?'), e.meta_type, e.data.hex())
    if e.kind == 'channel':
        action = 'NoteOn ' if e.status & 0xF0 == 0x90 else 'NoteOff'
        return 'ch%02d  %s key=0x%02X vel=0x%02X' % (
            e.status & 0x0F, action, e.data[0], e.data[1])
    return '%-7s raw=%s' % (e.kind, e.raw.hex())


def main():
    tracks = [
        track(  # 轨道 0： tempo/元事件
            (0, meta(0x03, b'tempo')),
            (0, tempo(500000)),
            (960, meta(0x2F)),
        ),
        track(  # 轨道 1：旋律 C4 -> D4
            (0, note(0x90, 0x3C)),
            (480, note_off(0x3C)),
            (0, note(0x90, 0x3E)),
            (480, note_off(0x3E)),
            (0, meta(0x2F)),
        ),
        track(  # 轨道 2：和声 G3（与旋律同拍起）
            (0, note(0x91, 0x43)),
            (960, note_off(0x43, 1)),
            (0, meta(0x2F)),
        ),
    ]
    parsed = [parse_track(t, track=i) for i, t in enumerate(tracks)]
    merged = merge_tracks(parsed)

    print('%-8s %-5s %-5s %s' % ('absTime', 'track', 'order', 'event'))
    print('-' * 56)
    for e in merged:
        print('%-8d %-5d %-5d %s'
              % (e.abs_time, e.track, e.order, describe(e)))
    print()
    print('规则：absTime 升序；同一时刻 track 小者优先；')
    print('      同轨道内按 order（原始先后）。')
    return merged


if __name__ == '__main__':
    main()
