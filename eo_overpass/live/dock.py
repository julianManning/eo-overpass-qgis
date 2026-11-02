"""Live tracker dock: satellite positions, ground tracks, current swaths and night side on the QGIS canvas."""

import time

import numpy as np
from qgis.core import (QgsApplication, QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsFeature, QgsFields, QgsFillSymbol, QgsGeometry, QgsLineSymbol,
                       QgsMarkerSymbol, QgsPalLayerSettings, QgsPointXY, QgsProject, QgsRendererCategory,
                       QgsTask, QgsTextBufferSettings, QgsTextFormat, QgsVectorLayer,
                       QgsVectorLayerSimpleLabeling)
from qgis.gui import QgsDockWidget, QgsMapLayerComboBox
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (QCheckBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSpinBox,
                                 QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..algorithms.common import plan_dir, resolve_tz, tle_dir
from ..core.catalogue import SATELLITES
from ..core.compat import enum
from ..core.geo import geod_direct, geod_inverse
from ..core.orbit import build_orbits
from ..core.sun import subsolar_point
from ..core.timezones import to_local
from ..core.tle import fetch_tles

GROUP = "EO Overpass — live"
WINDOW_DAYS = 14                    # matches the website; longer means older TLEs


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
            t0 = time.time() - 600
            self.passes = [enrich_local(p) for p in predict_overpasses(
                self.geom, self.dock.orbits, self.dock.tles, t0, WINDOW_DAYS, tz_name=self.tz, roi_name=self.name)]
            try:    # Sentinel-2 imaging confirmation from ESA's plans; offline = cached KMLs only
                from ..core.plans import crosscheck, fetch_plans, load_plans
                try:
                    fetch_plans(plan_dir(), t0, t0 + WINDOW_DAYS * 86400, log=lambda m: None)
                except Exception:
                    pass
                crosscheck(self.passes, load_plans(plan_dir()), self.geom)
            except Exception:
                pass    # rows then show "—" for the plan column
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
        row.addWidget(QLabel("ROI"))
        self.roi_combo = QgsMapLayerComboBox()
        try:
            from qgis.core import Qgis
            self.roi_combo.setFilters(enum(Qgis, "LayerFilter", "PolygonLayer") | enum(Qgis, "LayerFilter", "PointLayer"))
        except Exception:
            from qgis.core import QgsMapLayerProxyModel
            self.roi_combo.setFilters(QgsMapLayerProxyModel.PolygonLayer | QgsMapLayerProxyModel.PointLayer)
        self.roi_combo.setAllowEmptyLayer(True)
        self._hide_own_outputs()                    # Predict's Overpasses / Products layers are not ROIs
        QgsProject.instance().layersAdded.connect(self._hide_own_outputs)
        row.addWidget(self.roi_combo, 1)
        btn_file = QPushButton("…")
        btn_file.setToolTip("Choose an ROI file (GeoPackage, shapefile, GeoJSON …) without adding it to the map")
        btn_file.setMaximumWidth(32)
        btn_file.clicked.connect(self._browse_roi)
        row.addWidget(btn_file)
        self.btn_passes = QPushButton("Next passes")
        self.btn_passes.setToolTip("Predict the next 14 days of imaging passes for the selected ROI layer")
        self.btn_passes.clicked.connect(self.compute_passes)
        row.addWidget(self.btn_passes)
        lay.addLayout(row)
        self.roi_combo.layerChanged.connect(lambda *_: self._refresh_roi_note())
        self._file_roi = None                   # (name, EPSG:4326 geometry) from the … button
        self._refresh_roi_note()

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
        row2.addWidget(QLabel("Update every"))
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

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Basemap"))
        from qgis.PyQt.QtWidgets import QComboBox
        from ..core.basemaps import BASEMAP_NAMES, OFFLINE
        self.basemap_combo = QComboBox()
        self.basemap_combo.addItems(BASEMAP_NAMES + [OFFLINE])
        self.basemap_combo.setCurrentText("Esri World Imagery")
        row3.addWidget(self.basemap_combo, 1)
        btn_bm = QPushButton("Add to map")
        btn_bm.setToolTip("Add the selected basemap at the bottom of the layer list (the Natural Earth one works offline)")
        btn_bm.clicked.connect(self._add_basemap)
        row3.addWidget(btn_bm)
        lay.addLayout(row3)

        self.clock = QLabel("—")
        self.clock.setStyleSheet("font-family: Consolas, monospace; font-size: 12px")
        lay.addWidget(self.clock)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Satellite", "Lat", "Lon", "Alt km", "Dir"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(enum(QHeaderView, "ResizeMode", "Stretch"))
        lay.addWidget(self.table)
        lay.addWidget(QLabel("<b>Next ROI overpasses</b> (press <i>Next passes</i>)"))
        heads = ["Satellite", "Countdown", "Local date / time", "UTC date / time", "ROI %", "Direction", "View °",
                 "Will image?", "TLE age"]
        self.ptable = QTableWidget(0, len(heads))
        self.ptable.setHorizontalHeaderLabels(heads)
        self.ptable.verticalHeader().setVisible(False)
        hdr = self.ptable.horizontalHeader()
        for col in range(len(heads)):           # fit contents; the table scrolls sideways in a narrow dock
            hdr.setSectionResizeMode(col, enum(QHeaderView, "ResizeMode", "ResizeToContents"))
        lay.addWidget(self.ptable, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #664d03; background: #fff3cd; border-left: 4px solid #f0ad4e; padding: 4px")
        self.status.setText("⚠ Live positions come from the cached TLEs (refreshed every 12 h). "
                            "Swaths are nominal widths; acquisition is not guaranteed.")
        lay.addWidget(self.status)
        self.setWidget(w)

        self.idle = QTimer(self)                # keeps countdowns ticking while paused (tables only)
        self.idle.setInterval(1000)
        self.idle.timeout.connect(lambda: None if self.btn_live.isChecked() else self.update())
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.update)

    # ---------------------------------------------------------------------------------------------
    def toggle(self, on):
        self.btn_live.setText("⏸ Pause" if on else "▶ Live")
        if on:
            self._check_crs()
            if self.tles is None:
                self._load_tles()
            else:
                self._ensure_layers()
                self.update(force_tracks=True)
                self.timer.start()
        else:
            self.timer.stop()
            self._restore_crs()

    def _add_basemap(self):
        from ..core.basemaps import add_basemap_to_project
        name = self.basemap_combo.currentText()
        if add_basemap_to_project(name) is None:
            self.status.setText(f"Could not add {name} (check your network connection).")

    # ---------------------------------------------------------------------------------------------
    _GLOBAL_OK = {"EPSG:3857", "ESRI:54030", "EPSG:8857", "ESRI:54009", "ESRI:54042"}

    def _check_crs(self):
        """Orbits are global: offer to switch a local/projected map CRS (e.g. UTM) to WGS 84 while tracking."""
        proj = QgsProject.instance()
        crs = proj.crs()
        if not crs.isValid() or crs.authid() in self._GLOBAL_OK or crs.authid() == "EPSG:4326":
            return
        from qgis.PyQt.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(enum(QMessageBox, "Icon", "Question"))
        box.setWindowTitle("EO Overpass — live tracker")
        box.setText(f"The map is in <b>{crs.authid()}</b> ({crs.description()}).")
        box.setInformativeText(
            "Satellite orbits circle the whole Earth, so a local projection such as UTM distorts the tracks and "
            "swaths badly. A national datum such as GDA2020 also makes QGIS warn about 'ballpark' transforms "
            "when the global track layers are drawn.<br><br>Switch the map to <b>WGS 84 (EPSG:4326)</b> while the "
            "tracker runs? "
            f"{crs.authid()} is restored when you pause the tracker.")
        yes = box.addButton("Switch to WGS 84", enum(QMessageBox, "ButtonRole", "AcceptRole"))
        box.addButton("Keep " + crs.authid(), enum(QMessageBox, "ButtonRole", "RejectRole"))
        box.setDefaultButton(yes)
        box.exec() if hasattr(box, "exec") else box.exec_()
        if box.clickedButton() is not yes:
            return
        self._previous_crs = crs
        self._previous_extent = self.iface.mapCanvas().extent() if self.iface is not None else None
        proj.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        if self.iface is not None:
            from qgis.core import QgsRectangle
            canvas = self.iface.mapCanvas()
            canvas.setExtent(QgsRectangle(-180, -90, 180, 90))
            canvas.refresh()

    def _restore_crs(self):
        prev = getattr(self, "_previous_crs", None)
        if prev is None:
            return
        QgsProject.instance().setCrs(prev)
        if self.iface is not None and getattr(self, "_previous_extent", None) is not None:
            self.iface.mapCanvas().setExtent(self._previous_extent)
            self.iface.mapCanvas().refresh()
        self._previous_crs = None
        self._previous_extent = None

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

    def _hide_own_outputs(self, *_):
        # Predict's outputs and this dock's own live layers are not ROIs
        self.roi_combo.setExceptedLayerList([
            l for l in QgsProject.instance().mapLayers().values()
            if isinstance(l, QgsVectorLayer) and ("overpass_utc" in l.fields().names() or l.customProperty("eo_live"))])

    @staticmethod
    def _layer_roi(layer):
        """(name, EPSG:4326 geometry) of a polygon/point layer, or None if it has no geometry."""
        ct = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
        ct.setBallparkTransformsAreAppropriate(True)       # metre-level datum shifts are irrelevant here
        geoms = []
        for f in layer.getFeatures():
            g = QgsGeometry(f.geometry())
            g.transform(ct)
            geoms.append(g.makeValid())
        return (layer.name(), QgsGeometry.unaryUnion(geoms)) if geoms else None

    def _browse_roi(self):
        from qgis.PyQt.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose an ROI file", "", "Vector files (*.gpkg *.shp *.geojson *.json *.kml *.gml);;All files (*)")
        if not path:
            return
        from pathlib import Path
        lyr = QgsVectorLayer(path, Path(path).stem, "ogr")
        roi = self._layer_roi(lyr) if lyr.isValid() else None
        if roi is None:
            self.status.setText(f"Could not read any geometry from {path}.")
            return
        self._file_roi = roi
        self.roi_combo.setLayer(None)               # the file takes over from the map layer
        self._refresh_roi_note()

    def _current_roi(self):
        """(name, geometry, source text): the picked map layer, else the chosen file, else Predict's last ROI."""
        layer = self.roi_combo.currentLayer()
        if layer is not None:
            roi = self._layer_roi(layer)
            return (roi + ("map layer",)) if roi else None
        if self._file_roi:
            return self._file_roi + ("file",)
        from ..core import last_roi
        last = last_roi.load()
        return (last + ("Predict",)) if last else None

    def _refresh_roi_note(self, *_):
        """The combo's empty entry names the ROI in use (file or last Predict run), so it never looks blank."""
        if self.roi_combo.currentLayer() is not None or getattr(self, "_in_refresh", False):
            return
        self._in_refresh = True
        if self._file_roi:
            text = f"{self._file_roi[0]}  (ROI file)"
        else:
            from ..core import last_roi
            last = last_roi.load()
            text = f"{last[0]}  (ROI from Predict)" if last else "No ROI yet: run Predict or press …"
        try:
            self.roi_combo.setAllowEmptyLayer(True, text)
        except TypeError:                           # older QGIS: no custom text for the empty entry
            pass
        self.roi_combo.setLayer(None)
        self._in_refresh = False

    def open_for_last_roi(self):
        """Use the ROI of the Predict run that just finished and list its passes."""
        self.roi_combo.setLayer(None)
        self._file_roi = None
        self.compute_passes()

    def showEvent(self, e):
        self._refresh_roi_note()                    # Predict may have run since the dock was last shown
        self.idle.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self.idle.stop()
        super().hideEvent(e)

    def compute_passes(self):
        self._refresh_roi_note()
        roi = self._current_roi()
        if roi is None:
            self.status.setText("No ROI: pick a layer, press … to choose a file, or run Predict overpasses first.")
            return
        if self.tles is None:
            self._pending_passes = True
            self._load_tles()
            return
        name, geom, _src = roi
        tz = resolve_tz(geom, "")
        self.ptable.setRowCount(0)
        self.status.setText(f"Predicting passes for '{name}' ({tz}) …")
        self._ptask = _PassTask(self, geom, name, tz)
        QgsApplication.taskManager().addTask(self._ptask)

    def _on_passes(self, passes, tz, error):
        if error:
            self.status.setText(f"Prediction failed: {error}")
            return
        self.passes, self.tz = passes, tz
        if not passes:
            self.status.setText(f"No imaging passes in the next {WINDOW_DAYS} days ({tz}). The ROI may be very small, or the "
                                "selected satellites do not cover it; run Predict overpasses with a longer window.")
            self.update()
            return
        self.status.setText(f"⚠ {len(passes)} geometric imaging passes in the next {WINDOW_DAYS} days ({tz}). Sentinel-2 are "
                            "checked against ESA's acquisition plans; Landsat is routine. Acquisition is never "
                            "guaranteed and cloud is not forecast.")
        self.update()

    # ---------------------------------------------------------------------------------------------
    def _alive(self, lyr):
        try:    # layer removed by the user: the C++ object is gone and any call raises RuntimeError
            return QgsProject.instance().mapLayer(lyr.id()) is not None
        except RuntimeError:
            return False

    def _ensure_layers(self):
        proj = QgsProject.instance()
        if self.layers and all(self._alive(lyr) for lyr in self.layers.values()):
            return
        for lyr in self.layers.values():        # drop any survivors of a partly deleted set
            if self._alive(lyr):
                proj.removeMapLayer(lyr.id())
        self._last_tracks = 0
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
            lyr.setCustomProperty("eo_live", True)
            proj.addMapLayer(lyr, False)
            group.addLayer(lyr)
            self.layers[key] = lyr

    def _set(self, key, feats):
        lyr = self.layers.get(key)
        if lyr is None or not self._alive(lyr):
            return
        pr = lyr.dataProvider()
        pr.truncate()
        pr.addFeatures(feats)
        lyr.updateExtents()
        lyr.triggerRepaint()

    def _feat(self, lyr_key, sat, geom):
        f = QgsFeature(self.layers[lyr_key].fields() if self._alive(self.layers[lyr_key]) else QgsFields())
        f.setAttributes([sat])
        f.setGeometry(geom)
        return f

    def update(self, force_tracks=False):
        if not self.orbits:
            return
        live = self.btn_live.isChecked()
        if live:
            self._ensure_layers()               # recreate if the user deleted the live layers
        draw = live or bool(self.layers)       # paused with no map layers: tables and clock only
        now = time.time()
        sel = [n for n in self.orbits if self.chk[n].isChecked()]
        sats, swaths, rows = [], [], []
        for n in sel:
            orb = self.orbits[n]
            la, lo, h = orb.subpoint(np.array([now, now + 2.0]))
            if draw:
                sats.append(self._feat("sat", n, QgsGeometry.fromPointXY(QgsPointXY(lo[0], la[0]))))
            rows.append((n, la[0], lo[0], h[0], "↑" if la[1] > la[0] else "↓"))
            if not draw:
                continue
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
        if draw and (force_tracks or now - self._last_tracks > 30):
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
        age_d = max((now - t["epoch_unix"]) / 86400 for t in self.tles.values()) if self.tles else 0
        stale = age_d > 7                       # LEO TLE error grows quickly beyond about a week
        self.clock.setText(f"UTC {utc}   |   {self.tz}  {loc:%H:%M:%S %Z}   |   TLEs up to {age_d:.1f} d old"
                           + (" ⚠ stale, refresh" if stale else ""))
        self.clock.setStyleSheet("font-family: Consolas, monospace; font-size: 12px"
                                 + ("; color: #b26a00" if stale else ""))
        self.table.setRowCount(len(rows))
        for i, (n, la, lo, h, d) in enumerate(rows):
            for j, v in enumerate([n.title(), f"{la:+.2f}", f"{lo:+.2f}", f"{h:.0f}", d]):
                it = QTableWidgetItem(v)
                if j == 0:
                    it.setForeground(QColor(SATELLITES[n]["colour"]))
                self.table.setItem(i, j, it)
        up = [p for p in self.passes if p["roi_exit_utc"] >= now and p["satellite"] in sel][:40]
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
            st = p.get("esa_plan_status") or ""
            if p["satellite"].startswith("LANDSAT"):
                plan, pc = "Landsat: routine", "#2e7d32"        # Landsat 8/9 image all land every pass
            elif st.startswith("planned"):
                plan, pc = "Planned (ESA)", "#2e7d32"
            elif st == "not planned":
                plan, pc = "Not planned (ESA)", "#c62828"
            else:
                plan, pc = "Plan unknown", "#666666"
            age = p.get("tle_age_days")
            aged = age is not None and age > 7      # same fresh/aged split as the website
            tle = "" if age is None else f"{'aged' if aged else 'fresh'} · {age:.1f} d"
            for j, v in enumerate([p["satellite"].title(), cd, f"{loc:%a %d %b %Y %H:%M} {loc.tzname()}",
                                   time.strftime("%a %d %b %Y %H:%M UTC", time.gmtime(p["overpass_utc"])),
                                   f"{p['roi_coverage_pct']:.0f}", (p.get("direction") or "").title(),
                                   f"{p['view_angle_deg']:.1f}" if p.get("view_angle_deg") is not None else "",
                                   plan, tle]):
                it = QTableWidgetItem(v)
                if j == 0:
                    it.setForeground(QColor(SATELLITES[p["satellite"]]["colour"]))
                elif j == 7:
                    it.setForeground(QColor(pc))
                elif j == 8 and aged:
                    it.setForeground(QColor("#b26a00"))
                self.ptable.setItem(i, j, it)

    def closeEvent(self, e):
        self.timer.stop()
        self.btn_live.setChecked(False)
        super().closeEvent(e)
