"""Printable A4 field sheet: footprint map of upcoming passes + pass table + cautions."""

import math
import time

from qgis.core import (QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsFeature, QgsFillSymbol, QgsGeometry, QgsMapRendererSequentialJob, QgsMapSettings,
                       QgsPointXY, QgsProject, QgsRasterLayer, QgsRectangle, QgsRendererCategory, QgsVectorLayer)
from qgis.PyQt.QtCore import QPointF, QRectF, QSize
from qgis.PyQt.QtGui import QBrush, QColor, QPen

from ..core.catalogue import SATELLITES
from ..core.compat import is_point_geometry
from ..core.timezones import to_local
from . import _paint as P

from ..core.basemaps import SHEET_BASEMAPS, attribution, uri as basemap_uri

BASEMAPS = {n: basemap_uri(n) for n in SHEET_BASEMAPS}      # offline land/sea → None
WEB = QgsCoordinateReferenceSystem("EPSG:3857")
WGS = QgsCoordinateReferenceSystem("EPSG:4326")


def _memory_layers(passes, roi_geom):
    fp = QgsVectorLayer("Polygon?crs=EPSG:4326&field=num:integer&field=satellite:string", "footprints", "memory")
    prov = fp.dataProvider()
    feats = []
    for i, ps in enumerate(passes, 1):
        f = QgsFeature(fp.fields())
        f.setAttributes([i, ps["satellite"]])
        f.setGeometry(ps["geometry"])
        feats.append(f)
    prov.addFeatures(feats)
    cats = []
    for name, meta in SATELLITES.items():
        c = QColor(meta["colour"])
        sym = QgsFillSymbol.createSimple({"color": f"{c.red()},{c.green()},{c.blue()},18",
                                          "outline_color": f"{c.red()},{c.green()},{c.blue()},255",
                                          "outline_width": "0.6"})
        cats.append(QgsRendererCategory(name, sym, name.title()))
    fp.setRenderer(QgsCategorizedSymbolRenderer("satellite", cats))

    roi = QgsVectorLayer("Point?crs=EPSG:4326" if is_point_geometry(roi_geom) else "Polygon?crs=EPSG:4326", "roi", "memory")
    f = QgsFeature()
    f.setGeometry(roi_geom)
    roi.dataProvider().addFeatures([f])
    if not is_point_geometry(roi_geom):
        roi.renderer().setSymbol(QgsFillSymbol.createSimple(
            {"color": "215,38,61,40", "outline_color": "215,38,61,255", "outline_width": "0.9"}))
    return fp, roi


def compact_products(s: str) -> str:
    """'T55HBT T55HBU T55HCT' → '55H: BT BU CT'; Landsat path/rows unchanged."""
    items = s.split()
    if not items:
        return "–"
    if not items[0].startswith("T"):
        return " ".join(items)
    groups = {}
    for t in items:
        groups.setdefault(t[1:4], []).append(t[4:])
    return " · ".join(f"{k}: {' '.join(v)}" for k, v in groups.items())


def export_field_sheet_png(path, passes: list, roi_geom: QgsGeometry, roi_name: str, tz_name: str,
                           max_passes: int = 8, basemap: str = "Esri World Imagery", tles: dict = None,
                           width: int = 2480, height: int = 3508, dpi: int = 300, target=None):
    """A4 portrait PNG (300 dpi): map of the next passes' swaths over the ROI, numbered to match the table."""
    sel = [p for p in passes if p["imaging_expected"]][:max_passes] or passes[:max_passes]
    u = width / 1000.0
    cv = P.Canvas(width, height, target, dpi)
    p = cv.p
    m = 50 * u

    P.text(p, m, 40 * u, width - 2 * m, 40 * u, f"Overpass field sheet — {roi_name}", P.font(30 * u, P.BOLD))
    if sel:
        a = to_local(sel[0]["overpass_utc"], tz_name)
        b = to_local(sel[-1]["overpass_utc"], tz_name)
        produced, rerun, expired = P.validity(passes, tz_name)
        sub = f"Next {len(sel)} expected passes · {a:%d %b} – {b:%d %b %Y} · local time ({tz_name}) and UTC"
        stamp = (f"⚠ EXPIRED — valid until {rerun}. Re-run the prediction." if expired else
                 f"⚠ Produced {produced} · RE-RUN BY {rerun}")
    else:
        sub = "No expected passes in the window"
        produced, rerun, expired = P.validity(passes, tz_name)
        stamp = f"⚠ Produced {produced} · RE-RUN BY {rerun}"
    P.text(p, m, 80 * u, width - 2 * m, 22 * u, sub, P.font(14.5 * u), P.MUTED)
    P.text(p, m, 101 * u, width - 2 * m, 22 * u, stamp, P.font(14 * u, P.BOLD),
           QColor("#B42318") if expired else P.WARN_FG)

    # ---- map
    map_top, map_h = 132 * u, 683 * u
    map_rect = QRectF(m, map_top, width - 2 * m, map_h)
    fp, roi = _memory_layers(sel, roi_geom)
    layers = [fp, roi]
    if BASEMAPS.get(basemap):
        bm = QgsRasterLayer(BASEMAPS[basemap], basemap, "wms")
        if bm.isValid():
            layers.append(bm)
    # Natural Earth land/sea always sits underneath: it is the basemap when offline, or shows through
    # wherever tiles fail to load.
    from ..core.landmask import OCEAN, land_layer
    c0 = roi_geom.centroid().asPoint()
    layers.append(land_layer((c0.x() - 12, c0.y() - 10, c0.x() + 12, c0.y() + 10)))
    ms = QgsMapSettings()
    ms.setLayers(layers)
    ms.setDestinationCrs(WEB)
    ms.setOutputSize(QSize(int(map_rect.width()), int(map_rect.height())))
    ms.setOutputDpi(dpi)
    ms.setBackgroundColor(QColor(OCEAN))
    ct = QgsCoordinateTransform(WGS, WEB, QgsProject.instance())
    a = roi_geom.centroid().asPoint()
    lat = max(-80.0, min(80.0, a.y()))
    half_km = max(170.0, 0.6 * max(roi_geom.boundingBox().width(), roi_geom.boundingBox().height()) * 111.0)
    c = ct.transform(QgsPointXY(a.x(), lat))
    k = 1.0 / math.cos(math.radians(lat))           # Web Mercator scale factor
    hw = half_km * 1000 * k
    hh = hw * map_rect.height() / map_rect.width()
    ms.setExtent(QgsRectangle(c.x() - hw, c.y() - hh, c.x() + hw, c.y() + hh))
    job = QgsMapRendererSequentialJob(ms)
    job.start()
    job.waitForFinished()
    p.drawImage(map_rect.topLeft(), job.renderedImage())

    # pass numbers on each swath centreline, staggered so they never overlap
    p.save()
    p.setClipRect(map_rect)
    ext = ms.extent()
    def to_px(lon, lat):
        q = ct.transform(QgsPointXY(lon, lat))
        return (map_rect.left() + (q.x() - ext.xMinimum()) / ext.width() * map_rect.width(),
                map_rect.top() + (ext.yMaximum() - q.y()) / ext.height() * map_rect.height())
    n = len(sel)
    for i, ps in enumerate(sel, 1):
        col = QColor(SATELLITES[ps["satellite"]]["colour"])
        pts = [to_px(lo, la) for lo, la in zip(ps.get("track_lon") or [], ps.get("track_lat") or [])]
        if len(pts) < 2:                       # rebuilt from a layer: no nadir track, badge on the swath
            g = ps["geometry"]
            ring = g.asPolygon()[0] if g and not g.isMultipart() and g.asPolygon() else []
            if len(ring) >= 4:
                h = (len(ring) - 1) // 2
                pts = [to_px((ring[i].x() + ring[len(ring) - 2 - i].x()) / 2,
                             (ring[i].y() + ring[len(ring) - 2 - i].y()) / 2) for i in range(h)]
            if len(pts) < 2:
                c = g.pointOnSurface().asPoint()
                pts = [to_px(c.x(), c.y())] * 2
        pen = QPen(col, 2 * u)
        pen.setStyle(P.DASH)
        p.setPen(pen)
        for (x1, y1), (x2, y2) in zip(pts[:-1], pts[1:]):
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        target_y = map_rect.top() + map_rect.height() * (0.07 + 0.58 * (i - 0.5) / max(n, 1))
        r = 17 * u
        inside = [q for q in pts if map_rect.left() + r + 8 * u <= q[0] <= map_rect.right() - r - 8 * u
                  and map_rect.top() + r <= q[1] <= map_rect.bottom() - r] or pts
        bx, by = min(inside, key=lambda q: abs(q[1] - target_y))
        bx = min(max(bx, map_rect.left() + r + 8 * u), map_rect.right() - r - 8 * u)
        p.setPen(QPen(P.PAPER, 3 * u))
        p.setBrush(QBrush(col))
        p.drawEllipse(QPointF(bx, by), r, r)
        P.text(p, bx - r, by - r, 2 * r, 2 * r, str(i), P.font(17 * u, P.BOLD), P.PAPER, P.ALIGN_CENTER)
    p.restore()
    p.setPen(QPen(P.INK, 1.5 * u))
    p.setBrush(QBrush())
    p.drawRect(map_rect)

    # scale bar (true ground distance at the map centre)
    km_per_px = (2 * half_km) / map_rect.width()
    target = 100 if half_km > 120 else 50
    bar_px = target / km_per_px
    bx, by = map_rect.left() + 25 * u, map_rect.bottom() - 40 * u
    p.setPen(QPen(P.INK, 2 * u))
    p.setBrush(QBrush(P.PAPER))
    p.drawRect(QRectF(bx - 10 * u, by - 32 * u, bar_px + 20 * u, 44 * u))
    p.setPen(QPen(P.INK, 3 * u))
    p.drawLine(QPointF(bx, by), QPointF(bx + bar_px, by))
    P.text(p, bx, by - 29 * u, bar_px, 20 * u, f"{target} km", P.font(12 * u, P.DEMIBOLD), P.INK, P.ALIGN_HCENTER)
    # north arrow
    nx, ny = map_rect.right() - 45 * u, map_rect.top() + 30 * u
    p.setBrush(QBrush(P.INK))
    p.setPen(P.NO_PEN)
    from qgis.PyQt.QtGui import QPolygonF
    p.drawPolygon(QPolygonF([QPointF(nx, ny), QPointF(nx - 10 * u, ny + 30 * u), QPointF(nx + 10 * u, ny + 30 * u)]))
    P.text(p, nx - 20 * u, ny + 32 * u, 40 * u, 20 * u, "N", P.font(14 * u, P.BOLD), P.INK, P.ALIGN_HCENTER)
    # legend
    n_leg = len(SATELLITES) + 4
    lx, ly = map_rect.right() - 300 * u, map_rect.bottom() - 30 * u - 26 * u * n_leg
    p.setPen(QPen(P.LINE, u))
    p.setBrush(QBrush(QColor(255, 255, 255, 230)))
    p.drawRect(QRectF(lx - 12 * u, ly - 10 * u, 290 * u, 26 * u * n_leg + 14 * u))
    for i, (name, meta) in enumerate(SATELLITES.items()):
        col = QColor(meta["colour"])
        p.setPen(QPen(col, 2 * u))
        col.setAlpha(90)
        p.setBrush(QBrush(col))
        p.drawRect(QRectF(lx, ly + i * 26 * u + 4 * u, 30 * u, 16 * u))
        P.text(p, lx + 40 * u, ly + i * 26 * u, 190 * u, 24 * u, f"{name.title()} ({meta['sensor']})", P.font(12.5 * u))
    yr = ly + len(SATELLITES) * 26 * u
    p.setPen(QPen(QColor("#D7263D"), 2.5 * u))
    p.setBrush(QBrush(QColor(215, 38, 61, 40)))
    p.drawRect(QRectF(lx, yr + 4 * u, 30 * u, 16 * u))
    P.text(p, lx + 40 * u, yr, 230 * u, 24 * u, f"ROI: {roi_name}", P.font(12.5 * u, P.DEMIBOLD))
    yr += 26 * u
    pen = QPen(P.INK, 2 * u)
    pen.setStyle(P.DASH)
    p.setPen(pen)
    p.drawLine(QPointF(lx, yr + 12 * u), QPointF(lx + 30 * u, yr + 12 * u))
    P.text(p, lx + 40 * u, yr, 240 * u, 24 * u, "Satellite ground track (nadir)", P.font(12.5 * u))
    yr += 26 * u
    p.setPen(QPen(P.INK, 2 * u))
    p.setBrush(QBrush(QColor(23, 35, 43, 30)))
    p.drawRect(QRectF(lx, yr + 4 * u, 30 * u, 16 * u))
    P.text(p, lx + 40 * u, yr, 240 * u, 24 * u, "Imaged swath (solid edges)", P.font(12.5 * u))
    yr += 26 * u
    p.setPen(QPen(P.PAPER, 2 * u))
    p.setBrush(QBrush(P.MUTED))
    p.drawEllipse(QPointF(lx + 15 * u, yr + 12 * u), 11 * u, 11 * u)
    P.text(p, lx + 4 * u, yr + 1 * u, 22 * u, 22 * u, "1", P.font(12 * u, P.BOLD), P.PAPER, P.ALIGN_CENTER)
    P.text(p, lx + 40 * u, yr, 240 * u, 24 * u, "Pass number (see table)", P.font(12.5 * u))

    # ---- table
    y = map_top + map_h + 30 * u
    cols = [("#", 30), ("Satellite", 122), ("Local date", 150), ("Local time", 190), ("UTC", 88),
            ("ROI %", 60), ("Orbit", 62), ("Product(s)", 175), ("ESA plan", 150)]
    scale = (width - 2 * m) / sum(w for _, w in cols)
    f_h, f_b = P.font(13.5 * u, P.BOLD), P.font(13.5 * u)
    rh = 36 * u
    x = m
    p.setPen(P.NO_PEN)
    p.setBrush(QBrush(QColor("#EDF1F2")))
    p.drawRect(QRectF(m, y, width - 2 * m, rh))
    for name, w in cols:
        P.text(p, x + 8 * u, y, w * scale - 10 * u, rh, name, f_h, P.INK, P.ALIGN_LEFT | P.ALIGN_VCENTER)
        x += w * scale
    y += rh
    for i, ps in enumerate(sel, 1):
        tin = to_local(ps["roi_entry_utc"], tz_name)
        tout = to_local(ps["roi_exit_utc"], tz_name)
        ca = to_local(ps["overpass_utc"], tz_name)
        plan = ps.get("esa_plan_status") or ("n/a" if ps["mission"] == "Landsat" else "not checked")
        ro = ps.get("relative_orbit")
        orbit = (f"R{int(ro):03d}" if ps["mission"] == "Sentinel-2" else f"P{int(ro):03d}") if ro else "–"
        prods = compact_products(ps.get("products") or "")
        vals = [str(i), ps["satellite"].title(), f"{ca:%a %d %b %Y}", f"{tin:%H:%M:%S}–{tout:%H:%M:%S} {ca.tzname()}",
                time.strftime("%H:%M:%S", time.gmtime(ps["overpass_utc"])), f"{ps['roi_coverage_pct']:.0f}",
                orbit, prods, plan]
        if i % 2 == 0:
            p.setPen(P.NO_PEN)
            p.setBrush(QBrush(QColor("#F7F9FA")))
            p.drawRect(QRectF(m, y, width - 2 * m, rh))
        x = m
        for j, ((name, w), v) in enumerate(zip(cols, vals)):
            colr = QColor(SATELLITES[ps["satellite"]]["colour"]) if j == 1 else P.INK
            P.text(p, x + 8 * u, y, w * scale - 10 * u, rh, v, P.font(13.5 * u, P.DEMIBOLD) if j == 1 else (P.font(11.5 * u) if j in (7, 8) else f_b),
                   colr, P.ALIGN_LEFT | P.ALIGN_VCENTER)
            x += w * scale
        p.setPen(QPen(P.LINE, u))
        p.drawLine(QPointF(m, y + rh), QPointF(width - m, y + rh))
        y += rh

    y += 30 * u
    P.caution_box(p, m, y, width - 2 * m, "Before you go to the field", P.field_cautions(passes, tz_name), 14 * u)
    P.badge(p, m, height - 52.5 * u, 29 * u)
    P.text(p, m + 38 * u, height - 50 * u, width - 2 * m, 24 * u, "Page 1 · timeline p.2 · guide p.3+",
           P.font(11.5 * u, P.DEMIBOLD), P.MUTED)
    foot = ""                                       # produced / re-run-by is stated in the header and caution box
    if tles:
        oldest = max((time.time() - t["epoch_unix"]) / 86400 for t in tles.values())
        foot += f"oldest TLE {oldest:.1f} d · "
    foot += ("Basemap: " + (f"{basemap} ({attribution(basemap)})" if BASEMAPS.get(basemap)
                               else "Natural Earth land/sea")
             + " · EO Overpass Predictor (QGIS)")
    P.text(p, m, height - 50 * u, width - 2 * m, 24 * u, foot, P.font(11.5 * u), P.MUTED, P.ALIGN_RIGHT)
    return cv.finish(path)
