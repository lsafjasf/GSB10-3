"""tilelib 自测：索引互逆、越界规则、全幅/边缘瓦片、跨 180 度、
面积平均对拍、缓存容量与淘汰。"""

import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tilelib import (
    Grid, OutOfRangeError, TileCache, TileService, make_raster,
    area_average, area_average_reference, nearest_neighbor, max_abs_diff,
)


class TestIndexRoundTrip(unittest.TestCase):
    """行列号 -> 地理范围 -> 行列号 必须互逆。"""

    def test_roundtrip_all_tiles(self):
        # 故意用不能整除的尺寸，把边缘瓦片也纳入互逆检验
        grid = Grid(width=1000, height=700, tile_size=256)
        for row in range(grid.n_rows):
            for col in range(grid.n_cols):
                ext = grid.tile_geo_extent(row, col)
                lon, lat = ext.center
                self.assertEqual((row, col), grid.tile_for_lonlat(lon, lat))

    def test_roundtrip_corners_inside(self):
        grid = Grid(width=512, height=512, tile_size=256)
        for row in range(grid.n_rows):
            for col in range(grid.n_cols):
                ext = grid.tile_geo_extent(row, col)
                eps = 1e-9
                # 四个角略微内缩后仍应落回本瓦片
                for lon in (ext.lon0 + eps, ext.lon1 - eps):
                    for lat in (ext.lat0 + eps, ext.lat1 - eps):
                        self.assertEqual((row, col), grid.tile_for_lonlat(lon, lat))

    def test_pixel_window_geo_consistency(self):
        grid = Grid(width=1000, height=700, tile_size=256)
        row, col = 1, 2
        x0, y0, x1, y1 = grid.tile_pixel_window(row, col)
        ext = grid.tile_geo_extent(row, col)
        self.assertAlmostEqual(ext.lon0, grid.lon_min + x0 * grid.res_x)
        self.assertAlmostEqual(ext.lon1, grid.lon_min + x1 * grid.res_x)
        self.assertAlmostEqual(ext.lat1, grid.lat_max - y0 * grid.res_y)
        self.assertAlmostEqual(ext.lat0, grid.lat_max - y1 * grid.res_y)


class TestOutOfBounds(unittest.TestCase):
    """越界规则：列回绕（经度周期），行报错（纬度有硬边界）。"""

    def setUp(self):
        self.grid = Grid(width=1024, height=512, tile_size=256)  # 4 列 2 行

    def test_col_wraps(self):
        self.assertEqual(self.grid.normalize_col(4), 0)
        self.assertEqual(self.grid.normalize_col(-1), 3)
        self.assertEqual(self.grid.normalize_col(9), 1)
        # 回绕后的瓦片与正列号瓦片完全一致
        self.assertEqual(self.grid.tile_pixel_window(0, -1),
                         self.grid.tile_pixel_window(0, 3))

    def test_row_rejected(self):
        for bad in (-1, 2, 100):
            with self.assertRaises(OutOfRangeError):
                self.grid.tile_pixel_window(bad, 0)
            with self.assertRaises(OutOfRangeError):
                self.grid.tile_geo_extent(bad, 0)

    def test_lat_rejected(self):
        with self.assertRaises(OutOfRangeError):
            self.grid.tile_for_lonlat(0.0, 90.0001)
        with self.assertRaises(OutOfRangeError):
            self.grid.tile_for_lonlat(0.0, -90.0001)

    def test_lon_wraps(self):
        # 180 与 -180 是同一位置；181 回绕到 -179
        self.assertEqual(self.grid.tile_for_lonlat(180.0, 0.0),
                         self.grid.tile_for_lonlat(-180.0, 0.0))
        self.assertEqual(self.grid.tile_for_lonlat(181.0, 0.0),
                         self.grid.tile_for_lonlat(-179.0, 0.0))
        self.assertEqual(self.grid.tile_for_lonlat(540.0, 0.0),
                         self.grid.tile_for_lonlat(180.0, 0.0))


class TestFullAndEdgeTiles(unittest.TestCase):
    """单块全幅与边缘不满。"""

    def test_full_tile(self):
        raster = make_raster(512, 512, lambda x, y: x + y * 512)
        grid = Grid(width=512, height=512, tile_size=256)
        svc = TileService(raster, grid)
        tile = svc.get_tile(0, 0)
        self.assertEqual((tile.valid_w, tile.valid_h), (256, 256))
        self.assertAlmostEqual(tile.coverage, 1.0)
        # 1:1 无缩放时面积平均应原样返回源像素
        for y in range(256):
            for x in range(256):
                self.assertAlmostEqual(tile.data[y][x], raster[y][x])

    def test_edge_tile_partial(self):
        raster = make_raster(1000, 700, lambda x, y: 1.0)
        grid = Grid(width=1000, height=700, tile_size=256)
        svc = TileService(raster, grid)
        # 最右列：1000 = 3*256 + 232，有效宽 232
        right = svc.get_tile(0, 3)
        self.assertEqual((right.valid_w, right.valid_h), (232, 256))
        # 右下角：有效 232 x 188
        corner = svc.get_tile(2, 3)
        self.assertEqual((corner.valid_w, corner.valid_h), (232, 188))
        self.assertAlmostEqual(corner.coverage, 232 * 188 / (256 * 256))
        self.assertLess(corner.coverage, 1.0)
        # 常数场面积平均后仍为常数
        for row in corner.data:
            for v in row:
                self.assertAlmostEqual(v, 1.0)

    def test_edge_geo_extent_clipped(self):
        grid = Grid(width=1000, height=700, tile_size=256)
        ext = grid.tile_geo_extent(0, 3)
        self.assertAlmostEqual(ext.lon1, 180.0)  # 右边缘裁到栅格边界
        ext_bottom = grid.tile_geo_extent(2, 0)
        self.assertAlmostEqual(ext_bottom.lat0, -90.0)


class TestAntimeridian(unittest.TestCase):
    """跨经度 180 度。"""

    def setUp(self):
        # 4 列：col0 [-180,-90), col1 [-90,0), col2 [0,90), col3 [90,180]
        self.grid = Grid(width=1024, height=512, tile_size=256)

    def test_bbox_crossing_180(self):
        tiles = self.grid.tiles_for_bbox(170.0, -10.0, -170.0, 10.0)
        cols = {c for _, c in tiles}
        self.assertEqual(cols, {0, 3})  # 同时命中东西两端的列

    def test_bbox_not_crossing(self):
        tiles = self.grid.tiles_for_bbox(-170.0, -10.0, -100.0, 10.0)
        cols = {c for _, c in tiles}
        self.assertEqual(cols, {0})

    def test_adjacent_across_antimeridian(self):
        # 179.9 与 -179.9 地理上相邻，列号分别是 3 和 0
        _, c_east = self.grid.tile_for_lonlat(179.9, 0.0)
        _, c_west = self.grid.tile_for_lonlat(-179.9, 0.0)
        self.assertEqual((c_east, c_west), (3, 0))
        # 通过列回绕，3 的下一列就是 0
        self.assertEqual(self.grid.normalize_col(c_east + 1), c_west)


class TestAreaAverage(unittest.TestCase):
    """面积平均：手算真值 + 与参考实现对拍 + 与最近邻区分。"""

    def test_known_values_2x_downsample(self):
        # 4x4 值 0..15 缩到 2x2，每块 2x2 取平均
        src = [[float(y * 4 + x) for x in range(4)] for y in range(4)]
        out = area_average(src, (0, 0, 4, 4), 2, 2)
        expect = [[2.5, 4.5], [10.5, 12.5]]
        for j in range(2):
            for i in range(2):
                self.assertAlmostEqual(out[j][i], expect[j][i])

    def test_fractional_window(self):
        # 窗口 (0.5, 0.5, 2.5, 2.5) 输出 1x1：
        # 覆盖中心像素 1 份，边像素 0.5，角像素 0.25 的加权平均
        src = [[float(y * 4 + x) for x in range(4)] for y in range(4)]
        out = area_average(src, (0.5, 0.5, 2.5, 2.5), 1, 1)
        ref = area_average_reference(src, (0.5, 0.5, 2.5, 2.5), 1, 1)
        self.assertAlmostEqual(out[0][0], ref[0][0], places=12)
        # 手算：权重为各像素与 [0.5,2.5)^2 的交集面积 / 4
        total = 0.0
        for sy in range(3):
            for sx in range(3):
                ox = min(2.5, sx + 1) - max(0.5, sx)
                oy = min(2.5, sy + 1) - max(0.5, sy)
                total += ox * oy * src[sy][sx]
        self.assertAlmostEqual(out[0][0], total / 4.0, places=12)

    def test_matches_reference_random(self):
        rng = random.Random(42)
        for _ in range(20):
            w, h = 37, 23
            src = make_raster(w, h, lambda x, y: rng.uniform(-100, 100))
            x0 = rng.uniform(0, w / 2)
            y0 = rng.uniform(0, h / 2)
            x1 = rng.uniform(w / 2 + 1, w)
            y1 = rng.uniform(h / 2 + 1, h)
            ow, oh = rng.randint(1, 40), rng.randint(1, 40)
            a = area_average(src, (x0, y0, x1, y1), ow, oh)
            b = area_average_reference(src, (x0, y0, x1, y1), ow, oh)
            self.assertLess(max_abs_diff(a, b), 1e-9)

    def test_differs_from_nearest_neighbor(self):
        # 渐变场下采样，面积平均与最近邻必须给出不同结果
        src = make_raster(64, 64, lambda x, y: x * 2.0 + y)
        aa = area_average(src, (0, 0, 64, 64), 8, 8)
        nn = nearest_neighbor(src, (0, 0, 64, 64), 8, 8)
        self.assertGreater(max_abs_diff(aa, nn), 1.0)

    def test_total_preservation(self):
        # 面积平均下采样保持加权和（均值意义下守恒）
        src = make_raster(32, 32, lambda x, y: (x * 7 + y * 13) % 11)
        out = area_average(src, (0, 0, 32, 32), 8, 8)
        mean_src = sum(sum(r) for r in src) / (32 * 32)
        mean_out = sum(sum(r) for r in out) / 64
        self.assertAlmostEqual(mean_src, mean_out, places=9)


class TestTileCache(unittest.TestCase):
    """容量上界、LRU 淘汰顺序、命中率数据。"""

    def test_capacity_bound_and_eviction_order(self):
        cache = TileCache(capacity=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.get("a")          # a 变为最近使用
        cache.put("c", 3)       # 应淘汰 b
        self.assertLessEqual(cache.size, 2)
        self.assertIsNone(cache.get("b"))
        self.assertEqual(cache.get("a"), 1)
        self.assertEqual(cache.get("c"), 3)
        self.assertEqual(cache.eviction_log[0]["key"], "b")
        self.assertEqual(cache.eviction_log[0]["reason"], "capacity")

    def test_hit_rate_stats(self):
        cache = TileCache(capacity=4)
        for i in range(4):
            cache.put(i, i)
        for i in range(4):
            cache.get(i)          # 4 次命中
        for i in range(4, 8):
            cache.get(i)          # 4 次未命中
        stats = cache.stats()
        self.assertEqual(stats["hits"], 4)
        self.assertEqual(stats["misses"], 4)
        self.assertAlmostEqual(stats["hit_rate"], 0.5)
        self.assertEqual(stats["capacity"], 4)

    def test_service_uses_cache(self):
        raster = make_raster(512, 512, lambda x, y: x + y)
        grid = Grid(width=512, height=512, tile_size=256)
        cache = TileCache(capacity=3)
        svc = TileService(raster, grid, cache=cache)
        svc.get_tile(0, 0)
        svc.get_tile(0, 0)  # 命中
        svc.get_tile(0, 1)
        svc.get_tile(1, 0)
        svc.get_tile(1, 1)  # 容量 3，第 4 块触发淘汰最久未用的 (0,0)
        stats = cache.stats()
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 4)
        self.assertEqual(stats["evictions"], 1)
        self.assertEqual(cache.eviction_log[0]["key"], [0, 0])
        self.assertLessEqual(cache.size, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
