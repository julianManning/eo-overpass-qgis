"""Orbit-globe illustration of a featured pass (orthographic view, real swaths and orbits)."""

import json
import math
import time
from pathlib import Path

import numpy as np
from qgis.PyQt.QtCore import QPointF, QRectF
from qgis.PyQt.QtGui import QBrush, QColor, QImage, QPen, QPolygonF

from ..core.catalogue import SATELLITES
from ..core.geo import geod_direct, geod_inverse
from ..core.sun import solar_elevation_deg, subsolar_point
from ..core.timezones import to_local
from . import _paint as P

_DATA = Path(__file__).resolve().parents[1] / "data"
EW, EH = 3600, 1800
OMEGA = 360.0 / 86164.0905
MISSION_COL = {"Landsat": QColor(92, 162, 255), "Sentinel-2": QColor(255, 152, 52)}
SAT_RGB = {"LANDSAT 8": (130, 190, 255), "LANDSAT 9": (80, 150, 255), "SENTINEL-2A": (255, 200, 115),
           "SENTINEL-2B": (255, 152, 52), "SENTINEL-2C": (255, 112, 40)}


def _qimage_to_array(img: QImage) -> np.ndarray:
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes() if hasattr(img, "sizeInBytes") else img.byteCount())
    a = np.frombuffer(ptr, np.uint8).reshape(img.height(), img.bytesPerLine())
    return a[:, :img.width()].copy()


def _eq(lon, lat):
    return QPointF((lon + 180.0) / 360.0 * EW, (90.0 - lat) / 180.0 * EH)


def _land_mask():
    img = QImage(EW, EH, P.FMT_GRAY8)
    img.fill(0)
    p = P.begin(img)
    p.setPen(P.NO_PEN)
    p.setBrush(QBrush(QColor(255, 255, 255)))
    lj = json.loads((_DATA / "world_land_50m.json").read_text())
    for r in lj["rings"]:
        pts = [(r[i] * lj["scale"], r[i + 1] * lj["scale"]) for i in range(0, len(r), 2)]
        for sh in (-360, 0, 360):
            p.drawPolygon(QPolygonF([_eq(x + sh, y) for x, y in pts]))
    p.end()
    return _qimage_to_array(img) > 127


def _swath_mask(orbit, swath_km, t_end, hours):
    t = t_end + np.arange(-hours * 3600, 1, 20.0)
    lat, lon, _ = orbit.subpoint(t)
    az, _ = geod_inverse(lon[:-1], lat[:-1], lon[1:], lat[1:])
    az = np.append(az, az[-1])
    llon, llat = geod_direct(lon, lat, az - 90.0, swath_km * 500.0)
    rlon, rlat = geod_direct(lon, lat, az + 90.0, swath_km * 500.0)
    sun = solar_elevation_deg(t, lat, lon)
    desc = np.append(np.diff(lat) < 0, False)
    img = QImage(EW, EH, P.FMT_GRAY8)
    img.fill(0)
    p = P.begin(img)
    p.setPen(P.NO_PEN)
    p.setBrush(QBrush(QColor(255, 255, 255)))
    for i in range(len(t) - 1):
        if not (desc[i] and sun[i] > -12 and -66 < lat[i] < 74):
            continue
        q = [(llon[i], llat[i]), (llon[i + 1], llat[i + 1]), (rlon[i + 1], rlat[i + 1]), (rlon[i], rlat[i])]
        xs = [a for a, _ in q]
        if max(xs) - min(xs) > 180:
            continue
        p.drawPolygon(QPolygonF([_eq(a, b) for a, b in q]))
    p.end()
    return _qimage_to_array(img).astype(np.float32) / 255.0


def _unit(la, lo):
    la, lo = np.radians(la), np.radians(lo)
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], -1)


def export_globe_png(path, passes: list, orbits: dict, roi_geom, roi_name: str, tz_name: str,
                     featured: dict = None, size: int = 2400, hours: float = 5.0):
    """Square PNG: orthographic globe at the featured pass, swaths painted over the previous hours."""
    if featured is None:
        exp = [p for p in passes if p["imaging_expected"]] or passes
        if not exp:
            raise ValueError("No passes to feature.")
        featured = max(exp[:6], key=lambda p: p["roi_coverage_pct"])
    T = featured["overpass_utc"]
    a = roi_geom.centroid().asPoint()
    rlon, rlat = a.x(), a.y()
    lat0, lon0 = max(-60.0, min(60.0, rlat + 18.0)), rlon - 17.0

    land = _land_mask()
    masks = {n: _swath_mask(orbits[n], SATELLITES[n]["swath_km"], T, hours) for n in orbits}

    caption_h = int(size * 0.17)
    W, H = size, size + caption_h
    R = 0.40 * size
    cx, cy = size / 2, size / 2
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    X = (xx - cx) / R
    Y = -(yy - cy) / R
    rho = np.hypot(X, Y)
    inside = rho < 1.0
    c = np.arcsin(np.clip(rho, 0, 1))
    la0, lo0 = math.radians(lat0), math.radians(lon0)
    with np.errstate(invalid="ignore", divide="ignore"):
        lat = np.arcsin(np.clip(np.cos(c) * math.sin(la0)
                                + np.where(rho > 0, Y * np.sin(c) * math.cos(la0) / rho, 0), -1, 1))
        lon = lo0 + np.arctan2(X * np.sin(c), rho * np.cos(c) * math.cos(la0) - Y * np.sin(c) * math.sin(la0))
    latd, lond = np.degrees(lat), (np.degrees(lon) + 180) % 360 - 180
    pi = np.clip(((90 - latd) / 180 * EH).astype(int), 0, EH - 1)
    pj = np.clip(((lond + 180) / 360 * EW).astype(int), 0, EW - 1)
    sdec, slon = subsolar_point(T)
    cosi = (_unit(latd, lond) * _unit(np.array(sdec), np.array(slon))).sum(-1)
    day = np.clip((cosi + 0.08) / 0.25, 0, 1)
    limb = np.sqrt(np.clip(1 - rho ** 2, 0, 1))

    rgb = np.empty((size, size, 3), np.float32)
    rgb[:] = (6, 9, 16)
    rng = np.random.default_rng(7)
    for _ in range(int(size * size / 2500)):
        rgb[rng.integers(0, size), rng.integers(0, size)] = rng.uniform(60, 200)
    base = np.where(land[pi, pj][..., None], np.array([92, 100, 96], np.float32), np.array([34, 48, 66], np.float32))
    globe = base * ((0.14 + day) * (0.55 + 0.45 * limb))[..., None]
    for name in ["LANDSAT 8", "LANDSAT 9", "SENTINEL-2A", "SENTINEL-2B", "SENTINEL-2C"]:
        if name not in masks:
            continue
        al = (0.72 * masks[name][pi, pj] * day)[..., None]
        col = np.array(SAT_RGB[name], np.float32) * (0.55 + 0.45 * limb)[..., None]
        globe = globe * (1 - al) + col * al
    rgb = np.where(inside[..., None], globe, rgb)
    glow = np.clip(1 - np.abs(rho - 1.0) / 0.045, 0, 1) ** 2 * (rho > 0.97)
    rgb = np.clip(rgb + glow[..., None] * np.array([70, 130, 220], np.float32) * 0.9, 0, 255).astype(np.uint8)

    canvas = P.new_image(W, H, QColor(6, 9, 16))
    p = P.begin(canvas)
    buf = np.ascontiguousarray(rgb)
    qi = QImage(buf.data, size, size, 3 * size, P.enum_rgb888())
    p.drawImage(0, 0, qi)

    e = np.array([-math.sin(lo0), math.cos(lo0), 0.0])
    nn = np.array([-math.sin(la0) * math.cos(lo0), -math.sin(la0) * math.sin(lo0), math.cos(la0)])
    uu = np.array([math.cos(la0) * math.cos(lo0), math.cos(la0) * math.sin(lo0), math.sin(la0)])

    def view(la, lo, r):
        v = _unit(la, lo) * np.asarray(r)[..., None]
        return v @ e, v @ nn, v @ uu

    lw = max(2.0, size / 300)
    for mission, lead in (("Landsat", "LANDSAT 9"), ("Sentinel-2", "SENTINEL-2B")):
        if lead not in orbits:
            continue
        orb = orbits[lead]
        off = np.linspace(-orb.period_s / 2, orb.period_s / 2, 721)
        la, lo, alt = orb.subpoint(T + off)
        lo = lo + OMEGA * off
        x, y, z = view(la, lo, (6371 + alt) / 6371 * 1.04)
        hid = (z < 0) & (np.hypot(x, y) < 1)
        col = QColor(MISSION_COL[mission])
        for i in range(len(x) - 1):
            col.setAlpha(70 if hid[i] else 235)
            p.setPen(QPen(col, lw))
            p.drawLine(QPointF(cx + R * x[i], cy - R * y[i]), QPointF(cx + R * x[i + 1], cy - R * y[i + 1]))
    f_lab = P.font(size / 32, P.BOLD)
    for name, orb in orbits.items():
        la, lo, alt = orb.subpoint(np.array([T]))
        x, y, z = view(la, lo, (6371 + alt) / 6371 * 1.04)
        if z[0] < 0 and math.hypot(x[0], y[0]) < 1:
            continue
        px, py = cx + R * x[0], cy - R * y[0]
        rr = max(5.0, size / 110)
        p.setPen(QPen(QColor(10, 14, 22), lw))
        p.setBrush(QBrush(QColor(*SAT_RGB[name])))
        p.drawEllipse(QPointF(px, py), rr, rr)
        lbl = name.title()
        if name.startswith("LANDSAT"):
            P.text(p, px - size * 0.32, py - size / 28, size * 0.3, size / 22, lbl, f_lab, QColor(*SAT_RGB[name]), P.ALIGN_RIGHT)
        else:
            P.text(p, px + size / 45, py - size / 64, size * 0.3, size / 22, lbl, f_lab, QColor(*SAT_RGB[name]))
    x, y, z = view(np.array(rlat), np.array(rlon), np.array(1.0))
    k = max(6.0, size / 120)
    p.setPen(QPen(QColor(255, 70, 85), lw))
    p.setBrush(QBrush())
    rx, ry = cx + R * float(x), cy - R * float(y)
    p.drawRect(QRectF(rx - k, ry - k, 2 * k, 2 * k))
    P.text(p, rx - k - size * 0.12, ry + k, size * 0.12, size / 22, "ROI", f_lab, QColor(255, 90, 100), P.ALIGN_RIGHT)

    loc = to_local(T, tz_name)
    y0 = size + size * 0.015
    P.text(p, size * 0.05, y0, size * 0.9, size / 18,
           f"{featured['satellite'].title()} over {roi_name}", P.font(size / 26, P.BOLD), QColor(235, 240, 245))
    P.text(p, size * 0.05, y0 + size / 17, size * 0.9, size / 22,
           f"{loc:%a %d %b %Y  %H:%M:%S} {loc.tzname()}  ·  {time.strftime('%H:%M:%S', time.gmtime(T))} UTC  ·  "
           f"{featured['roi_coverage_pct']:.0f}% of ROI", P.font(size / 34), QColor(255, 205, 150))
    P.text(p, size * 0.05, y0 + size / 9, size * 0.9, size / 26,
           f"Previous {hours:g} h of daylit descending swaths · Landsat blue · Sentinel-2 orange · "
           f"rings = orbital planes", P.font(size / 52), QColor(170, 185, 200))
    p.end()
    canvas.save(str(path), "PNG")
    return path
