"""瓦片切片与索引：规则经纬栅格的行列号索引、面积平均重采样、LRU 缓存。"""

from .grid import Grid, GeoExtent, OutOfRangeError
from .resample import (
    area_average,
    area_average_reference,
    nearest_neighbor,
    max_abs_diff,
)
from .cache import TileCache
from .service import TileService, Tile


def make_raster(width: int, height: int, value_fn):
    """按 value_fn(x, y) 生成浮点栅格 list[list[float]]。"""
    return [[float(value_fn(x, y)) for x in range(width)] for y in range(height)]


__all__ = [
    "Grid", "GeoExtent", "OutOfRangeError",
    "area_average", "area_average_reference", "nearest_neighbor", "max_abs_diff",
    "TileCache", "TileService", "Tile", "make_raster",
]
