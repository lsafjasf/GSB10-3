#!/usr/bin/env python3
"""往返对拍：构造 ICO -> 写出 -> 读回 -> 再写出，逐字节比对并输出对拍数据。

用法：python3 roundtrip.py [输出目录]   （默认 artifacts/）
"""

import hashlib
import json
import os
import sys

from ico import IconFile
from tests.fixtures import SPECS_SINGLE, SPECS_MULTI, SPECS_ALPHA, build_icon

CASES = {
    "single_32bpp": SPECS_SINGLE,
    "multi_mixed": SPECS_MULTI,
    "alpha_32bpp": SPECS_ALPHA,
    "edge_256_and_tiny": [dict(size=1, bpp=32), dict(size=256, bpp=32, fmt="png")],
    "palette_8bpp": [dict(size=16, bpp=8), dict(size=32, bpp=8)],
}


def run_case(name, specs, outdir):
    icon = build_icon(specs)
    data1 = icon.to_bytes()
    reparsed = IconFile.read(data1)
    data2 = reparsed.to_bytes()

    assert data1 == data2, "%s: 往返写出不一致" % name
    assert len(reparsed) == len(icon), "%s: 图像数量不一致" % name
    for original, got in zip(icon.images, reparsed.images):
        assert got.data == original.data, "%s: 载荷逐字节比对失败" % name
        assert (got.width, got.height, got.bpp, got.compressed) == \
               (original.width, original.height, original.bpp, original.compressed)

    path = os.path.join(outdir, name + ".ico")
    with open(path, "wb") as fh:
        fh.write(data1)
    return {
        "file": os.path.relpath(path),
        "images": len(icon),
        "bytes": len(data1),
        "sha256": hashlib.sha256(data1).hexdigest(),
        "roundtrip_identical": True,
        "entries": [
            {"size": "%dx%d" % (im.width, im.height), "bpp": im.bpp,
             "format": "PNG" if im.compressed else "BMP",
             "payload_bytes": len(im.data)}
            for im in reparsed.images
        ],
    }


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "artifacts"
    os.makedirs(outdir, exist_ok=True)
    results = {name: run_case(name, specs, outdir) for name, specs in CASES.items()}

    manifest_path = os.path.join(outdir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)

    print("往返对拍结果（写出 -> 读回 -> 再写出，逐字节一致）：")
    for name, info in results.items():
        print("  %-18s %d 项  %6d 字节  sha256=%s" %
              (name, info["images"], info["bytes"], info["sha256"][:16]))
        for i, entry in enumerate(info["entries"], 1):
            print("      #%d %-9s %-3d bpp %-4s 载荷 %d 字节"
                  % (i, entry["size"], entry["bpp"], entry["format"],
                     entry["payload_bytes"]))
    print("对拍清单已写入 %s" % manifest_path)
    print("全部 %d 个用例往返一致。" % len(results))


if __name__ == "__main__":
    main()
