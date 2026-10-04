"""mixcat: 多段 WAV 统一响度 + 交叉淡出拼接的命令行入口。

用法:
  python3 mixcat.py 输出.wav 输入1.wav 输入2.wav [...] \
      [--target -20] [--metric rms|peak] [--ceiling -0.3] \
      [--fade 0.05] [--curve sine|linear|exp]
"""
import argparse
import sys

from audionorm import concat, loudness_report, normalize_gain, read_wav, write_wav, NEG_INF


def fmt(db):
    return "-inf" if db == NEG_INF else f"{db:+.2f}"


def main(argv=None):
    ap = argparse.ArgumentParser(description="统一响度并交叉淡出拼接多段 WAV")
    ap.add_argument("output")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--target", type=float, default=-20.0, help="目标响度 dBFS")
    ap.add_argument("--metric", choices=["rms", "peak"], default="rms", help="响度口径")
    ap.add_argument("--ceiling", type=float, default=-0.3, help="峰值上限 dBFS")
    ap.add_argument("--fade", type=float, default=0.05, help="交叉淡出时长(秒)")
    ap.add_argument("--curve", choices=["sine", "linear", "exp"], default="sine")
    args = ap.parse_args(argv)

    segs = []
    for path in args.inputs:
        a = read_wav(path)
        before = loudness_report(a)
        rep = normalize_gain(a, args.target, args.metric, args.ceiling)
        after = loudness_report(a)
        note = " [静音跳过]" if rep.skipped else \
               f" [峰值限制, 避免削顶 {rep.would_clip_samples} 样本]" if rep.limited else ""
        print(f"{path}: rms {fmt(before['rms_dbfs'])}->{fmt(after['rms_dbfs'])} dBFS, "
              f"peak {fmt(after['peak_dbfs'])} dBFS, 增益 {fmt(rep.applied_gain_db)} dB, "
              f"实削顶 {rep.clipped_samples}{note}")
        segs.append(a)

    joined, reports = concat(segs, args.fade, args.curve)
    for i, rep in enumerate(reports):
        print(f"拼接点 {i + 1}: 重叠 {rep.overlap_samples} 样本, "
              f"点前 {fmt(rep.rms_before_dbfs)} / 重叠 {fmt(rep.rms_overlap_dbfs)} / "
              f"点后 {fmt(rep.rms_after_dbfs)} dBFS")
    write_wav(joined, args.output)
    print(f"写出 {args.output}: {joined.duration:.2f}s, {joined.n_channels}ch, "
          f"{joined.sample_rate}Hz")


if __name__ == "__main__":
    sys.exit(main())
