"""
Engine tests — run with QGIS's Python:

    python tests/test_engine.py            (or: python -m pytest tests)

Validation cases are delivered acquisitions (USGS Collection 2 scene centres, Sentinel-2 L2A granule
times from the Earth Search STAC API). The fixture TLEs were current at the time of those scenes.
"""

import calendar
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import _bootstrap  # noqa: E402,F401

from qgis.core import QgsGeometry  # noqa: E402

from eo_overpass.core.orbit import build_orbits  # noqa: E402
from eo_overpass.core.predict import predict_overpasses  # noqa: E402
from eo_overpass.core.timezones import to_local, zone_at  # noqa: E402
from eo_overpass.core.tle import normalise_tle_line, parse_tle_text  # noqa: E402

HERE = os.path.dirname(__file__)


def _fixture_tles():
    from eo_overpass.core.tle import tle_epoch_unix
    recs = parse_tle_text(open(os.path.join(HERE, "data", "tles_2026-10.txt")).read())
    from eo_overpass.core.catalogue import SATELLITES
    by_id = {v["norad_id"]: k for k, v in SATELLITES.items()}
    return {by_id[n]: {**r, "norad_id": n, "epoch_unix": tle_epoch_unix(r["line1"]), "source": "fixture"}
            for n, r in recs.items()}


def _utc(s):
    from datetime import datetime
    d = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
    frac = float("0." + s[20:-1]) if "." in s else 0.0
    return calendar.timegm(d.timetuple()) + frac


def test_tle_repair():
    collapsed = "1 39084U 13008A 26296.23131832 .00000185 00000-0 51189-4 0 9996"
    fixed = normalise_tle_line(collapsed)
    assert len(fixed) == 69 and fixed.startswith("1 39084U 13008A   26296.23131832")


def test_timezones():
    assert zone_at(144.96, -37.81) == "Australia/Melbourne"
    assert zone_at(151.21, -33.87) == "Australia/Sydney"
    assert zone_at(-30.0, 0.0).startswith("Etc/GMT")
    # DST: AEST in July, AEDT in October
    assert to_local(_utc("2026-07-01T00:00:00Z"), "Australia/Melbourne").utcoffset().total_seconds() == 36000
    assert to_local(_utc("2026-10-26T00:00:00Z"), "Australia/Melbourne").utcoffset().total_seconds() == 39600


VALIDATION = [  # satellite, (lon, lat) scene/granule centroid, observed UTC
    ("LANDSAT 9", (144.32334, -37.47084), "2026-10-13T00:09:46.602Z"),
    ("LANDSAT 9", (159.41421, -14.45721), "2026-10-18T23:26:17.851Z"),
    ("LANDSAT 9", (163.75985, 5.78207), "2026-10-18T23:20:42.932Z"),
    ("SENTINEL-2B", (145.41108, -37.5509), "2026-10-23T00:17:22.164Z"),
    ("SENTINEL-2B", (144.78643, -37.85308), "2026-10-23T00:17:29.601Z"),   # swath-edge sliver
]


def test_against_delivered_acquisitions():
    tles = _fixture_tles()
    orbs = build_orbits(tles)
    for sat, (lon, lat), obs in VALIDATION:
        t = _utc(obs)
        roi = QgsGeometry.fromWkt(f"POINT({lon} {lat})")
        rows = predict_overpasses(roi, orbs, tles, t - 3 * 3600, 0.25, [sat], "UTC",
                                  daytime_descending_only=False)
        assert rows, f"{sat} {obs}: no pass predicted"
        dt = min(abs(r["overpass_utc"] - t) for r in rows)
        assert dt < 1.5, f"{sat} {obs}: off by {dt:.2f} s"


def test_notebook_parity_port_phillip_bay():
    """Same TLEs and window as the notebook README example → same passes and coverage."""
    tles = _fixture_tles()
    orbs = build_orbits(tles)
    roi = QgsGeometry.fromWkt("POLYGON((144.35 -38.35,145.15 -38.35,145.15 -37.80,144.35 -37.80,144.35 -38.35))")
    rows = predict_overpasses(roi, orbs, tles, _utc("2026-10-24T00:00:00Z"), 16, tz_name="Australia/Melbourne")
    assert len(rows) == 12
    first = rows[0]
    assert first["satellite"] == "SENTINEL-2B" and abs(first["overpass_utc"] - _utc("2026-10-26T00:27:30Z")) < 1.5
    assert first["roi_coverage_pct"] == 100.0
    s2c = next(r for r in rows if r["satellite"] == "SENTINEL-2C")
    assert abs(s2c["roi_coverage_pct"] - 55.3) < 0.5


def test_products_port_phillip_bay():
    """Relative orbit / WRS-2 path-row and MGRS tile IDs attached to each pass."""
    from eo_overpass.core.products import attribute_products
    tles = _fixture_tles()
    orbs = build_orbits(tles)
    roi = QgsGeometry.fromWkt("POLYGON((144.35 -38.35,145.15 -38.35,145.15 -37.80,144.35 -37.80,144.35 -38.35))")
    rows = predict_overpasses(roi, orbs, tles, _utc("2026-10-24T00:00:00Z"), 16, tz_name="Australia/Melbourne")
    seen = {}
    for r in rows:
        attribute_products(r, orbs[r["satellite"]], roi)
        seen.setdefault(r["satellite"][:2], set()).add(r["relative_orbit"])
        if r["satellite"].startswith("SENTINEL"):
            assert set(r["products"].split()) <= {"T55HBT", "T55HBU", "T55HCT", "T55HCU"}, r["products"]
        else:
            assert set(r["products"].split()) <= {"092/086", "092/087", "093/086", "093/087"}, r["products"]
    assert seen["SE"] == {116, 73}, seen
    assert seen["LA"] == {92, 93}, seen


def test_mgrs_tile_names():
    from eo_overpass.core.products import s2_tiles
    for (lon, lat), tile in [((144.79, -37.85), "55HCU"), ((153.15, -27.38), "56JNQ")]:
        ids = [t for t, _ in s2_tiles(QgsGeometry.fromWkt(f"POINT({lon} {lat})"))]
        assert tile in ids, (tile, ids)


def test_last_roi_roundtrip():
    from eo_overpass.core import last_roi
    last_roi.save("Site A", QgsGeometry.fromWkt("POLYGON((144 -38,145 -38,145 -37,144 -37,144 -38))"))
    name, g = last_roi.load()
    assert name == "Site A" and abs(g.area() - 1.0) < 1e-6


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL  {name}: {e}")
    sys.exit(1 if fails else 0)
