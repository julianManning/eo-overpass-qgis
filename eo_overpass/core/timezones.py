"""Offline IANA time-zone lookup from a bundled 0.1° grid (timezone-boundary-builder, ODbL)."""

import json
import zlib
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

_DATA = Path(__file__).resolve().parents[1] / "data"
_GRID = None
_META = None


def _load():
    global _GRID, _META
    if _GRID is None:
        _META = json.loads((_DATA / "tz_grid_0p1.json").read_text())
        raw = zlib.decompress((_DATA / "tz_grid_0p1.bin").read_bytes())
        _GRID = np.frombuffer(raw, dtype=np.uint16).reshape(_META["shape"])
    return _GRID, _META


def zone_at(lon: float, lat: float, search_cells: int = 3) -> str:
    """IANA zone at a point. Offshore points take the nearest land zone within ~0.3°, else nautical Etc/GMT±N."""
    grid, meta = _load()
    res = meta["res"]
    i = int(np.clip((90.0 - lat) / res, 0, grid.shape[0] - 1))
    j = int(((lon + 180.0) / res) % grid.shape[1])
    k = int(grid[i, j])
    if not k:
        for r in range(1, search_cells + 1):
            win = grid[max(i - r, 0):i + r + 1, np.arange(j - r, j + r + 1) % grid.shape[1]]
            vals = win[win > 0]
            if vals.size:
                k = int(np.bincount(vals).argmax())
                break
    if k:
        return meta["names"][k]
    off = int(round(lon / 15.0))
    return "Etc/GMT" + (f"{-off:+d}" if off else "")


def zones_in_bbox(xmin, ymin, xmax, ymax) -> set:
    return {zone_at(x, y) for x, y in [(xmin, ymin), (xmin, ymax), (xmax, ymin), (xmax, ymax)]}


def utc_offset_str(dt: datetime) -> str:
    off = dt.utcoffset().total_seconds()
    sign = "+" if off >= 0 else "-"
    off = abs(off)
    return f"{sign}{int(off // 3600):02d}:{int(off % 3600 // 60):02d}"


def to_local(t_unix: float, tz_name: str) -> datetime:
    return datetime.fromtimestamp(t_unix, tz=timezone.utc).astimezone(ZoneInfo(tz_name))
