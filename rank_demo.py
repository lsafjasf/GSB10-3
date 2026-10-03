#!/usr/bin/env python3
"""候选排序样例：展示多尺寸/重复尺寸/PNG+BMP 混合图标在不同目标尺寸下的排序。"""

from ico import rank, pick_best
from tests.fixtures import SPECS_MULTI, build_icon

TARGETS = (16, 22, 24, 32, 48, 64, 256)


def main():
    icon = build_icon(SPECS_MULTI)
    print("图标内容（按目录顺序）：")
    for i, im in enumerate(icon.images):
        print("  #%d  %-9s %-2d bpp  %s" %
              (i, "%dx%d" % (im.width, im.height), im.bpp,
               "PNG" if im.compressed else "BMP"))
    print()
    print("排序规则：cost=|log2(候选宽/目标宽)|，放大乘惩罚系数 2；"
          "平局依次按 色深降序 -> PNG 优先 -> 原始索引升序。")
    for target in TARGETS:
        print("\n目标尺寸 %d：" % target)
        for item in rank(icon.images, target):
            print("  %s" % item)
        best = pick_best(icon.images, target)
        print("  => 选中 %dx%d %dbpp %s" %
              (best.width, best.height, best.bpp,
               "PNG" if best.compressed else "BMP"))


if __name__ == "__main__":
    main()
