"""Shared helpers for the Processing algorithms."""

import re
from pathlib import Path

from qgis.core import (QgsApplication, QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsFillSymbol, QgsGeometry, QgsPalLayerSettings,
                       QgsProcessingLayerPostProcessorInterface, QgsRendererCategory, QgsTextBufferSettings,
                       QgsTextFormat, QgsVectorLayerSimpleLabeling)
from qgis.PyQt.QtGui import QColor

from ..core.catalogue import SATELLITES, SATELLITE_NAMES
from ..core.compat import enum
from ..core.records import enrich_local
from ..core.timezones import zone_at, zones_in_bbox

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")


def data_dir() -> Path:
    d = Path(QgsApplication.qgisSettingsDirPath()) / "eo_overpass"
    d.mkdir(parents=True, exist_ok=True)
    return d


def tle_dir() -> Path:
    return data_dir() / "tle"


def plan_dir() -> Path:
    return data_dir() / "kml"


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(s)).strip("_") or "ROI"


def iter_rois(source, name_field, dissolve, context, feedback=None):
    """Yield (name, EPSG:4326 geometry) for each ROI feature, or one dissolved ROI."""
    ct = QgsCoordinateTransform(source.sourceCrs(), WGS84, context.transformContext())
    geoms = []
    for i, f in enumerate(source.getFeatures(), 1):
        g = QgsGeometry(f.geometry())
        if g.isEmpty():
            continue
        g.transform(ct)
        g = g.makeValid()
        if dissolve:
            geoms.append(g)
            continue
        name = str(f[name_field]) if name_field and f[name_field] not in (None, "") else f"ROI {i}"
        yield name, g
    if dissolve and geoms:
        yield (source.sourceName() or "ROI"), QgsGeometry.unaryUnion(geoms)


def resolve_tz(geom: QgsGeometry, override: str, feedback=None) -> str:
    if override and override.strip():
        from zoneinfo import ZoneInfo
        ZoneInfo(override.strip())
        return override.strip()
    c = geom.centroid().asPoint()
    tz = zone_at(c.x(), c.y())
    bb = geom.boundingBox()
    zs = zones_in_bbox(bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum()) | {tz}
    if len(zs) > 1 and feedback is not None:
        feedback.pushWarning(f"ROI spans several time zones {sorted(zs)}; using {tz}. Set the time-zone override to change it.")
    return tz


def satellites_from_enum(indices) -> list:
    return [SATELLITE_NAMES[i] for i in (indices or range(len(SATELLITE_NAMES)))]


class PassLayerStyler(QgsProcessingLayerPostProcessorInterface):
    """Mission colours, local-time labels and temporal (Temporal Controller) settings for pass layers."""

    _instances = []

    @classmethod
    def create(cls):
        inst = cls()
        cls._instances.append(inst)     # keep alive until QGIS calls postProcessLayer
        return inst

    def postProcessLayer(self, layer, context, feedback):
        style_pass_layer(layer)


def style_pass_layer(layer):
    cats = []
    for name, meta in SATELLITES.items():
        c = QColor(meta["colour"])
        sym = QgsFillSymbol.createSimple({"color": f"{c.red()},{c.green()},{c.blue()},40",
                                          "outline_color": f"{c.red()},{c.green()},{c.blue()},255",
                                          "outline_width": "0.5"})
        cats.append(QgsRendererCategory(name, sym, name.title()))
    layer.setRenderer(QgsCategorizedSymbolRenderer("satellite", cats))

    pal = QgsPalLayerSettings()
    pal.isExpression = True
    pal.fieldName = "title(\"satellite\") || '\\n' || \"local_date\" || ' ' || left(\"local_time\", 5)"
    fmt = QgsTextFormat()
    fmt.setSize(8)
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(0.8)
    buf.setColor(QColor("white"))
    fmt.setBuffer(buf)
    pal.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    layer.setLabelsEnabled(False)

    try:
        from qgis.core import Qgis
        mode = enum(Qgis, "VectorTemporalMode", "FeatureDateTimeInstantFromField")
    except AttributeError:
        from qgis.core import QgsVectorLayerTemporalProperties as T
        mode = T.ModeFeatureDateTimeInstantFromField
    tp = layer.temporalProperties()
    tp.setMode(mode)
    tp.setStartField("overpass_utc")
    tp.setIsActive(True)
    layer.triggerRepaint()


def write_field_pack(folder, roi_name, roi_geom, passes, tles, orbits, tz, start_unix, end_unix,
                     basemap="OpenStreetMap", feedback=None) -> dict:
    """CSV + live-tracker HTML + timeline / field-sheet / globe PNGs for one ROI."""
    from ..core.records import write_csv
    from ..exports.globe_png import export_globe_png
    from ..exports.html_tracker import export_tracker_html
    from ..exports.map_png import export_field_sheet_png
    from ..exports.timeline_png import export_timeline_png

    out = Path(folder) / safe_name(roi_name)
    out.mkdir(parents=True, exist_ok=True)
    stem = safe_name(roi_name)
    files = {}

    def step(label, fn, path):
        try:
            fn(path)
            files[label] = str(path)
            if feedback:
                feedback.pushInfo(f"  ✔ {label}: {path}")
        except Exception as exc:
            if feedback:
                feedback.reportError(f"  ✘ {label} failed: {exc}", fatalError=False)

    step("CSV report", lambda p: write_csv(p, passes), out / f"overpasses_{stem}.csv")
    step("Live tracker (HTML)", lambda p: export_tracker_html(p, tles, roi_geom, roi_name, tz, passes),
         out / f"live_tracker_{stem}.html")
    step("Timeline (PNG)", lambda p: export_timeline_png(p, passes, roi_name, tz, start_unix, end_unix, tles),
         out / f"timeline_{stem}.png")
    step("Field sheet (PNG)", lambda p: export_field_sheet_png(p, passes, roi_geom, roi_name, tz, basemap=basemap,
                                                               tles=tles), out / f"field_sheet_{stem}.png")
    if passes and orbits:
        step("Orbit globe (PNG)", lambda p: export_globe_png(p, passes, orbits, roi_geom, roi_name, tz),
             out / f"orbit_globe_{stem}.png")
    return files


__all__ = ["data_dir", "tle_dir", "plan_dir", "iter_rois", "resolve_tz", "satellites_from_enum",
           "PassLayerStyler", "style_pass_layer", "write_field_pack", "enrich_local", "WGS84"]
