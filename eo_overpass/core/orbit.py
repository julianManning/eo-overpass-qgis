"""
SGP4 orbit propagation with no dependency beyond numpy and the (vendored) sgp4 package.

TEME → Earth-fixed uses Greenwich Mean Sidereal Time (IAU-82), as in Vallado's reference
implementation; polar motion (~10 m) is ignored. Geodetic coordinates are on WGS84.
"""

import numpy as np
from sgp4.api import Satrec, WGS72

from .tle import tle_epoch_unix

WGS84_A = 6378.137
WGS84_F = 1 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)
EARTH_RADIUS_KM = 6371.0088


def _jd_split(t_unix):
    jd = np.asarray(t_unix, dtype=float) / 86400.0 + 2440587.5
    jdi = np.floor(jd - 0.5) + 0.5
    return jdi, jd - jdi


def gmst_rad(t_unix):
    """Greenwich Mean Sidereal Time (IAU-82) in radians."""
    jdi, fr = _jd_split(t_unix)
    tut1 = ((jdi - 2451545.0) + fr) / 36525.0
    temp = (-6.2e-6 * tut1 ** 3 + 0.093104 * tut1 ** 2
            + (876600.0 * 3600 + 8640184.812866) * tut1 + 67310.54841)
    return np.mod(np.radians(temp / 240.0), 2 * np.pi)


def ecef_to_geodetic(x, y, z):
    """WGS84 geodetic latitude/longitude (deg) and height (km) from ECEF (km)."""
    lon = np.degrees(np.arctan2(y, x))
    p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1 - WGS84_E2))
    for _ in range(5):
        n = WGS84_A / np.sqrt(1 - WGS84_E2 * np.sin(lat) ** 2)
        h = p / np.cos(lat) - n
        lat = np.arctan2(z, p * (1 - WGS84_E2 * n / (n + h)))
    n = WGS84_A / np.sqrt(1 - WGS84_E2 * np.sin(lat) ** 2)
    h = p / np.cos(lat) - n
    return np.degrees(lat), lon, h


class Orbit:
    """One satellite: propagate to arrays of Unix times → geodetic sub-point, height and ECEF position."""

    def __init__(self, name: str, line1: str, line2: str):
        self.name = name
        self.line1, self.line2 = line1, line2
        self.sat = Satrec.twoline2rv(line1, line2, WGS72)
        self.epoch_unix = tle_epoch_unix(line1)
        self.period_s = 86400.0 / float(line2[52:63])

    def ecef(self, t_unix):
        t = np.atleast_1d(np.asarray(t_unix, dtype=float))
        jdi, fr = _jd_split(t)
        e, r, _ = self.sat.sgp4_array(jdi, fr)
        r = np.asarray(r, dtype=float)
        r[np.asarray(e) != 0] = np.nan
        g = gmst_rad(t)
        c, s = np.cos(g), np.sin(g)
        x = c * r[:, 0] + s * r[:, 1]
        y = -s * r[:, 0] + c * r[:, 1]
        return x, y, r[:, 2]

    def subpoint(self, t_unix):
        """(lat °, lon °, height km) arrays."""
        return ecef_to_geodetic(*self.ecef(t_unix))


def build_orbits(tles: dict) -> dict:
    return {name: Orbit(name, t["line1"], t["line2"]) for name, t in tles.items()}


def elevation_deg(sat_ecef, lat, lon):
    """Elevation of satellite ECEF positions (km) seen from a ground point (lat, lon on WGS84, h=0)."""
    la, lo = np.radians(lat), np.radians(lon)
    n = WGS84_A / np.sqrt(1 - WGS84_E2 * np.sin(la) ** 2)
    gx, gy, gz = n * np.cos(la) * np.cos(lo), n * np.cos(la) * np.sin(lo), n * (1 - WGS84_E2) * np.sin(la)
    up = np.array([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])
    dx, dy, dz = sat_ecef[0] - gx, sat_ecef[1] - gy, sat_ecef[2] - gz
    rng = np.sqrt(dx * dx + dy * dy + dz * dz)
    return np.degrees(np.arcsin((dx * up[0] + dy * up[1] + dz * up[2]) / rng))
