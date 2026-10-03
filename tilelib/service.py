"""瓦片服务：请求到来时按行列号取窗口、面积平均缩放，并走缓存。"""

from __future__ import annotations

from dataclasses import dataclass, asdict

from .grid import Grid
from .resample import area_average
from .cache import TileCache


@dataclass(frozen=True)
class Tile:
    row: int
    col: int
    data: list           # 面积平均结果（valid_h x valid_w）
    valid_w: int         # 边缘瓦片不满时小于 tile_size
    valid_h: int
    coverage: float      # 有效面积占满幅瓦片的比例
    geo: dict            # 实际覆盖的地理范围

    def to_dict(self) -> dict:
        return asdict(self)


class TileService:
    def __init__(self, raster, grid: Grid, cache: "TileCache | None" = None,
                 out_size: "int | None" = None):
        self.raster = raster
        self.grid = grid
        self.cache = cache
        self.out_size = out_size or grid.tile_size

    def _render(self, row: int, col: int) -> Tile:
        x0, y0, x1, y1 = self.grid.tile_pixel_window(row, col)
        scale = self.out_size / self.grid.tile_size
        valid_w = max(1, round((x1 - x0) * scale))
        valid_h = max(1, round((y1 - y0) * scale))
        data = area_average(self.raster, (x0, y0, x1, y1), valid_w, valid_h)
        full = self.grid.tile_size * self.grid.tile_size
        extent = self.grid.tile_geo_extent(row, col)
        return Tile(
            row=row,
            col=self.grid.normalize_col(col),
            data=data,
            valid_w=valid_w,
            valid_h=valid_h,
            coverage=((x1 - x0) * (y1 - y0)) / full,
            geo={"lon0": extent.lon0, "lat0": extent.lat0,
                 "lon1": extent.lon1, "lat1": extent.lat1},
        )

    def get_tile(self, row: int, col: int) -> Tile:
        row = self.grid.check_row(row)
        col = self.grid.normalize_col(col)
        key = (row, col)
        if self.cache is not None:
            tile = self.cache.get(key)
            if tile is not None:
                return tile
        tile = self._render(row, col)
        if self.cache is not None:
            self.cache.put(key, tile)
        return tile
