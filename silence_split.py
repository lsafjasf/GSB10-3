#!/usr/bin/env python3
"""Adaptive silence detection and WAV splitting — Python 3 standard library only.

Pipeline
--------
1. Read a PCM WAV file (8/16/24/32-bit, mono or multi-channel), mix to mono.
2. Compute per-frame RMS in dBFS.
3. Estimate the local noise floor as a low percentile of frame levels inside a
   sliding window, so the threshold follows a noise bed that changes over time.
   A frame is "silent" when  level_db < floor_db + delta_db.
4. Group silent frames into silence runs (>= min_silence).
5. Put every cut point *inside* a silence run, centered, and clamped so that at
   least `margin` seconds of silence remain on both sides when the run is long
   enough; otherwise the point is placed at the run midpoint.
6. Segments shorter than `min_segment` are merged with the shorter neighbour.
"""

import argparse
import bisect
import math
import os
import struct
import wave

SILENCE_DB = -100.0  # dB floor used for digital zero frames


# --------------------------------------------------------------------------- #
# WAV I/O (PCM only; no third-party libraries)
# --------------------------------------------------------------------------- #
def read_wav(path):
    """Return (samples[float, mono, -1..1], framerate)."""
    with wave.open(path, "rb") as wf:
        nch = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    if width == 1:
        vals = [(b - 128) / 128.0 for b in raw]
    elif width == 2:
        vals = [s / 32768.0 for s in struct.unpack("<%dh" % (len(raw) // 2), raw)]
    elif width == 3:
        vals = []
        for i in range(0, len(raw), 3):
            v = raw[i] | (raw[i + 1] << 8) | (raw[i + 2] << 16)
            if v & 0x800000:
                v -= 1 << 24
            vals.append(v / 8388608.0)
    elif width == 4:
        vals = [s / 2147483648.0 for s in struct.unpack("<%di" % (len(raw) // 4), raw)]
    else:
        raise ValueError("unsupported sample width: %d bytes" % width)
    if nch > 1:
        mono = []
        for i in range(0, len(vals) - nch + 1, nch):
            mono.append(sum(vals[i:i + nch]) / nch)
        vals = mono
    return vals, rate


def write_wav(path, samples, rate):
    n = len(samples)
    packed = bytearray(n * 2)
    for i, x in enumerate(samples):
        v = int(round(x * 32767))
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        struct.pack_into("<h", packed, i * 2, v)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(packed))


# --------------------------------------------------------------------------- #
# Levels and adaptive noise floor
# --------------------------------------------------------------------------- #
def frame_levels(samples, rate, frame_ms=30, hop_ms=10):
    """Return (times_s, level_db) for overlapping analysis frames."""
    flen = max(1, int(round(rate * frame_ms / 1000.0)))
    hop = max(1, int(round(rate * hop_ms / 1000.0)))
    times, levels = [], []
    total = len(samples)
    i = 0
    while i < total:
        block = samples[i:i + flen]
        if len(block) < flen:
            block = block + [0.0] * (flen - len(block))
        acc = 0.0
        for x in block:
            acc += x * x
        rms = math.sqrt(acc / flen)
        times.append(i / rate)
        levels.append(20.0 * math.log10(rms) if rms > 1e-10 else SILENCE_DB)
        i += hop
    return times, levels


def percentile_sorted(data, p):
    """p in [0,100]; data must already be sorted; linear interpolation."""
    if not data:
        return SILENCE_DB
    if len(data) == 1:
        return data[0]
    pos = (len(data) - 1) * (p / 100.0)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(data) - 1)
    frac = pos - lo
    return data[lo] * (1 - frac) + data[hi] * frac


def estimate_noise_floor(levels, percentile=10.0):
    """Global noise-floor estimate: low percentile of all frame levels."""
    return percentile_sorted(sorted(levels), percentile)


def adaptive_floors(times, levels, window_s=5.0, percentile=10.0):
    """Per-frame local noise floor: low percentile inside a sliding +/-window.

    A sorted sliding window (bisect insert/remove) gives O(n log w) cost and
    lets the threshold track a noise bed that rises and falls over time.
    """
    n = len(times)
    half = window_s / 2.0
    window = []          # sorted frame levels inside the current window
    ordered = []         # frame levels in chronological order (for removal)
    left = right = 0
    floors = [0.0] * n
    for i in range(n):
        t = times[i]
        while right < n and times[right] <= t + half:
            bisect.insort(window, levels[right])
            ordered.append(levels[right])
            right += 1
        while left < right and times[left] < t - half:
            idx = bisect.bisect_left(window, ordered[left])
            window.pop(idx)
            left += 1
        floors[i] = percentile_sorted(window, percentile)
    return floors


# --------------------------------------------------------------------------- #
# Silence runs, split points, segment merging
# --------------------------------------------------------------------------- #
def silence_flags(levels, floors, delta_db):
    """Per-frame boolean: True when the frame is below floor + delta."""
    return [levels[i] < floors[i] + delta_db for i in range(len(levels))]


def detect_silences(times, silent, rate, duration, frame_ms=30,
                    min_silence_s=0.5):
    """Group silent frames into runs.

    Returns (interior, edge) runs as [start_s, end_s] lists. Runs touching
    the first or last frame are "edge" runs: they bound the recording and
    must not produce cut points.
    """
    flen = max(1, int(round(rate * frame_ms / 1000.0)))
    interior, edge = [], []
    i = 0
    n = len(silent)
    while i < n:
        if not silent[i]:
            i += 1
            continue
        j = i
        while j < n and silent[j]:
            j += 1
        start = times[i]
        end = min(times[j - 1] + flen / rate, duration)
        if end - start >= min_silence_s:
            run = [start, end]
            if i == 0 or j == n:
                edge.append(run)
            else:
                interior.append(run)
        i = j
    return interior, edge


def split_points_for_silences(silences, margin_s=0.15):
    """Cut point inside each silence run.

    Preferred point is the run midpoint; it is clamped to
    [start+margin, end-margin]. If the run is shorter than 2*margin the
    midpoint is used (still strictly inside the run).
    """
    points = []
    for start, end in silences:
        mid = (start + end) / 2.0
        lo, hi = start + margin_s, end - margin_s
        point = min(max(mid, lo), hi) if lo <= hi else mid
        points.append(point)
    return points


def merge_short_segments(segments, min_segment_s):
    """Repeatedly merge the shortest-too-short segment with its shorter neighbour.

    Returns (segments, merge_count). A single segment is never dropped, so a
    fully non-silent file stays one segment and an all-silent file yields none.
    """
    segs = [list(s) for s in segments]
    merges = 0
    while len(segs) > 1:
        i = min(range(len(segs)), key=lambda k: segs[k][1] - segs[k][0])
        if segs[i][1] - segs[i][0] >= min_segment_s:
            break
        if i == 0:
            ni = 1
        elif i == len(segs) - 1:
            ni = i - 1
        else:
            prev_d = segs[i][0] - segs[i - 1][0]
            next_d = segs[i + 1][1] - segs[i][1]
            ni = i - 1 if prev_d <= next_d else i + 1
        lo, hi = min(i, ni), max(i, ni)
        segs[lo:hi + 1] = [[segs[lo][0], segs[hi][1]]]
        merges += 1
    return [(a, b) for a, b in segs], merges


# --------------------------------------------------------------------------- #
# Top-level analysis
# --------------------------------------------------------------------------- #
def analyze(samples, rate, frame_ms=30, hop_ms=10, window_s=5.0,
            floor_percentile=10.0, delta_db=10.0, min_silence_s=0.5,
            margin_s=0.15, min_segment_s=2.0, absolute_silence_db=-45.0):
    duration = len(samples) / rate
    times, levels = frame_levels(samples, rate, frame_ms, hop_ms)
    global_floor = estimate_noise_floor(levels, floor_percentile)
    floors = adaptive_floors(times, levels, window_s, floor_percentile)
    silent = silence_flags(levels, floors, delta_db)
    silences, edge_silences = detect_silences(
        times, silent, rate, duration, frame_ms, min_silence_s)

    notes = []
    any_voiced = any(not f for f in silent)
    peak_db = max(levels) if levels else SILENCE_DB
    if not any_voiced:
        # No frame ever rises above its local floor + delta.
        if peak_db < absolute_silence_db:
            notes.append("all-silent: peak %.1f dBFS < %.1f dBFS"
                         % (peak_db, absolute_silence_db))
            raw_segments, segments, merges, points = [], [], 0, []
        else:
            notes.append("no-silence: signal present but no silence run "
                         "qualifies; keeping the file as one segment")
            raw_segments = [(0.0, duration)]
            segments, merges, points = list(raw_segments), 0, []
    else:
        points = split_points_for_silences(silences, margin_s)
        boundaries = [0.0] + points + [duration]
        raw_segments = [(boundaries[k], boundaries[k + 1])
                        for k in range(len(boundaries) - 1)]
        segments, merges = merge_short_segments(raw_segments, min_segment_s)

    return {
        "duration": duration,
        "rate": rate,
        "levels": levels,
        "times": times,
        "peak_db": peak_db,
        "global_floor_db": global_floor,
        "delta_db": delta_db,
        "global_threshold_db": global_floor + delta_db,
        "local_floor_min_db": min(floors) if floors else SILENCE_DB,
        "local_floor_max_db": max(floors) if floors else SILENCE_DB,
        "floors": floors,
        "silences": silences,
        "edge_silences": edge_silences,
        "split_points": points,
        "raw_segments": raw_segments,
        "segments": segments,
        "merge_count": merges,
        "margin_s": margin_s,
        "notes": notes,
    }


def split_wav(in_path, out_dir, **kw):
    samples, rate = read_wav(in_path)
    report = analyze(samples, rate, **kw)
    os.makedirs(out_dir, exist_ok=True)
    for idx, (start, end) in enumerate(report["segments"], 1):
        a = max(0, int(round(start * rate)))
        b = min(len(samples), int(round(end * rate)))
        name = "part_%03d_%.2f-%.2f.wav" % (idx, start, end)
        write_wav(os.path.join(out_dir, name), samples[a:b], rate)
    return report


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def print_report(r):
    print("=" * 68)
    print("noise estimate vs threshold")
    print("-" * 68)
    print("sample rate            : %d Hz" % r["rate"])
    print("duration               : %.3f s" % r["duration"])
    print("delta (margin above floor): %.1f dB" % r["delta_db"])
    print("global noise floor     : %.2f dBFS" % r["global_floor_db"])
    print("global threshold       : %.2f dBFS  (= floor + %.1f)"
          % (r["global_threshold_db"], r["delta_db"]))
    print("local floor range      : %.2f .. %.2f dBFS (sliding window)"
          % (r["local_floor_min_db"], r["local_floor_max_db"]))
    print("silent frame rule      : level_db < local_floor_db + %.1f"
          % r["delta_db"])
    print("peak level             : %.2f dBFS" % r["peak_db"])
    for note in r["notes"]:
        print("note                   : %s" % note)

    print("=" * 68)
    print("split points vs silence intervals (margin = %.2fs)" % r["margin_s"])
    print("-" * 68)
    if not r["silences"]:
        print("(no interior silence interval usable for cutting)")
    for s, e in r["edge_silences"]:
        print("silence [%7.3f, %7.3f] (%.3fs) -> edge of file, no cut"
              % (s, e, e - s))
    kept = set()
    # split points that survive merging
    for a, b in zip(r["segments"][:-1], r["segments"][1:]):
        kept.add(round(b[0], 9))
    for (s, e), p in zip(r["silences"], r["split_points"]):
        status = "kept" if round(p, 9) in kept else "removed-by-merge"
        print("silence [%7.3f, %7.3f] (%.3fs) -> cut %7.3f | "
              "left gap %6.3fs, right gap %6.3fs  [%s]"
              % (s, e, e - s, p, p - s, e - p, status))

    print("=" * 68)
    print("segment statistics")
    print("-" * 68)
    print("segments before merging : %d" % len(r["raw_segments"]))
    print("segments after  merging : %d  (%d merge(s))"
          % (len(r["segments"]), r["merge_count"]))
    for i, (a, b) in enumerate(r["segments"], 1):
        print("  part_%03d  %8.3f -> %8.3f   length %7.3f s"
              % (i, a, b, b - a))
    print("=" * 68)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="input PCM WAV file")
    ap.add_argument("-o", "--outdir", default="parts", help="output directory")
    ap.add_argument("--frame-ms", type=float, default=30)
    ap.add_argument("--hop-ms", type=float, default=10)
    ap.add_argument("--window", type=float, default=5.0,
                    help="noise-floor sliding window, seconds")
    ap.add_argument("--percentile", type=float, default=10.0)
    ap.add_argument("--delta-db", type=float, default=10.0,
                    help="threshold = local_floor + delta_db")
    ap.add_argument("--min-silence", type=float, default=0.5)
    ap.add_argument("--margin", type=float, default=0.15)
    ap.add_argument("--min-segment", type=float, default=2.0)
    ap.add_argument("--absolute-silence-db", type=float, default=-45.0,
                    help="peak below this means the whole file is silence")
    args = ap.parse_args(argv)

    report = split_wav(
        args.input, args.outdir,
        frame_ms=args.frame_ms, hop_ms=args.hop_ms, window_s=args.window,
        floor_percentile=args.percentile, delta_db=args.delta_db,
        min_silence_s=args.min_silence, margin_s=args.margin,
        min_segment_s=args.min_segment,
        absolute_silence_db=args.absolute_silence_db)
    print_report(report)
    return 0 if report["segments"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
