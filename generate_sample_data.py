"""生成采样结果数据文件 sample_data.txt。

内容：
1. 4x4 测试纹理（纹素值 = y * 4 + x）。
2. 三种环绕方式在 1x / 2x / 4x / 8x 放大倍数下的双线性采样结果矩阵。
3. 接缝扫描：固定 v，u 从 -1.25 扫到 2.25，跨 u=0 与 u=1 两条接缝，
   直观对比三种环绕方式在接缝处的差异。
"""

from texture import Texture, WrapMode

SCALES = (1, 2, 4, 8)


def build_texture():
    return Texture(4, 4, [y * 4 + x for y in range(4) for x in range(4)])


def _magnify_block(tex, mode, scale):
    out_w = tex.width * scale
    out_h = tex.height * scale
    lines = ["## mode=%s scale=%dx (%dx%d)"
             % (mode.value, scale, out_w, out_h)]
    for py in range(out_h):
        v = (py + 0.5) / out_h
        cells = []
        for px in range(out_w):
            u = (px + 0.5) / out_w
            cells.append("%7.3f" % tex.sample_bilinear(u, v, mode))
        lines.append("".join(cells))
    return lines


def _seam_block(tex):
    lines = ["## seam scan: v=0.375 (row 1 center), u in [-1.25, 2.25] step 0.125"]
    us = [-1.25 + 0.125 * k for k in range(29)]
    lines.append("u       " + "".join("%8.3f" % u for u in us))
    for mode in WrapMode:
        vals = ["%8.3f" % tex.sample_bilinear(u, 0.375, mode) for u in us]
        lines.append("%-7s " % mode.value + "".join(vals))
    return lines


def generate():
    tex = build_texture()
    out = ["# texture sample data",
           "# source texture 4x4, texel(x, y) = y * 4 + x, texel centers at (i+0.5)/n",
           "#"]
    for y in range(tex.height):
        out.append("#   " + " ".join("%4.1f" % tex.read(x, y)
                                     for x in range(tex.width)))
    out.append("")
    for mode in WrapMode:
        for scale in SCALES:
            out.extend(_magnify_block(tex, mode, scale))
            out.append("")
    out.extend(_seam_block(tex))
    out.append("")
    return "\n".join(out)


def main():
    data = generate()
    with open("sample_data.txt", "w", encoding="utf-8") as f:
        f.write(data)
    print("wrote sample_data.txt (%d bytes)" % len(data))


if __name__ == "__main__":
    main()
