#!/usr/bin/env python3
"""生成交付数据：面积平均数值对拍、缓存命中/淘汰过程、边界用例。

用法：python3 run_demo.py
输出：out/resample_comparison.json, out/cache_stats.json, out/edge_cases.json
"""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tilelib import (
    Grid, OutOfRangeError, TileCache, TileService, make_raster,
    area_average, area_average_reference, nearest_neighbor, max_abs_diff,
)

OUT_DIR = Path(__file__).resolve().parent / "out"


def resample_comparison():
    """面积平均两遍实现 vs 参考实现的数值对拍，并与最近邻对照。"""
    cases = []

    def add_case(name, src, window, out_w, out_h):
        a = area_average(src, window, out_w, out_h)
        ref = area_average_reference(src, window, out_w, out_h)
        nn = nearest_neighbor(src, window, out_w, out_h)
        cases.append({
            "case": name,
            "src_size": [len(src[0]), len(src)],
            "window": list(window),
            "out_size": [out_w, out_h],
            "area_average_sample": [row[:4] for row in a[:4]],
            "max_abs_diff_area_avg_vs_reference": max_abs_diff(a, ref),
            "max_abs_diff_nearest_neighbor_vs_reference": max_abs_diff(nn, ref),
        })

    # 1. 整数倍下采样，4x4 -> 2x2（手算真值 2.5/4.5/10.5/12.5）
    src1 = [[float(y * 4 + x) for x in range(4)] for y in range(4)]
    add_case("integer_ratio_downsample_4x4_to_2x2", src1, (0, 0, 4, 4), 2, 2)

    # 2. 非整数倍下采样 37x23 -> 16x9（典型瓦片取窗缩放）
    rng = random.Random(42)
    src2 = make_raster(37, 23, lambda x, y: rng.uniform(-100, 100))
    add_case("non_integer_ratio_37x23_to_16x9", src2, (0, 0, 37, 23), 16, 9)

    # 3. 浮点窗口（跨像素边界）10x10 -> 8x8
    src3 = make_raster(10, 10, lambda x, y: (x * 3 + y * 5) % 7)
    add_case("fractional_window_10x10_to_8x8", src3, (0.3, 0.7, 9.1, 8.9), 8, 8)

    # 4. 放大 8x6 -> 16x12
    src4 = make_raster(8, 6, lambda x, y: x * x + y)
    add_case("upscale_8x6_to_16x12", src4, (0, 0, 8, 6), 16, 12)

    # 5. 边缘不满窗口：232x188 -> 256x... 实际服务按比例缩到 232x188（见 edge_cases）
    src5 = make_raster(232, 188, lambda x, y: (x + y) % 256)
    add_case("edge_partial_window_232x188_to_232x188", src5, (0, 0, 232, 188), 232, 188)

    result = {
        "method": "area average (exact pixel-overlap weights)",
        "reference": "area_average_reference: brute-force 2D per-output-pixel summation",
        "tolerance_note": "two implementations are mathematically identical; "
                          "diff comes only from float summation order",
        "cases": cases,
    }
    return result


def cache_trace():
    """固定请求序列下的缓存命中/淘汰全过程。"""
    raster = make_raster(1024, 1024, lambda x, y: x + y)
    grid = Grid(width=1024, height=1024, tile_size=256)  # 4x4 = 16 块
    cache = TileCache(capacity=4)
    svc = TileService(raster, grid, cache=cache)

    # 请求序列：含重复访问、局部工作集回扫、全部 16 块扫一遍
    requests = [
        (0, 0), (0, 0), (0, 1), (0, 2), (0, 3),
        (1, 0), (0, 3), (1, 1), (1, 2), (1, 3),
        (2, 0), (2, 1), (2, 2), (2, 3),
        (3, 0), (3, 1), (3, 2), (3, 3),
        (0, 0),  # 已被淘汰 -> 未命中
        (3, 3),  # 最近访问过 -> 命中
    ]
    trace = []
    for row, col in requests:
        before_evict = len(cache.eviction_log)
        outcome = "hit" if (row, col) in cache else "miss"
        svc.get_tile(row, col)  # 每个请求只走一次服务
        new_evictions = cache.eviction_log[before_evict:]
        trace.append({
            "request": [row, col],
            "outcome": outcome,
            "evicted": [e["key"] for e in new_evictions],
            "cache_keys_after": [list(k) for k in cache.keys()],
        })
    return {
        "capacity": cache.capacity,
        "eviction_policy": "LRU (least-recently-used, access moves key to most-recent end)",
        "trace": trace,
        "eviction_log": cache.eviction_log,
        "stats": cache.stats(),
    }


def edge_cases():
    """边界用例汇总。"""
    out = {}

    # 1. 单块全幅
    grid_full = Grid(width=512, height=512, tile_size=256)
    raster_full = make_raster(512, 512, lambda x, y: x + y)
    t = TileService(raster_full, grid_full).get_tile(0, 0)
    out["full_tile"] = {
        "grid": "512x512, tile_size=256", "requested": [0, 0],
        "valid_size": [t.valid_w, t.valid_h], "coverage": t.coverage,
        "geo_extent": t.geo,
    }

    # 2. 边缘不满（1000x700 对 256 不齐）
    grid_edge = Grid(width=1000, height=700, tile_size=256)
    raster_edge = make_raster(1000, 700, lambda x, y: (x * 2 + y) % 256)
    svc = TileService(raster_edge, grid_edge)
    corner = svc.get_tile(2, 3)
    out["edge_partial_tile"] = {
        "grid": "1000x700, tile_size=256 (n_cols=4, n_rows=3)",
        "requested": [2, 3],
        "pixel_window": list(grid_edge.tile_pixel_window(2, 3)),
        "valid_size": [corner.valid_w, corner.valid_h],
        "coverage": corner.coverage,
        "geo_extent": corner.geo,
        "note": "coverage < 1.0 的瓦片仅含真实栅格区域，不做最近邻补边",
    }

    # 3. 跨经度 180 度（4 列网格：col3=[90,180], col0=[-180,-90)）
    grid_anti = Grid(width=1024, height=512, tile_size=256)
    tiles = grid_anti.tiles_for_bbox(170.0, -10.0, -170.0, 10.0)
    out["antimeridian_crossing"] = {
        "grid": "1024x512, tile_size=256 (4 cols x 2 rows)",
        "bbox": [170.0, -10.0, -170.0, 10.0],
        "convention": "lon0 > lon1 表示跨反经线，区间拆成 [170,180] 与 [-180,-170]",
        "tiles": [list(t) for t in tiles],
        "hit_columns": sorted({c for _, c in tiles}),
        "lon_180_equals_minus_180":
            grid_anti.tile_for_lonlat(180.0, 0.0) == grid_anti.tile_for_lonlat(-180.0, 0.0),
        "negative_col_wraps":
            grid_anti.tile_pixel_window(0, -1) == grid_anti.tile_pixel_window(0, 3),
    }

    # 4. 行列号越界
    err_rows = []
    for bad in (-1, 2, 100):
        try:
            grid_full.check_row(bad)
        except OutOfRangeError as exc:
            err_rows.append({"requested_row": bad, "error": str(exc)})
    out["out_of_bounds"] = {
        "row_rule": "拒绝并抛 OutOfRangeError：纬度在极点有硬边界，越界无对应区域",
        "col_rule": "取模回绕 col % n_cols：经度为周期坐标（360 度首尾相接）",
        "row_errors": err_rows,
        "col_examples": [
            {"requested_col": -1, "normalized": grid_full.normalize_col(-1)},
            {"requested_col": 4, "normalized": grid_full.normalize_col(4)},
            {"requested_col": 7, "normalized": grid_full.normalize_col(7)},
        ],
        "lat_errors": [],
    }
    for bad_lat in (90.0001, -90.0001):
        try:
            grid_full.tile_for_lonlat(0.0, bad_lat)
        except OutOfRangeError as exc:
            out["out_of_bounds"]["lat_errors"].append({"lat": bad_lat, "error": str(exc)})

    return out


def main():
    OUT_DIR.mkdir(exist_ok=True)
    payloads = {
        "resample_comparison.json": resample_comparison(),
        "cache_stats.json": cache_trace(),
        "edge_cases.json": edge_cases(),
    }
    for name, payload in payloads.items():
        path = OUT_DIR / name
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"wrote {path}")

    stats = payloads["cache_stats.json"]["stats"]
    worst = max(c["max_abs_diff_area_avg_vs_reference"]
                for c in payloads["resample_comparison.json"]["cases"])
    print(f"area-average vs reference worst diff: {worst:.3e}")
    print(f"cache: {stats['hits']} hits / {stats['requests']} requests, "
          f"hit_rate={stats['hit_rate']:.3f}, evictions={stats['evictions']}")


if __name__ == "__main__":
    main()
