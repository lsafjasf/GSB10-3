"""瓦片网格索引：行列号 <-> 地理范围 的互逆换算。

坐标约定：
  - 栅格为规则经纬网格，左上角为 (lon_min, lat_max)，行号向下增大，列号向右增大。
  - 瓦片为正方形，边长 tile_size 个栅格像素；栅格宽高不是 tile_size 整数倍时，
    最右列 / 最下行的边缘瓦片不满（只覆盖部分像素）。

越界处理规则（依据）：
  - 列号（经度方向）：取模回绕，col = col % n_cols。
    依据：经度是周期坐标，东西向首尾相接（周期 360 度），
    因此列号越界在几何上对应同一位置，回绕是唯一自洽的解释；
    这也天然支持跨经度 180 度（反经线）的请求。
  - 行号（纬度方向）：拒绝，抛出 OutOfRangeError。
    依据：纬度在极点处终止（[-90, 90] 有硬边界），
    越过极点的行号在地理上没有对应区域，回绕会给出错误数据，
    因此明确报错而不是静默返回错误瓦片。
"""

from __future__ import annotations

import math
from dataclasses import dataclass


class OutOfRangeError(Exception):
    """行号（纬度方向）越界时抛出。"""


@dataclass(frozen=True)
class GeoExtent:
    """地理范围：lon0 西, lat0 南, lon1 东, lat1 北（度）。"""

    lon0: float
    lat0: float
    lon1: float
    lat1: float

    @property
    def center(self):
        return ((self.lon0 + self.lon1) / 2.0, (self.lat0 + self.lat1) / 2.0)


@dataclass(frozen=True)
class Grid:
    """规则经纬栅格 + 瓦片切分方案。"""

    width: int            # 栅格宽（像素）
    height: int           # 栅格高（像素）
    tile_size: int = 256  # 瓦片边长（栅格像素）
    lon_min: float = -180.0
    lon_max: float = 180.0
    lat_min: float = -90.0
    lat_max: float = 90.0

    def __post_init__(self):
        if self.width <= 0 or self.height <= 0:
            raise ValueError("width/height must be positive")
        if self.tile_size <= 0:
            raise ValueError("tile_size must be positive")
        if not (self.lon_min < self.lon_max and self.lat_min < self.lat_max):
            raise ValueError("invalid geographic extent")

    # ---- 派生量 ----

    @property
    def n_cols(self) -> int:
        return math.ceil(self.width / self.tile_size)

    @property
    def n_rows(self) -> int:
        return math.ceil(self.height / self.tile_size)

    @property
    def res_x(self) -> float:
        """单个栅格像素的经度宽度（度）。"""
        return (self.lon_max - self.lon_min) / self.width

    @property
    def res_y(self) -> float:
        """单个栅格像素的纬度高度（度）。"""
        return (self.lat_max - self.lat_min) / self.height

    # ---- 越界规则 ----

    def normalize_col(self, col: int) -> int:
        """列号按 n_cols 取模回绕（经度是周期坐标）。"""
        return col % self.n_cols

    def check_row(self, row: int) -> int:
        """行号越界（纬度有硬边界）则抛 OutOfRangeError。"""
        if not 0 <= row < self.n_rows:
            raise OutOfRangeError(
                f"row {row} out of range [0, {self.n_rows}); "
                "latitude is bounded at the poles, out-of-range rows are rejected"
            )
        return row

    def wrap_lon(self, lon: float) -> float:
        """把经度回绕进 [lon_min, lon_max)。"""
        span = self.lon_max - self.lon_min
        return (lon - self.lon_min) % span + self.lon_min

    # ---- 行列号 -> 像素窗口 / 地理范围 ----

    def tile_pixel_window(self, row: int, col: int):
        """瓦片在栅格中的像素窗口 (x0, y0, x1, y1)，右下按栅格边界裁剪。

        边缘瓦片的 x1 - x0 或 y1 - y0 会小于 tile_size（不满）。
        """
        row = self.check_row(row)
        col = self.normalize_col(col)
        x0 = col * self.tile_size
        y0 = row * self.tile_size
        x1 = min(x0 + self.tile_size, self.width)
        y1 = min(y0 + self.tile_size, self.height)
        return (x0, y0, x1, y1)

    def tile_geo_extent(self, row: int, col: int) -> GeoExtent:
        """瓦片实际覆盖的地理范围（边缘瓦片为裁剪后的范围）。"""
        x0, y0, x1, y1 = self.tile_pixel_window(row, col)
        return GeoExtent(
            lon0=self.lon_min + x0 * self.res_x,
            lat0=self.lat_max - y1 * self.res_y,
            lon1=self.lon_min + x1 * self.res_x,
            lat1=self.lat_max - y0 * self.res_y,
        )

    # ---- 地理坐标 -> 行列号（上面两个换算的逆运算） ----

    def tile_for_lonlat(self, lon: float, lat: float):
        """经纬度所在的瓦片 (row, col)。

        经度先回绕进 [lon_min, lon_max)；纬度越界抛 OutOfRangeError。
        """
        if not self.lat_min <= lat <= self.lat_max:
            raise OutOfRangeError(
                f"lat {lat} out of range [{self.lat_min}, {self.lat_max}]"
            )
        wlon = self.wrap_lon(lon)
        col = int((wlon - self.lon_min) / (self.res_x * self.tile_size))
        col = min(col, self.n_cols - 1)  # 防浮点把 lon_max 顶到界外
        row = int((self.lat_max - lat) / (self.res_y * self.tile_size))
        row = min(row, self.n_rows - 1)  # lat == lat_min 时落在最后一行
        return (row, col)

    def tiles_for_bbox(self, lon0: float, lat0: float, lon1: float, lat1: float):
        """地理范围覆盖到的所有瓦片 [(row, col), ...]，按行优先排序。

        经度需在 [lon_min, lon_max] 内；lon0 > lon1 表示跨经度 180 度
        （反经线），此时把区间拆成 [lon0, lon_max] 与 [lon_min, lon1]
        两段分别求列范围。
        """
        if not (self.lat_min <= lat0 <= self.lat_max and self.lat_min <= lat1 <= self.lat_max):
            raise OutOfRangeError("bbox latitude out of range")
        if not (self.lon_min <= lon0 <= self.lon_max and self.lon_min <= lon1 <= self.lon_max):
            raise OutOfRangeError("bbox longitude out of range")
        if lat0 > lat1:
            lat0, lat1 = lat1, lat0

        def col_of(lon):
            # lon 已在 [lon_min, lon_max]；lon == lon_max 归入最后一列
            c = int((lon - self.lon_min) / (self.res_x * self.tile_size))
            return min(c, self.n_cols - 1)

        if lon0 <= lon1:
            col_ranges = [(col_of(lon0), col_of(lon1))]
        else:
            # 跨反经线：拆成两段
            col_ranges = [(col_of(lon0), self.n_cols - 1), (0, col_of(lon1))]

        row_top = int((self.lat_max - lat1) / (self.res_y * self.tile_size))
        row_bottom = int((self.lat_max - lat0) / (self.res_y * self.tile_size))
        row_bottom = min(row_bottom, self.n_rows - 1)  # lat0 == lat_min 边界

        cols = set()
        for c0, c1 in col_ranges:
            for c in range(c0, c1 + 1):
                cols.add(c % self.n_cols)
        return [(r, c) for r in range(row_top, row_bottom + 1) for c in sorted(cols)]
