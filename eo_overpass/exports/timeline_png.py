"""Printable pass-timeline chart (QPainter, no matplotlib)."""

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from qgis.PyQt.QtCore import QPointF
from qgis.PyQt.QtGui import QBrush, QColor, QPen

from ..core.catalogue import SATELLITES
from ..core.timezones import to_local
from . import _paint as P


def export_timeline_png(path, passes: list, roi_name: str, tz_name: str, start_unix: float, end_unix: float,
                        tles: dict = None, width: int = 3300, dpi: int = 300, target=None):
    """A4-landscape-proportioned PNG: one row per satellite, local dates on x, local times labelled."""
    sats = [s for s in SATELLITES if any(p["satellite"] == s for p in passes)] or list(SATELLITES)
    u = width / 1100.0
    left, right = 150 * u, 40 * u
    top = 175 * u
    row_h = 78 * u
    plot_h = row_h * len(sats)
    caution_px = 13 * u
    height = int(top + plot_h + 70 * u + 170 * u + 40 * u)
    cv = P.Canvas(width, height, target, dpi)
    p = cv.p

    tz = ZoneInfo(tz_name)
    d0 = datetime.fromtimestamp(start_unix, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    d1 = datetime.fromtimestamp(end_unix, tz).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    span = (d1 - d0).total_seconds()
    pw = width - left - right

    def X(t_unix):
        return left + pw * (t_unix - d0.timestamp()) / span

    P.text(p, left, 24 * u, pw, 34 * u, f"Predicted overpasses — {roi_name}", P.font(26 * u, P.BOLD))
    P.text(p, left, 60 * u, pw, 22 * u,
           f"Local time ({tz_name}) · {d0:%d %b %Y} – {(d1 - timedelta(days=1)):%d %b %Y}", P.font(14 * u), P.MUTED)
    # legend: what filled / hollow circles and the numbers mean
    grey = QColor("#5B6B74")
    ly, f_leg = 100 * u, P.font(12.5 * u)
    p.setPen(QPen(grey, 2.2 * u))
    p.setBrush(QBrush(grey))
    p.drawEllipse(QPointF(left + 10 * u, ly + 11 * u), 9 * u, 9 * u)
    P.text(p, left + 26 * u, ly, 330 * u, 22 * u, "Filled = imaging expected (planned, or Landsat)", f_leg, P.INK)
    x2 = left + 380 * u
    p.setBrush(QBrush(P.PAPER))
    p.drawEllipse(QPointF(x2 + 10 * u, ly + 11 * u), 9 * u, 9 * u)
    P.text(p, x2 + 26 * u, ly, pw - 410 * u, 22 * u,
           "Hollow = not expected (night / ascending) or NOT in ESA's Sentinel-2 acquisition plan", f_leg, P.INK)
    P.text(p, left, ly + 26 * u, pw, 22 * u,
           "Number in a circle = % of the ROI covered (bigger circle = more) · label above = local time of the pass",
           f_leg, P.MUTED)

    # day grid
    p.setPen(QPen(P.LINE, max(1.0, u)))
    day = d0
    f_day = P.font(12.5 * u)
    step = 1 if span / 86400 <= 21 else 2 if span / 86400 <= 42 else 7
    k = 0
    while day <= d1:
        x = X(day.timestamp())
        p.setPen(QPen(P.LINE, max(1.0, u)))
        p.drawLine(QPointF(x, top - 6 * u), QPointF(x, top + plot_h))
        if day < d1 and k % step == 0:
            P.text(p, x + 3 * u, top + plot_h + 6 * u, 60 * u, 40 * u, f"{day:%a}\n{day:%d %b}", f_day, P.MUTED)
        day += timedelta(days=1)
        k += 1

    f_lab = P.font(15 * u, P.DEMIBOLD)
    f_t = P.font(11.5 * u)
    for r, sat in enumerate(sats):
        cy = top + r * row_h + row_h / 2
        col = QColor(SATELLITES[sat]["colour"])
        P.text(p, 10 * u, cy - 12 * u, left - 20 * u, 24 * u, sat.title(), f_lab, P.INK, P.ALIGN_RIGHT | P.ALIGN_VCENTER)
        p.setPen(QPen(P.LINE, max(1.0, 0.6 * u)))
        p.drawLine(QPointF(left, cy), QPointF(width - right, cy))
        for ps in (q for q in passes if q["satellite"] == sat):
            x = X(ps["overpass_utc"])
            cov = float(ps["roi_coverage_pct"])
            rad = (13 + 0.07 * cov) * u
            filled = bool(ps["imaging_expected"]) and not str(ps.get("esa_plan_status") or "").startswith("not planned")
            p.setPen(QPen(col, 2.2 * u))
            p.setBrush(QBrush(col) if filled else QBrush(P.PAPER))
            p.drawEllipse(QPointF(x, cy), rad, rad)
            P.text(p, x - rad, cy - rad, 2 * rad, 2 * rad, f"{cov:.0f}", P.font(11 * u, P.BOLD),
                   P.PAPER if filled else col, P.ALIGN_CENTER)
            loc = to_local(ps["overpass_utc"], tz_name)
            P.text(p, x - 40 * u, cy - rad - 19 * u, 80 * u, 18 * u, f"{loc:%H:%M}", f_t, P.MUTED, P.ALIGN_HCENTER)

    y = top + plot_h + 60 * u
    if tles:
        ages = " · ".join(f"{n.title()} {(time.time() - t['epoch_unix']) / 86400:.1f} d" for n, t in tles.items())
        P.text(p, left, y, pw, 20 * u, f"TLE age at export: {ages}", P.font(11.5 * u), P.MUTED)
    y += 26 * u
    P.caution_box(p, left, y, pw, "Before you go to the field", P.field_cautions(passes, tz_name), caution_px)
    P.badge(p, left, height - 36 * u, 32 * u)
    P.text(p, left + 40 * u, height - 30 * u, pw - 40 * u, 20 * u, "Page 2 · EO Overpass Predictor (QGIS)",
           P.font(10.5 * u), P.MUTED, P.ALIGN_LEFT)
    return cv.finish(path)
