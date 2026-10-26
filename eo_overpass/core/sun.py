"""Analytic solar position (~0.01°), no ephemeris files."""

import numpy as np


def _ra_dec_gmst(t_unix):
    n = np.asarray(t_unix, dtype=float) / 86400.0 + 2440587.5 - 2451545.0
    L = np.radians((280.460 + 0.9856474 * n) % 360)
    g = np.radians((357.528 + 0.9856003 * n) % 360)
    lam = L + np.radians(1.915) * np.sin(g) + np.radians(0.020) * np.sin(2 * g)
    eps = np.radians(23.439 - 0.0000004 * n)
    ra = np.degrees(np.arctan2(np.cos(eps) * np.sin(lam), np.cos(lam)))
    dec = np.degrees(np.arcsin(np.sin(eps) * np.sin(lam)))
    gmst = (280.46061837 + 360.98564736629 * n) % 360
    return ra, dec, gmst


def solar_elevation_deg(t_unix, lat, lon):
    """Solar elevation (°) at (lat, lon) — scalars or arrays broadcast against t_unix."""
    ra, dec, gmst = _ra_dec_gmst(t_unix)
    h = np.radians(gmst + np.asarray(lon) - ra)
    la, de = np.radians(lat), np.radians(dec)
    return np.degrees(np.arcsin(np.sin(la) * np.sin(de) + np.cos(la) * np.cos(de) * np.cos(h)))


def subsolar_point(t_unix):
    ra, dec, gmst = _ra_dec_gmst(t_unix)
    return float(dec), float(((ra - gmst + 180) % 360) - 180)
