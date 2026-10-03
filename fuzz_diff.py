"""对拍脚本：随机生成事件流，主实现与逐字节参考实现结果必须完全一致。

用法：
    python3 fuzz_diff.py                 # 默认 3000 轮
    python3 fuzz_diff.py -n 10000 --seed 42
    python3 fuzz_diff.py --case 'hex1 hex2 ...'   # 复现指定用例（每轨道一段 hex）

发现不一致时自动执行收缩（ddmin 风格的收敛过程）：
    1. 尝试整轨道删除；
    2. 对每条轨道按 chunk 逐步删减字节；
每一步都验证“仍然不一致”，直到无法再缩小，输出最小复现用例、
两侧首个分歧点及复现命令。
"""

import argparse
import random
import sys

import midi_stream as main_impl
import reference as ref_impl
from midi_stream import encode_vlq


# ---------------- 随机用例生成 ----------------

def _gen_delta(rng):
    r = rng.random()
    if r < 0.45:
        return rng.randint(0, 3)
    if r < 0.8:
        return rng.randint(0, 0x7F)
    if r < 0.95:
        return rng.randint(0, 0x1FFFFF)
    return rng.randint(0, 1 << 40)  # 超长增量时间


def _rand_data_bytes(rng, n):
    # 偶尔故意生成 >=0x80 的“非法”数据字节，验证确定性消费规则
    if rng.random() < 0.05:
        return bytes(rng.randint(0, 255) for _ in range(n))
    return bytes(rng.randint(0, 0x7F) for _ in range(n))


def _gen_event_bytes(rng, state):
    """生成一个事件（不含增量时间）。state 维护是否可发运行状态。"""
    choices = ['channel', 'channel', 'channel', 'meta', 'sysex',
               'system', 'unknown_status', 'stray_data', 'garbage']
    kind = rng.choice(choices)
    if kind == 'channel':
        hi = rng.choice([0x80, 0x90, 0xA0, 0xB0, 0xC0, 0xD0, 0xE0])
        status = hi | rng.randint(0, 15)
        need = 1 if hi in (0xC0, 0xD0) else 2
        payload = _rand_data_bytes(rng, need)
        if state.get('running') == status and rng.random() < 0.5:
            return payload  # 运行状态：省略状态字节
        state['running'] = status
        return bytes([status]) + payload
    if kind == 'meta':
        state['running'] = None
        mtype = rng.choice([0x01, 0x03, 0x2F, 0x51, 0x58, 0x59, 0x7F,
                            rng.randint(0, 0x7F)])
        payload = _rand_data_bytes(rng, rng.randint(0, 6))
        return bytes([0xFF, mtype]) + encode_vlq(len(payload)) + payload
    if kind == 'sysex':
        state['running'] = None
        payload = _rand_data_bytes(rng, rng.randint(0, 6))
        return bytes([rng.choice([0xF0, 0xF7])]) + encode_vlq(len(payload)) + payload
    if kind == 'system':
        st = rng.choice([0xF1, 0xF2, 0xF3, 0xF6, 0xF8, 0xFA, 0xFC])
        if st not in (0xF8, 0xFA, 0xFC):
            state['running'] = None
        need = {0xF1: 1, 0xF2: 2, 0xF3: 1}.get(st, 0)
        return bytes([st]) + _rand_data_bytes(rng, need)
    if kind == 'unknown_status':
        state['running'] = None
        return bytes([rng.choice([0xF4, 0xF5, 0xFD])])
    if kind == 'stray_data':
        return bytes([rng.randint(0, 0x7F)])  # 孤立数据字节
    return bytes(rng.randint(0, 255) for _ in range(rng.randint(1, 4)))


def gen_track(rng):
    state = {'running': None}
    parts = []
    for _ in range(rng.randint(0, 12)):
        parts.append(encode_vlq(_gen_delta(rng)))
        parts.append(_gen_event_bytes(rng, state))
    data = b''.join(parts)
    if data and rng.random() < 0.25:  # 截断事件流
        data = data[:rng.randint(0, len(data) - 1)]
    return data


def gen_case(rng):
    return [gen_track(rng) for _ in range(rng.randint(1, 4))]


# ---------------- 两侧执行与比较 ----------------

def run_main(tracks):
    parsed = [main_impl.parse_track(t, track=i) for i, t in enumerate(tracks)]
    merged = main_impl.merge_tracks(parsed)
    return [e.signature() for e in merged]


def run_ref(tracks):
    parsed = [ref_impl.parse_track_reference(t, track=i)
              for i, t in enumerate(tracks)]
    return ref_impl.merge_reference(parsed)


def first_divergence(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    if len(a) != len(b):
        return min(len(a), len(b))
    return None


def fails(tracks):
    return run_main(tracks) != run_ref(tracks)


# ---------------- 收缩（不一致时的收敛过程） ----------------

def shrink(tracks, log):
    tracks = list(tracks)
    # 第 1 步：尝试整轨道删除
    i = 0
    while i < len(tracks) and len(tracks) > 1:
        cand = tracks[:i] + tracks[i + 1:]
        if fails(cand):
            log('  shrink: 删除轨道 %d（剩 %d 条）' % (i, len(cand)))
            tracks = cand
        else:
            i += 1
    # 第 2 步：对每条轨道做 chunk 删减（ddmin 风格）
    for ti in range(len(tracks)):
        t = tracks[ti]
        chunk = max(1, len(t) // 2)
        while len(t) > 0 and chunk >= 1:
            i = 0
            reduced = False
            while i < len(t):
                cand_t = t[:i] + t[i + chunk:]
                cand = list(tracks)
                cand[ti] = cand_t
                if fails(cand):
                    log('  shrink: 轨道 %d 长度 %d -> %d'
                        % (ti, len(t), len(cand_t)))
                    t = cand_t
                    tracks = cand
                    reduced = True
                    break  # 保持当前 chunk 继续
                i += chunk
            if not reduced:
                if chunk == 1:
                    break
                chunk //= 2
    return tracks


def report_failure(tracks, shrunk):
    print('发现不一致！')
    print('原始用例（每轨道一段 hex）：')
    for i, t in enumerate(tracks):
        print('  track %d (%d 字节): %s' % (i, len(t), t.hex()))
    print('开始收缩……')
    shrunk = shrink(tracks, print)
    print('收缩完成，最小复现用例：')
    for i, t in enumerate(shrunk):
        print('  track %d (%d 字节): %s' % (i, len(t), t.hex()))
    a, b = run_main(shrunk), run_ref(shrunk)
    idx = first_divergence(a, b)
    print('合并流长度：主实现 %d，参考实现 %d' % (len(a), len(b)))
    if idx is not None:
        print('首个分歧位于合并流第 %d 个事件：' % idx)
        lo = max(0, idx - 2)
        for j in range(lo, min(len(a), len(b), idx + 3)):
            mark = '>>' if j == idx else '  '
            print('%s [%d] main: %r' % (mark, j, a[j]))
            print('%s [%d] ref : %r' % (mark, j, b[j]))
    print('复现命令：python3 fuzz_diff.py --case \'%s\''
          % ' '.join(t.hex() for t in shrunk))
    return 1


# ---------------- 入口 ----------------

def main():
    ap = argparse.ArgumentParser(description='MIDI 事件流解析对拍')
    ap.add_argument('-n', type=int, default=3000, help='随机轮数')
    ap.add_argument('--seed', type=int, default=None, help='随机种子')
    ap.add_argument('--case', type=str, default=None,
                    help='复现指定用例：每轨道一段 hex，空格分隔')
    args = ap.parse_args()

    if args.case is not None:
        tracks = [bytes.fromhex(h) for h in args.case.split()] or [b'']
        if fails(tracks):
            return report_failure(tracks, None)
        print('该用例两侧结果一致（%d 条轨道，%d 个事件）。'
              % (len(tracks), len(run_main(tracks))))
        return 0

    seed = args.seed if args.seed is not None else random.randrange(1 << 30)
    rng = random.Random(seed)
    print('种子：%d，轮数：%d' % (seed, args.n))
    for it in range(1, args.n + 1):
        tracks = gen_case(rng)
        if fails(tracks):
            print('第 %d 轮失败（种子 %d）' % (it, seed))
            return report_failure(tracks, None)
        if it % 500 == 0:
            print('  已完成 %d 轮，两侧一致' % it)
    print('全部 %d 轮通过：主实现与逐字节参考实现完全一致。' % args.n)
    return 0


if __name__ == '__main__':
    sys.exit(main())
