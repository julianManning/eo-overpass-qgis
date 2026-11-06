"""
Product identifiers and nominal footprints for a predicted pass.

* Sentinel-2: relative orbit (R001–R143) from the descending-node longitude, and the MGRS tiles
  (109.8 × 109.8 km, anchored at the MGRS 100 km square's upper-left corner) that the swath covers
  over the ROI.
* Landsat 8/9: WRS-2 path/row from the descending-node longitude and the along-track position;
  nominal 185 × 180 km scene footprints.

Calibrated against delivered acquisitions (WRS-2 path/rows of seven Landsat scenes; Sentinel-2
relative orbits R030/R130 over Port of Brisbane and R038 from an ESA acquisition plan).
"""

import math

import numpy as np
from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsPointXY,
                       QgsProject, QgsRectangle)

from .compat import is_point_geometry
from .geo import geod_direct, geod_inverse

# --- calibration constants -----------------------------------------------------------------------------
WRS2_PATH1_NODE_LON = -64.59            # descending-node longitude of WRS-2 path 1 (°E)
WRS2_PATHS, WRS2_ROWS, WRS2_ROW_AT_NODE = 233, 248, 60
S2_REF_NODE_LON, S2_REF_ORBIT = -43.046, 38   # descending node of relative orbit R038
S2_ORBITS, S2_CYCLE_DAYS = 143, 10
S2_TILE_M = 109800.0

_COL_SETS = ("ABCDEFGH", "JKLMNPQR", "STUVWXYZ")
_ROW_LETTERS = "ABCDEFGHJKLMNPQRSTUV"
_BANDS = "CDEFGHJKLMNPQRSTUVWX"


# --- orbit geometry ------------------------------------------------------------------------------------
def descending_node(orbit, t_unix: float):
    """(time, longitude) of the descending equator crossing of the revolution containing t."""
    off = np.arange(-0.75 * orbit.period_s, 0.75 * orbit.period_s, 2.0)
    la, lo, _ = orbit.subpoint(t_unix + off)
    idx = np.flatnonzero((la[:-1] > 0) & (la[1:] <= 0))
    if idx.size == 0:
        return None, None
    i = idx[np.argmin(np.abs(off[idx]))]
    f = la[i] / (la[i] - la[i + 1])
    dlon = ((lo[i + 1] - lo[i] + 180) % 360) - 180
    return float(t_unix + off[i] + 2.0 * f), float(((lo[i] + f * dlon + 180) % 360) - 180)


def s2_relative_orbit(node_lon: float) -> int:
    step = 360.0 * S2_CYCLE_DAYS / S2_ORBITS          # successive orbits move this far west
    best = min(range(S2_ORBITS),
               key=lambda k: abs((((S2_REF_NODE_LON - node_lon - k * step) + 180) % 360) - 180))
    return (S2_REF_ORBIT - 1 + best) % S2_ORBITS + 1


def wrs2_path(node_lon: float) -> int:
    k = round((((WRS2_PATH1_NODE_LON - node_lon) % 360)) / (360.0 / WRS2_PATHS))
    return int(k % WRS2_PATHS) + 1


def wrs2_row_at(t_unix: float, t_node: float, period_s: float) -> float:
    u = (t_unix - t_node) / period_s * 360.0
    return WRS2_ROW_AT_NODE + u / (360.0 / WRS2_ROWS)


# --- MGRS ------------------------------------------------------------------------------------------------
def utm_zone(lon: float, lat: float) -> int:
    z = int((lon + 180) // 6) + 1
    if 56 <= lat < 64 and 3 <= lon < 12:
        return 32
    if 72 <= lat < 84:
        for lo_, hi_, zz in ((0, 9, 31), (9, 21, 33), (21, 33, 35), (33, 42, 37)):
            if lo_ <= lon < hi_:
                return zz
    return min(max(z, 1), 60)


def lat_band(lat: float) -> str:
    return _BANDS[min(max(int((lat + 80) // 8), 0), len(_BANDS) - 1)]


def square_id(zone: int, easting: float, northing_with_false: float) -> str:
    s = (zone - 1) % 6 + 1
    col = _COL_SETS[(s - 1) % 3][int(easting // 100000) - 1]
    r = int(northing_with_false // 100000)
    row = _ROW_LETTERS[(r + (5 if s % 2 == 0 else 0)) % 20]
    return col + row


def _utm_crs(zone: int, south: bool):
    return QgsCoordinateReferenceSystem(f"EPSG:{(32700 if south else 32600) + zone}")


def s2_tiles(geom4326: QgsGeometry) -> list:
    """[(tile_id, footprint EPSG:4326)] of Sentinel-2 tiles intersecting geom (EPSG:4326)."""
    if geom4326 is None or geom4326.isEmpty():
        return []
    wgs = QgsCoordinateReferenceSystem("EPSG:4326")
    bb = geom4326.boundingBox()
    zones = set()
    for lon in np.linspace(bb.xMinimum(), bb.xMaximum(), 9):
        for lat in np.linspace(bb.yMinimum(), bb.yMaximum(), 5):
            zones.add(utm_zone(float(lon), float(lat)))
    out = {}
    for south in {bb.yMinimum() < 0, bb.yMaximum() < 0}:
        for zone in sorted(zones):
            crs = _utm_crs(zone, south)
            fwd = QgsCoordinateTransform(wgs, crs, QgsProject.instance())
            inv = QgsCoordinateTransform(crs, wgs, QgsProject.instance())
            fwd.setBallparkTransformsAreAppropriate(True)
            inv.setBallparkTransformsAreAppropriate(True)
            g = QgsGeometry(geom4326)
            try:
                g.transform(fwd)
            except Exception:  # nosec B112
                continue
            gb = g.boundingBox()
            e0 = int(gb.xMinimum() // 100000) * 100000 - 100000
            n0 = int(gb.yMinimum() // 100000) * 100000
            for e in range(max(e0, 100000), int(gb.xMaximum()) + 1, 100000):
                if e > 800000:
                    continue
                for n in range(n0, int(gb.yMaximum()) + 100001, 100000):
                    top = n + 100000
                    tile = QgsGeometry.fromRect(QgsRectangle(e, top - S2_TILE_M, e + S2_TILE_M, top))
                    if not tile.intersects(g):
                        continue
                    part = tile.intersection(g)
                    tile_d = tile.densifyByCount(20)
                    tile_d.transform(inv)
                    c = part.centroid()
                    c.transform(inv)
                    lat_c = c.asPoint().y()
                    if (lat_c < 0) != south:
                        continue
                    tid = f"{zone:02d}{lat_band(lat_c)}{square_id(zone, e, n)}"
                    out.setdefault(tid, tile_d)
    return sorted(out.items())


# --- per-pass product attribution -------------------------------------------------------------------------
def _rect_along_track(orbit, t_center, half_along_km, half_cross_km):
    t = t_center + np.linspace(-half_along_km / 6.75, half_along_km / 6.75, 13)
    la, lo, _ = orbit.subpoint(t)
    az, _ = geod_inverse(lo[:-1], la[:-1], lo[1:], la[1:])
    az = np.append(az, az[-1])
    llo, lla = geod_direct(lo, la, az - 90.0, half_cross_km * 1000.0)
    rlo, rla = geod_direct(lo, la, az + 90.0, half_cross_km * 1000.0)
    xs = np.concatenate([llo, rlo[::-1]])
    ys = np.concatenate([lla, rla[::-1]])
    if xs.max() - xs.min() > 180:
        xs = np.where(xs < 0, xs + 360.0, xs)
    ring = [QgsPointXY(float(a), float(b)) for a, b in zip(xs, ys)]
    ring.append(ring[0])
    return QgsGeometry.fromPolygonXY([ring])


def attribute_products(p: dict, orbit, roi_geom: QgsGeometry) -> list:
    """
    Add 'relative_orbit' and 'products' to pass dict p; return product footprints:
    [{"product", "kind", "relative_orbit", "wrs_path", "wrs_row", "geometry"}].
    """
    t_node, node_lon = descending_node(orbit, p["overpass_utc"])
    feats = []
    if node_lon is None:
        p["relative_orbit"], p["products"] = None, ""
        return feats
    swath = p.get("swath_geometry") or p["geometry"]
    target = roi_geom if is_point_geometry(roi_geom) else roi_geom.intersection(swath)
    if target.isEmpty():
        target = roi_geom
    if p["mission"] == "Sentinel-2":
        rel = s2_relative_orbit(node_lon)
        p["relative_orbit"] = rel
        tiles = s2_tiles(target)
        p["products"] = " ".join(f"T{t}" for t, _ in tiles)
        for tid, g in tiles:
            feats.append({"product": f"T{tid}", "kind": "Sentinel-2 MGRS tile (data only inside the swath)",
                          "relative_orbit": rel, "wrs_path": None, "wrs_row": None, "geometry": g})
    else:
        path = wrs2_path(node_lon)
        p["relative_orbit"] = path
        r_in = wrs2_row_at(p["roi_entry_utc"], t_node, orbit.period_s)
        r_out = wrs2_row_at(p["roi_exit_utc"], t_node, orbit.period_s)
        rows = range(int(math.floor(min(r_in, r_out))) - 1, int(math.ceil(max(r_in, r_out))) + 2)
        names = []
        for row in rows:
            t_row = t_node + (row - WRS2_ROW_AT_NODE) * (360.0 / WRS2_ROWS) / 360.0 * orbit.period_s
            g = _rect_along_track(orbit, t_row, 90.0, 92.5)
            if not g.intersects(target):
                continue
            rr = (row - 1) % WRS2_ROWS + 1
            names.append(f"{path:03d}/{rr:03d}")
            feats.append({"product": f"{path:03d}/{rr:03d}", "kind": "Landsat WRS-2 scene (nominal 185 × 180 km)",
                          "relative_orbit": None, "wrs_path": path, "wrs_row": rr, "geometry": g})
        p["products"] = " ".join(names)
    return feats
