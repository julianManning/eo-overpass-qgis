"""
Swath-aware overpass prediction (QGIS geometry engine, WGS84 geodesics, SGP4).

Same method as the eo-overpass-predictor notebooks:
  1. coarse scan (30 s) of the sub-satellite track against the ROI anchor;
  2. 1 s refinement around each candidate: ground-track heading → geodesic cross-track
     scan lines (± half swath) → quads intersected with the ROI in an ROI-centred
     equal-area projection → entry/exit times and true ROI coverage;
  3. attribution: direction, nadir offset, view angle, elevation, sun elevation, TLE age.
"""

import math

import numpy as np
from qgis.core import QgsGeometry, QgsPointXY

from .catalogue import SATELLITES
from .compat import is_point_geometry
from .geo import LocalEqualArea, geod_direct, geod_inverse, haversine_km
from .orbit import EARTH_RADIUS_KM, elevation_deg
from .sun import solar_elevation_deg


def _anchor(geom: QgsGeometry) -> QgsPointXY:
    c = geom.centroid()
    if not is_point_geometry(geom) and not geom.contains(c):
        c = geom.pointOnSurface()
    return c.asPoint()


def project_geometry(geom: QgsGeometry, proj: LocalEqualArea) -> QgsGeometry:
    """Copy of geom with every vertex projected by proj (keeps rings/parts intact)."""
    g = QgsGeometry(geom)
    pts = [(v.x(), v.y()) for v in g.vertices()]
    if not pts:
        return g
    x, y = proj.forward(np.array([p[0] for p in pts]), np.array([p[1] for p in pts]))
    for i in range(len(pts)):
        g.moveVertex(float(x[i]), float(y[i]), i)
    return g


def _polygon(xs, ys) -> QgsGeometry:
    ring = [QgsPointXY(float(a), float(b)) for a, b in zip(xs, ys)]
    ring.append(ring[0])
    return QgsGeometry.fromPolygonXY([ring])


def predict_overpasses(roi_geom: QgsGeometry, orbits: dict, tles: dict, start_unix: float, days: float,
                       satellites=None, tz_name: str = "UTC", roi_name: str = "ROI",
                       daytime_descending_only: bool = True, min_sun_elevation_deg: float = 0.0,
                       min_roi_coverage_pct: float = 0.0, coarse_step_s: float = 30.0,
                       fine_step_s: float = 1.0, feedback=None) -> list:
    """
    Predict imaging overpasses of one ROI (EPSG:4326 polygon or point geometry).

    Returns a list of dicts (one per pass, time-sorted) whose "geometry" is the swath strip
    around the ROI crossing in EPSG:4326, and whose times are Unix seconds (UTC).
    """
    names = satellites or list(orbits)
    a = _anchor(roi_geom)
    lon0, lat0 = a.x(), a.y()
    proj = LocalEqualArea(lon0, lat0)
    roi_eq = project_geometry(roi_geom, proj)
    is_point = is_point_geometry(roi_geom)
    roi_area = roi_eq.area() if not is_point else 0.0
    verts = [(v.x(), v.y()) for v in roi_eq.vertices()]
    roi_radius_m = max((math.hypot(x, y) for x, y in verts), default=0.0)
    engine = QgsGeometry.createGeometryEngine(roi_eq.constGet())
    engine.prepareGeometry()

    total_s = float(days) * 86400.0
    rows = []
    for k, name in enumerate(names):
        if feedback is not None and feedback.isCanceled():
            break
        cat = SATELLITES[name]
        orb = orbits[name]
        half_km = cat["swath_km"] / 2.0
        thresh_km = half_km + roi_radius_m / 1000.0 + 150.0

        off = np.arange(0.0, total_s + coarse_step_s, coarse_step_s)
        clat, clon, _ = orb.subpoint(start_unix + off)
        d = haversine_km(lon0, lat0, clon, clat)
        idx = np.flatnonzero(d < thresh_km)
        runs = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1) if idx.size else []

        for run in runs:
            if feedback is not None and feedback.isCanceled():
                break
            f_off = np.arange(max(off[run[0]] - 2 * coarse_step_s, 0.0),
                              min(off[run[-1]] + 2 * coarse_step_s, total_s) + fine_step_s, fine_step_s)
            t = start_unix + f_off
            ex, ey, ez = orb.ecef(t)
            from .orbit import ecef_to_geodetic
            lat, lon, alt = ecef_to_geodetic(ex, ey, ez)
            ok = np.isfinite(lat)
            if ok.sum() < 3:
                continue
            az, _ = geod_inverse(lon[:-1], lat[:-1], lon[1:], lat[1:])
            az = np.append(az, az[-1])
            llon, llat = geod_direct(lon, lat, az - 90.0, half_km * 1000.0)
            rlon, rlat = geod_direct(lon, lat, az + 90.0, half_km * 1000.0)
            lx, ly = proj.forward(llon, llat)
            rx, ry = proj.forward(rlon, rlat)
            nx, ny = proj.forward(lon, lat)

            near = np.hypot(nx, ny) < (half_km * 1000.0 + roi_radius_m + 25000.0)
            hits = []
            for i in np.flatnonzero(near[:-1]):
                quad = _polygon([lx[i], lx[i + 1], rx[i + 1], rx[i]], [ly[i], ly[i + 1], ry[i + 1], ry[i]])
                if engine.intersects(quad.constGet()):
                    hits.append(i)
            if not hits:
                continue
            i_in, i_out = hits[0], hits[-1] + 1

            d2 = nx ** 2 + ny ** 2
            i_ca = int(np.argmin(d2))
            frac = 0.0
            if 0 < i_ca < len(d2) - 1:
                den = d2[i_ca - 1] - 2 * d2[i_ca] + d2[i_ca + 1]
                if den > 0:
                    frac = float(np.clip(0.5 * (d2[i_ca - 1] - d2[i_ca + 1]) / den, -0.5, 0.5))
            if is_point:
                i_in = i_out = i_ca

            strip = _polygon(np.concatenate([lx, rx[::-1]]), np.concatenate([ly, ry[::-1]])).makeValid()
            if is_point:
                coverage = 100.0
            else:
                coverage = 100.0 * strip.intersection(roi_eq).area() / roi_area if roi_area > 0 else 100.0
            if coverage < min_roi_coverage_pct:
                continue

            i_prev, i_next = max(i_ca - 1, 0), min(i_ca + 1, len(lat) - 1)
            direction = "Ascending" if lat[i_next] > lat[i_prev] else "Descending"
            az_roi, dist_roi = geod_inverse([lon[i_ca]], [lat[i_ca]], [lon0], [lat0])
            d_ca_km = float(dist_roi[0]) / 1000.0
            gamma = d_ca_km / EARTH_RADIUS_KM
            view = math.degrees(math.atan2(EARTH_RADIUS_KM * math.sin(gamma),
                                           EARTH_RADIUS_KM + alt[i_ca] - EARTH_RADIUS_KM * math.cos(gamma)))
            side = "nadir" if d_ca_km < 1.0 else ("E" if 0.0 < (az_roi[0] % 360.0) < 180.0 else "W")

            t_in = float(t[i_in])
            t_ca = float(t[i_ca] + frac * fine_step_s)
            t_out = float(t[min(i_out, len(t) - 1)])
            if is_point:                       # a point is imaged at one instant: the closest approach
                t_in = t_out = t_ca
            sun_el = float(solar_elevation_deg(t_ca, lat0, lon0))
            max_el = float(elevation_deg((ex[i_ca], ey[i_ca], ez[i_ca]), lat0, lon0))
            lo_lim, hi_lim = cat["lat_limits"]
            expected = bool(direction == "Descending" and sun_el >= min_sun_elevation_deg and lo_lim <= lat0 <= hi_lim)
            if daytime_descending_only and not expected:
                continue

            pad = int(round(20.0 / fine_step_s))
            j0, j1 = max(i_in - pad, 0), min(i_out + pad, len(lat) - 1)
            px = np.concatenate([llon[j0:j1 + 1], rlon[j0:j1 + 1][::-1]])
            py = np.concatenate([llat[j0:j1 + 1], rlat[j0:j1 + 1][::-1]])
            if px.max() - px.min() > 180:
                px = np.where(px < 0, px + 360.0, px)
            disp = _polygon(px, py).makeValid()

            epoch = tles[name]["epoch_unix"]
            rows.append({
                "roi_name": roi_name, "satellite": name, "mission": cat["mission"], "sensor": cat["sensor"],
                "norad_id": cat["norad_id"], "direction": direction, "imaging_expected": expected,
                "roi_coverage_pct": round(coverage, 1),
                "roi_entry_utc": t_in, "overpass_utc": t_ca, "roi_exit_utc": t_out,
                "duration_s": round(t_out - t_in, 1), "timezone": tz_name,
                "nadir_offset_km": round(d_ca_km, 1), "roi_side_of_track": side,
                "edge_margin_km": round(half_km - d_ca_km, 1), "view_angle_deg": round(view, 2),
                "max_elevation_deg": round(max_el, 1), "sun_elevation_deg": round(sun_el, 1),
                "sat_altitude_km": round(float(alt[i_ca]), 1), "tle_epoch_utc": epoch,
                "tle_age_days": round((t_ca - epoch) / 86400.0, 1), "note": cat["note"],
                "esa_plan_status": "", "esa_datatake_id": "", "esa_relative_orbit": None,
                "anchor_lon": lon0, "anchor_lat": lat0, "geometry": disp,
                "track_lon": lon[j0:j1 + 1:5].tolist(), "track_lat": lat[j0:j1 + 1:5].tolist(),
            })
        if feedback is not None:
            feedback.setProgress(100.0 * (k + 1) / len(names))
    rows.sort(key=lambda r: r["overpass_utc"])
    return rows
