"""Live tracker dock: satellite positions, ground tracks, current swaths and night side on the QGIS canvas."""

import time

import numpy as np
from qgis.core import (QgsApplication, QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsFeature, QgsFillSymbol, QgsGeometry, QgsLineSymbol,
                       QgsMarkerSymbol, QgsPalLayerSettings, QgsPointXY, QgsProject, QgsRendererCategory,
                       QgsTask, QgsTextBufferSettings, QgsTextFormat, QgsVectorLayer,
                       QgsVectorLayerSimpleLabeling)
from qgis.gui import QgsDockWidget, QgsMapLayerComboBox
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (QCheckBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSpinBox,
                                 QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..algorithms.common import resolve_tz, tle_dir
from ..core.catalogue import SATELLITES
from ..core.compat import enum
from ..core.geo import geod_direct, geod_inverse
from ..core.orbit import build_orbits
from ..core.sun import subsolar_point
from ..core.timezones import to_local
from ..core.tle import fetch_tles

GROUP = "EO Overpass — live"


def _split(lons, lats):
    segs, cur = [], []
    for i in range(len(lons)):
        if cur and abs(lons[i] - lons[i - 1]) > 180:
            if len(cur) > 1:
                segs.append(cur)
            cur = []
        cur.append(QgsPointXY(float(lons[i]), float(lats[i])))
    if len(cur) > 1:
        segs.append(cur)
    return segs


def _night_polygon(t):
    dec, slon = subsolar_point(t)
    dec = dec if abs(dec) > 1e-3 else 1e-3
    lons = np.arange(-180.0, 180.1, 2.0)
    lat_t = np.degrees(np.arctan(-np.cos(np.radians(lons - slon)) / np.tan(np.radians(dec))))
    pole = -85.0 if dec > 0 else 85.0
    ring = [QgsPointXY(-180, pole)] + [QgsPointXY(float(a), float(np.clip(b, -85, 85))) for a, b in zip(lons, lat_t)]
    ring += [QgsPointXY(180, pole), QgsPointXY(-180, pole)]
    return QgsGeometry.fromPolygonXY([ring])


class _TleTask(QgsTask):
    def __init__(self, dock):
        super().__init__("EO Overpass: loading TLEs", QgsTask.CanCancel if hasattr(QgsTask, "CanCancel") else
                         QgsTask.Flag.CanCancel)
        self.dock, self.tles, self.error = dock, None, None

    def run(self):
        try:
            self.tles = fetch_tles(tle_dir(), max_age_hours=12, log=lambda m: None)
            return True
        except Exception as exc:
            self.error = str(exc)
            return False

    def finished(self, ok):
        self.dock._on_tles(self.tles, self.error)


class _PassTask(QgsTask):
    def __init__(self, dock, geom, name, tz):
        super().__init__("EO Overpass: next passes", QgsTask.CanCancel if hasattr(QgsTask, "CanCancel") else
                         QgsTask.Flag.CanCancel)
        self.dock, self.geom, self.name, self.tz, self.passes, self.error = dock, geom, name, tz, [], None

    def run(self):
        try:
            from ..core.predict import predict_overpasses
            from ..core.records import enrich_local
            self.passes = [enrich_local(p) for p in predict_overpasses(
                self.geom, self.dock.orbits, self.dock.tles, time.time() - 600, 10, tz_name=self.tz, roi_name=self.name)]
            return True
        except Exception as exc:
            self.error = str(exc)
            return False

    def finished(self, ok):
        self.dock._on_passes(self.passes, self.tz, self.error)


class LiveTrackerDock(QgsDockWidget):
    def __init__(self, iface):
        super().__init__("EO Overpass — live tracker")
        self.setObjectName("EOOverpassLiveDock")
        self.iface = iface
        self.tles, self.orbits, self.passes, self.tz = None, {}, [], "UTC"
        self.layers = {}
        self._last_tracks = 0
        self._task = None

        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("ROI layer"))
        self.roi_combo = QgsMapLayerComboBox()
        try:
            from qgis.core import Qgis
            self.roi_combo.setFilters(enum(Qgis, "LayerFilter", "PolygonLayer") | enum(Qgis, "LayerFilter", "PointLayer"))
        except Exception:
            from qgis.core import QgsMapLayerProxyModel
            self.roi_combo.setFilters(QgsMapLayerProxyModel.PolygonLayer | QgsMapLayerProxyModel.PointLayer)
        self.roi_combo.setAllowEmptyLayer(True)
        row.addWidget(self.roi_combo, 1)
        self.btn_passes = QPushButton("Next passes")
        self.btn_passes.setToolTip("Predict the next 10 days of imaging passes for the selected ROI layer")
        self.btn_passes.clicked.connect(self.compute_passes)
        row.addWidget(self.btn_passes)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        self.chk = {}
        for n in SATELLITES:
            cb = QCheckBox(n.title().replace("Sentinel-", "S").replace("Landsat ", "L"))
            cb.setChecked(True)
            cb.setStyleSheet(f"color: {SATELLITES[n]['colour']}; font-weight: 600")
            cb.toggled.connect(lambda _=None: self.update(force_tracks=True))
            self.chk[n] = cb
            row2.addWidget(cb)
        row2.addStretch(1)
        row2.addWidget(QLabel("every"))
        self.spin = QSpinBox()
        self.spin.setRange(1, 60)
        self.spin.setValue(2)
        self.spin.setSuffix(" s")
        self.spin.valueChanged.connect(lambda v: self.timer.setInterval(v * 1000))
        row2.addWidget(self.spin)
        self.btn_live = QPushButton("▶ Live")
        self.btn_live.setCheckable(True)
        self.btn_live.toggled.connect(self.toggle)
        row2.addWidget(self.btn_live)
        lay.addLayout(row2)

        self.clock = QLabel("—")
        self.clock.setStyleSheet("font-family: Consolas, monospace; font-size: 12px")
        lay.addWidget(self.clock)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Satellite", "Lat", "Lon", "Alt km", "Dir"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(enum(QHeaderView, "ResizeMode", "Stretch"))
        lay.addWidget(self.table)
        lay.addWidget(QLabel("<b>Next ROI overpasses</b> (press <i>Next passes</i>)"))
        self.ptable = QTableWidget(0, 4)
        self.ptable.setHorizontalHeaderLabels(["Satellite", "Countdown", "Local", "ROI %"])
        self.ptable.verticalHeader().setVisible(False)
        self.ptable.horizontalHeader().setSectionResizeMode(enum(QHeaderView, "ResizeMode", "Stretch"))
        lay.addWidget(self.ptable, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #664d03; background: #fff3cd; border-left: 4px solid #f0ad4e; padding: 4px")
        self.status.setText("⚠ Live positions come from the cached TLEs (refreshed every 12 h). "
                            "Swaths are nominal widths; acquisition is not guaranteed.")
        lay.addWidget(self.status)
        self.setWidget(w)

        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.update)

    # ---------------------------------------------------------------------------------------------
    def toggle(self, on):
        self.btn_live.setText("⏸ Pause" if on else "▶ Live")
        if on:
            if self.tles is None:
                self._load_tles()
            else:
                self._ensure_layers()
                self.update(force_tracks=True)
                self.timer.start()
        else:
            self.timer.stop()

    def _load_tles(self):
        self.clock.setText("Loading orbital elements …")
        self._task = _TleTask(self)
        QgsApplication.taskManager().addTask(self._task)

    def _on_tles(self, tles, error):
        if error or not tles:
            self.clock.setText(f"Could not load TLEs: {error}")
            self.btn_live.setChecked(False)
            return
        self.tles = tles
        self.orbits = build_orbits(tles)
        self.clock.setText("Orbital elements loaded.")
        if getattr(self, "_pending_passes", False):
            self._pending_passes = False
            self.compute_passes()
        if self.btn_live.isChecked():
            self._ensure_layers()
            self.update(force_tracks=True)
            self.timer.start()

    def compute_passes(self):
        layer = self.roi_combo.currentLayer()
        if layer is None:
            self.status.setText("Select an ROI layer first.")
            return
        if self.tles is None:
            self._pending_passes = True
            self._load_tles()
            return
        ct = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
        geoms = []
        for f in layer.getFeatures():
            g = QgsGeometry(f.geometry())
            g.transform(ct)
            geoms.append(g.makeValid())
        if not geoms:
            return
        geom = QgsGeometry.unaryUnion(geoms)
        tz = resolve_tz(geom, "")
        self.ptable.setRowCount(0)
        self.status.setText(f"Predicting passes for '{layer.name()}' ({tz}) …")
        self._ptask = _PassTask(self, geom, layer.name(), tz)
        QgsApplication.taskManager().addTask(self._ptask)

    def _on_passes(self, passes, tz, error):
        if error:
            self.status.setText(f"Prediction failed: {error}")
            return
        self.passes, self.tz = passes, tz
        self.status.setText(f"⚠ {len(passes)} geometric imaging passes in the next 10 days ({tz}). Acquisition is not "
                            "guaranteed — run Processing ▸ EO Overpass ▸ Predict overpasses with the ESA cross-check "
                            "for Sentinel-2 confirmation.")
        self.update()

    # ---------------------------------------------------------------------------------------------
    def _ensure_layers(self):
        proj = QgsProject.instance()
        alive = all(lyr is not None and proj.mapLayer(lyr.id()) is not None for lyr in self.layers.values()) \
            if self.layers else False
        if alive:
            return
        root = proj.layerTreeRoot()
        group = root.findGroup(GROUP) or root.insertGroup(0, GROUP)

        def cat_renderer(kind):
            cats = []
            for n, meta in SATELLITES.items():
                c = QColor(meta["colour"])
                rgb = f"{c.red()},{c.green()},{c.blue()}"
                if kind == "point":
                    s = QgsMarkerSymbol.createSimple({"name": "circle", "color": rgb, "size": "3.2",
                                                      "outline_color": "255,255,255", "outline_width": "0.5"})
                elif kind == "future":
                    s = QgsLineSymbol.createSimple({"color": rgb, "width": "0.6"})
                elif kind == "past":
                    s = QgsLineSymbol.createSimple({"color": rgb + ",150", "width": "0.4", "line_style": "dash"})
                else:
                    s = QgsFillSymbol.createSimple({"color": rgb + ",80", "outline_color": rgb, "outline_width": "0.3"})
                cats.append(QgsRendererCategory(n, s, n.title()))
            return QgsCategorizedSymbolRenderer("satellite", cats)

        specs = [("night", "Polygon", None), ("swath", "Polygon", "fill"), ("past", "LineString", "past"),
                 ("future", "LineString", "future"), ("sat", "Point", "point")]
        titles = {"night": "Night side", "swath": "Current swaths", "past": "Track (last 45 min)",
                  "future": "Track (next 90 min)", "sat": "Satellites"}
        self.layers = {}
        for key, gtype, rkind in reversed(specs):
            lyr = QgsVectorLayer(f"{gtype}?crs=EPSG:4326&field=satellite:string(20)", titles[key], "memory")
            if rkind:
                lyr.setRenderer(cat_renderer(rkind))
            else:
                lyr.renderer().setSymbol(QgsFillSymbol.createSimple({"color": "0,0,0,70", "outline_style": "no"}))
            if key == "sat":
                pal = QgsPalLayerSettings()
                pal.fieldName = "satellite"
                fmt = QgsTextFormat()
                fmt.setSize(9)
                buf = QgsTextBufferSettings()
                buf.setEnabled(True)
                buf.setColor(QColor("white"))
                fmt.setBuffer(buf)
                pal.setFormat(fmt)
                lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
                lyr.setLabelsEnabled(True)
            proj.addMapLayer(lyr, False)
            group.addLayer(lyr)
            self.layers[key] = lyr

    def _set(self, key, feats):
        lyr = self.layers.get(key)
        if lyr is None or QgsProject.instance().mapLayer(lyr.id()) is None:
            return
        pr = lyr.dataProvider()
        pr.truncate()
        pr.addFeatures(feats)
        lyr.updateExtents()
        lyr.triggerRepaint()

    def _feat(self, lyr_key, sat, geom):
        f = QgsFeature(self.layers[lyr_key].fields())
        f.setAttributes([sat])
        f.setGeometry(geom)
        return f

    def update(self, force_tracks=False):
        if not self.orbits or not self.layers:
            return
        now = time.time()
        sel = [n for n in self.orbits if self.chk[n].isChecked()]
        sats, swaths, rows = [], [], []
        for n in sel:
            orb = self.orbits[n]
            la, lo, h = orb.subpoint(np.array([now, now + 2.0]))
            sats.append(self._feat("sat", n, QgsGeometry.fromPointXY(QgsPointXY(lo[0], la[0]))))
            rows.append((n, la[0], lo[0], h[0], "↑" if la[1] > la[0] else "↓"))
            t = now + np.arange(-150, 151, 10.0)
            sla, slo, _ = orb.subpoint(t)
            az, _ = geod_inverse(slo[:-1], sla[:-1], slo[1:], sla[1:])
            az = np.append(az, az[-1])
            half = SATELLITES[n]["swath_km"] * 500.0
            llo, lla = geod_direct(slo, sla, az - 90, half)
            rlo, rla = geod_direct(slo, sla, az + 90, half)
            ring_lon = np.concatenate([llo, rlo[::-1]])
            if ring_lon.max() - ring_lon.min() < 180:
                ring = [QgsPointXY(float(a), float(b)) for a, b in zip(ring_lon, np.concatenate([lla, rla[::-1]]))]
                ring.append(ring[0])
                swaths.append(self._feat("swath", n, QgsGeometry.fromPolygonXY([ring])))
        self._set("sat", sats)
        self._set("swath", swaths)
        if force_tracks or now - self._last_tracks > 30:
            past, fut = [], []
            for n in sel:
                orb = self.orbits[n]
                tp = now + np.arange(-45 * 60, 1, 30.0)
                tf = now + np.arange(0, 90 * 60 + 1, 30.0)
                for t, bucket, key in ((tp, past, "past"), (tf, fut, "future")):
                    la, lo, _ = orb.subpoint(t)
                    for seg in _split(lo, la):
                        bucket.append(self._feat(key, n, QgsGeometry.fromPolylineXY(seg)))
            self._set("past", past)
            self._set("future", fut)
            self._set("night", [self._feat("night", "", _night_polygon(now))])
            self._last_tracks = now

        utc = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now))
        loc = to_local(now, self.tz)
        self.clock.setText(f"UTC {utc}   |   {self.tz}  {loc:%H:%M:%S %Z}")
        self.table.setRowCount(len(rows))
        for i, (n, la, lo, h, d) in enumerate(rows):
            for j, v in enumerate([n.title(), f"{la:+.2f}", f"{lo:+.2f}", f"{h:.0f}", d]):
                it = QTableWidgetItem(v)
                if j == 0:
                    it.setForeground(QColor(SATELLITES[n]["colour"]))
                self.table.setItem(i, j, it)
        up = [p for p in self.passes if p["roi_exit_utc"] >= now and p["satellite"] in sel][:12]
        self.ptable.setRowCount(len(up))
        for i, p in enumerate(up):
            s = int(p["roi_entry_utc"] - now)
            if s <= 0:
                cd = "imaging now"
            else:
                d, r = divmod(s, 86400)
                hh, r = divmod(r, 3600)
                cd = f"{d}d {hh:02d}h {r // 60:02d}m" if d else f"{hh:02d}:{r // 60:02d}:{r % 60:02d}"
            loc = to_local(p["overpass_utc"], p["timezone"])
            for j, v in enumerate([p["satellite"].title(), cd, f"{loc:%a %d %b %H:%M:%S}", f"{p['roi_coverage_pct']:.0f}"]):
                it = QTableWidgetItem(v)
                if j == 0:
                    it.setForeground(QColor(SATELLITES[p["satellite"]]["colour"]))
                self.ptable.setItem(i, j, it)

    def closeEvent(self, e):
        self.timer.stop()
        self.btn_live.setChecked(False)
        super().closeEvent(e)
