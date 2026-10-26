"""Geodesy helpers: WGS84 geodesics (Karney, via geographiclib), haversine screening, local equal-area projection."""

import math

import numpy as np
from geographiclib.geodesic import Geodesic

from .orbit import EARTH_RADIUS_KM

_G = Geodesic.WGS84
_AZI = Geodesic.AZIMUTH
_DIST = Geodesic.DISTANCE


def geod_inverse(lon1, lat1, lon2, lat2):
    """Forward azimuth (°) and distance (m) for paired arrays."""
    n = len(lon1)
    az = np.empty(n)
    dist = np.empty(n)
    for i in range(n):
        r = _G.Inverse(lat1[i], lon1[i], lat2[i], lon2[i], _AZI | _DIST)
        az[i], dist[i] = r["azi1"], r["s12"]
    return az, dist


def geod_direct(lon, lat, azi, dist_m):
    """Destination lon/lat arrays from start points, azimuths (°) and distance(s) (m)."""
    n = len(lon)
    dist_m = np.broadcast_to(np.asarray(dist_m, dtype=float), (n,))
    out_lon = np.empty(n)
    out_lat = np.empty(n)
    for i in range(n):
        r = _G.Direct(lat[i], lon[i], azi[i], dist_m[i])
        out_lon[i], out_lat[i] = ((r["lon2"] + 180.0) % 360.0) - 180.0, r["lat2"]
    return out_lon, out_lat


def haversine_km(lon1, lat1, lon2, lat2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


class LocalEqualArea:
    """Spherical Lambert azimuthal equal-area projection centred on the ROI (metres).

    Used only for ratios and intersections in a small region around the ROI, where the
    spherical approximation of an ellipsoidal LAEA changes area ratios by far less than 0.1 %.
    """

    def __init__(self, lon0: float, lat0: float):
        self.lon0, self.lat0 = lon0, lat0
        self._s0, self._c0 = math.sin(math.radians(lat0)), math.cos(math.radians(lat0))
        self.R = EARTH_RADIUS_KM * 1000.0

    def forward(self, lon, lat):
        lam = np.radians(np.asarray(lon, dtype=float) - self.lon0)
        lam = (lam + np.pi) % (2 * np.pi) - np.pi
        phi = np.radians(np.asarray(lat, dtype=float))
        cosc = self._s0 * np.sin(phi) + self._c0 * np.cos(phi) * np.cos(lam)
        k = np.sqrt(2.0 / np.clip(1.0 + cosc, 1e-12, None))
        x = self.R * k * np.cos(phi) * np.sin(lam)
        y = self.R * k * (self._c0 * np.sin(phi) - self._s0 * np.cos(phi) * np.cos(lam))
        return x, y
