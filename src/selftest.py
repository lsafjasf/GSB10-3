"""自测：手算向量、逐行对拍、边界用例。

运行：python3 src/selftest.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rowfilter as rf
import images


PASS_COUNT = 0


def check(cond, msg):
    assert cond, msg
    global PASS_COUNT
    PASS_COUNT += 1


def test_paeth_predictor():
    # 手算 Paeth 预测器（PNG 规范示例规则）
    cases = [
        (0, 0, 0, 0),
        (50, 60, 40, 60),    # p=70: pa20 pb10 pc30 -> b
        (100, 10, 0, 100),   # p=110: pa10 pb100 pc110 -> a
        (10, 10, 20, 10),    # p=0: pa10 pb10 pc20, 并列取 a
        (5, 9, 9, 5),        # p=5: pa0 pb4 pc4 -> a
        (200, 201, 202, 200),
    ]
    for a, b, c, expect in cases:
        check(rf.paeth_predictor(a, b, c) == expect,
              "paeth(%d,%d,%d) 应为 %d" % (a, b, c, expect))


def test_filter_vectors():
    # 手算前向滤波向量
    row = bytes([10, 20, 30])
    prev = bytes([1, 2, 3])

    check(rf.filter_row(row, b"", 1, 0) == bytes([10, 20, 30]), "None 向量错误")
    check(rf.filter_row(row, b"", 1, 1) == bytes([10, 10, 10]), "Sub 向量错误")
    check(rf.filter_row(row, prev, 1, 2) == bytes([9, 18, 27]), "Up 向量错误")

    # Average: floor((a+b)/2) -> [0, 6, 11], 残差 [10,14,19]
    check(rf.filter_row(row, prev, 1, 3) == bytes([10, 14, 19]), "Average 向量错误")

    # bpp=3 的 Sub：第二个像素相对第一个像素做差
    rgb = bytes([100, 100, 100, 110, 110, 110])
    check(rf.filter_row(rgb, b"", 3, 1) == bytes([100, 100, 100, 10, 10, 10]),
          "bpp=3 Sub 向量错误")


def test_unfilter_vectors():
    # 由残差行反推，并与已知原始行逐字节比较
    row = bytes([10, 20, 30])
    prev = bytes([4, 8, 12])
    for ftype in rf.FILTERS:
        filt = rf.filter_row(row, prev, 1, ftype)
        recon = rf.unfilter_row(filt, prev, 1, ftype)
        check(recon == row, "filter %d 单行还原失败: %r != %r"
              % (ftype, recon, row))


def test_roundtrip_rows(name, raw, width, height, bpp):
    """编码-解码全流程，并逐行对拍。"""
    stride = width * bpp
    blob = rf.encode(raw, width, height, bpp)
    decoded, dw, dh, dbpp, choices = rf.decode(blob)

    check((dw, dh, dbpp) == (width, height, bpp), "%s: 头信息不一致" % name)
    check(len(choices) == height, "%s: choices 行数错误" % name)
    check(len(decoded) == len(raw), "%s: 还原长度错误" % name)

    # —— 逐行对拍断言 ——
    for y in range(height):
        got = decoded[y * stride:(y + 1) * stride]
        want = raw[y * stride:(y + 1) * stride]
        check(got == want, "%s: 第 %d 行逐字节不一致" % (name, y))

    # decode 给出的 choices 必须与独立计算的分布一致
    choices2, _ = rf.filter_distribution(raw, width, height, bpp)
    check(choices == choices2, "%s: 选择分布与编码不一致" % name)


def test_all_edge_cases():
    # 单像素宽、单行高、1x1，多 bpp
    edge_specs = [
        ("1x1 灰度", 1, 1, 1),
        ("1x1 RGB", 1, 1, 3),
        ("1x1 RGBA", 1, 1, 4),
        ("单像素宽 1x20", 1, 20, 1),
        ("单像素宽 1x20 RGB", 1, 20, 3),
        ("单行高 20x1", 20, 1, 1),
        ("单行高 20x1 RGBA", 20, 1, 4),
        ("2x2 全bpp", 2, 2, 1),
    ]
    for name, w, h, bpp in edge_specs:
        for kind, img in [
            ("同色", images.make_solid(w, h, bpp, 200)),
            ("噪声", images.make_noise(w, h, bpp, seed=w * 31 + h + bpp)),
            ("渐变", images.make_vgradient(w, h, bpp)),
        ]:
            test_roundtrip_rows("%s/%s" % (name, kind), *img)

    # 单行高时任何 Up/Average/Paeth 的“上一行”退化为 0，也必须正确还原
    raw = bytes(range(1, 13))
    for ftype in rf.FILTERS:
        blob = rf.encode(raw, 3, 1, 4, force_filter=ftype)
        decoded, _, _, _, choices = rf.decode(blob)
        check(decoded == raw and choices == [ftype], "单行高强制 filter %d 失败" % ftype)


def test_exhaustive_small():
    # 小尺寸随机图穷举式对拍（多种尺寸/bpp/内容）
    for seed in range(30):
        for bpp in (1, 3, 4):
            for w, h in [(1, 1), (1, 5), (5, 1), (2, 3), (3, 2), (4, 4)]:
                raw, _, _, _ = images.make_noise(w, h, bpp, seed=seed * 1000 + bpp * 17 + w * 7 + h)
                test_roundtrip_rows("穷举 seed=%d %dx%d bpp=%d" % (seed, w, h, bpp),
                                    raw, w, h, bpp)


def test_all_image_types_roundtrip():
    for name, img in images.all_types(64, 48, 3).items():
        test_roundtrip_rows(name, *img)


def test_forced_filters_and_cost():
    # 每种图像类型：自适应的逐行代价和 <= 任意单一强制滤波器的代价和
    for name, (raw, w, h, bpp) in images.all_types(32, 24, 3).items():
        _, adaptive_counts = rf.filter_distribution(raw, w, h, bpp)
        stride = w * bpp

        def total_cost(force):
            total = 0
            prev = b""
            for y in range(h):
                row = raw[y * stride:(y + 1) * stride]
                if force is None:
                    _, _, c = rf.choose_filter(row, prev, bpp)
                else:
                    c = rf.filtered_cost(rf.filter_row(row, prev, bpp, force))
                total += c
                prev = row
            return total

        adaptive_cost = total_cost(None)
        for ftype in rf.FILTERS:
            check(adaptive_cost <= total_cost(ftype),
                  "%s: 自适应代价(%d) 大于强制 %s(%d)"
                  % (name, adaptive_cost, rf.FILTER_NAMES[ftype], total_cost(ftype)))

        # 同色图首行之后 Up 残差全为 0，应几乎全选 Up
        solid, _, _, _ = images.make_solid(32, 24, 3)
        _, solid_counts = rf.filter_distribution(solid, 32, 24, 3)
        check(solid_counts[2] >= 23, "同色图应几乎全选 Up，实际 %r" % solid_counts)


def test_encode_auto():
    # encode_auto 体积不逊于任何单一滤波器，且可无损往返
    for name, (raw, w, h, bpp) in images.all_types(32, 24, 3).items():
        blob = rf.encode_auto(raw, w, h, bpp)
        decoded, dw, dh, dbpp, _ = rf.decode(blob)
        check(decoded == raw and (dw, dh, dbpp) == (w, h, bpp),
              "%s: encode_auto 往返失败" % name)
        for f in rf.FILTERS:
            single = len(rf.encode(raw, w, h, bpp, force_filter=f))
            check(len(blob) <= single, "%s: encode_auto(%d) 大于单一滤波器 %s(%d)"
                  % (name, len(blob), rf.FILTER_NAMES[f], single))


def test_invalid_inputs():
    def raises(fn):
        try:
            fn()
        except Exception:
            return True
        return False

    check(raises(lambda: rf.encode(b"\x00", 2, 1, 1)), "长度不符应报错")
    check(raises(lambda: rf.encode(b"", 0, 1, 1)), "width=0 应报错")
    check(raises(lambda: rf.encode(b"\x00", 1, 1, 1, force_filter=9)), "非法 filter 应报错")
    check(raises(lambda: rf.decode(b"XXXX" + b"\x00" * 10)), "非法文件头应报错")


def main():
    test_paeth_predictor()
    test_filter_vectors()
    test_unfilter_vectors()
    test_all_edge_cases()
    test_exhaustive_small()
    test_all_image_types_roundtrip()
    test_forced_filters_and_cost()
    test_encode_auto()
    test_invalid_inputs()
    print("全部通过：%d 条断言（含逐行对拍、边界用例、7 类图像往返）" % PASS_COUNT)


if __name__ == "__main__":
    main()
