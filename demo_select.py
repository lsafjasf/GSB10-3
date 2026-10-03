#!/usr/bin/env python3
"""demo_select.py — 候选挑选/排序样例。

构造一个含多尺寸、同尺寸不同色深、PNG/BMP 混合的图标，
对多个目标尺寸打印完整排序顺序，并演示平局时的确定性打破规则。
"""
import icoformat
from icoformat import IconImage
from icon_samples import make_bmp, make_png


def describe(img: IconImage) -> str:
    tag = "PNG" if img.compressed else "BMP"
    alpha = "+alpha" if img.has_alpha else ""
    return f"#{img.index} {img.width}x{img.height} {img.bit_count}bpp {tag}{alpha}"


def main() -> None:
    images = [
        IconImage(16, 16, 32, make_bmp(16, 16, 32)),                    # 0
        IconImage(24, 24, 24, make_bmp(24, 24, 24)),                    # 1
        IconImage(32, 32, 24, make_bmp(32, 32, 24)),                    # 2
        IconImage(32, 32, 32, make_bmp(32, 32, 32, alpha_mode="sparse")),  # 3
        IconImage(48, 48, 8, make_bmp(48, 48, 8)),                      # 4
        IconImage(256, 256, 32, make_png(256, 256), compressed=True),   # 5
    ]

    for target in (32, 40, 16, 20, 128):
        ranked = icoformat.rank(images, target)
        print(f"目标 {target}x{target}，候选从优到劣：")
        for place, img in enumerate(ranked, 1):
            mark = "  <- 选中" if place == 1 else ""
            print(f"  {place}. [目录项 {images.index(img)}] {describe(img)}{mark}")
        print()

    # 平局演示：两个候选在尺寸/色深/压缩方式上完全相同，
    # 仅目录序号不同；多次运行必须稳定返回同一结果。
    a = IconImage(32, 32, 32, make_bmp(32, 32, 32))
    b = IconImage(32, 32, 32, make_bmp(32, 32, 32))
    picks = {icoformat.select([a, b], 32) is a for _ in range(20)}
    print(f"完全等价候选的平局结果（重复 20 次）: 稳定选中目录序号较小者 -> {picks == {True}}")

    # 同尺寸 24bpp vs 32bpp：缩放代价相同 -> 高色深优先
    assert icoformat.select([images[2], images[3]], 32) is images[3]
    print("同尺寸平局规则：32x32 32bpp(带透明) 优先于 32x32 24bpp")


if __name__ == "__main__":
    main()
