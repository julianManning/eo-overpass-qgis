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
    # Swaths are hundreds of km wide and timing is ~1 s (≈7 km): a metre-level datum shift
    # (e.g. GDA2020/GDA94 → WGS 84) is irrelevant, so a ballpark transform is acceptable here.
    ct.setBallparkTransformsAreAppropriate(True)
    geoms = []
    single = source.featureCount() == 1
    if not name_field and not single:       # several features, no field chosen: use an obvious name field if any
        names = {f.name().lower(): f.name() for f in source.fields()}
        name_field = next((names[k] for k in ("name", "site", "site_name", "roi", "roi_name", "label") if k in names),
                          None)
    for i, f in enumerate(source.getFeatures(), 1):
        g = QgsGeometry(f.geometry())
        if g.isEmpty():
            continue
        g.transform(ct)
        g = g.makeValid()
        if dissolve:
            geoms.append(g)
            continue
        val = f[name_field] if name_field else None
        if val not in (None, "") and str(val) != "NULL":
            name = str(val)
        elif single and source.sourceName():
            name = source.sourceName()
        else:
            name = f"ROI {i}"
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


def sat_param_name(sat: str) -> str:
    return "SAT_" + sat.replace("SENTINEL-", "S").replace("LANDSAT ", "L")      # SAT_L8, SAT_S2A …


def add_satellite_parameters(alg):
    """One tick box per satellite (all ticked by default)."""
    from qgis.core import QgsProcessingParameterBoolean
    for s in SATELLITE_NAMES:
        alg.addParameter(QgsProcessingParameterBoolean(
            sat_param_name(s), f"{s.title()} ({SATELLITES[s]['sensor']}, {SATELLITES[s]['swath_km']:.0f} km swath)",
            defaultValue=True))


def satellites_from_parameters(alg, parameters, context) -> list:
    sats = [s for s in SATELLITE_NAMES if alg.parameterAsBool(parameters, sat_param_name(s), context)]
    if not sats:
        from qgis.core import QgsProcessingException
        raise QgsProcessingException("Tick at least one satellite.")
    return sats


def add_basemap_parameter(alg, name="ADD_BASEMAP"):
    from qgis.core import QgsProcessingParameterEnum
    from ..core.basemaps import ADD_CHOICES
    alg.addParameter(QgsProcessingParameterEnum(name, "Also add this basemap to my QGIS map", options=ADD_CHOICES,
                                                defaultValue=ADD_CHOICES.index("Esri World Imagery")))


def add_selected_basemap(alg, parameters, context, feedback=None, name="ADD_BASEMAP"):
    """Call from postProcessAlgorithm (main thread)."""
    from ..core.basemaps import ADD_CHOICES, add_basemap_to_project
    idx = alg.parameterAsEnum(parameters, name, context)
    if idx and idx < len(ADD_CHOICES):
        lyr = add_basemap_to_project(ADD_CHOICES[idx], context.project())
        if feedback is not None:
            feedback.pushInfo(f"Basemap added: {ADD_CHOICES[idx]}" if lyr else f"Could not add {ADD_CHOICES[idx]}")


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


class ProductLayerStyler(QgsProcessingLayerPostProcessorInterface):
    _instances = []

    @classmethod
    def create(cls):
        inst = cls()
        cls._instances.append(inst)
        return inst

    def postProcessLayer(self, layer, context, feedback):
        style_product_layer(layer)


def fmt_local_stamp(t_unix: float, tz_name: str = None) -> str:
    """'26 Oct 2026 18:41 AEDT (07:41 UTC)' in the ROI zone, or the computer's zone if none given."""
    import time as _t
    utc = _t.strftime("%H:%M UTC", _t.gmtime(t_unix))
    if tz_name:
        from ..core.timezones import to_local
        loc = to_local(t_unix, tz_name)
        return f"{loc:%d %b %Y %H:%M} {loc.tzname()} ({utc})"
    return _t.strftime("%d %b %Y %H:%M", _t.localtime(t_unix)) + f" local ({utc})"


def style_product_layer(layer):
    cats = []
    for name, meta in SATELLITES.items():
        c = QColor(meta["colour"])
        sym = QgsFillSymbol.createSimple({"color": "0,0,0,0", "outline_color": f"{c.red()},{c.green()},{c.blue()},255",
                                          "outline_width": "0.6", "outline_style": "dash"})
        cats.append(QgsRendererCategory(name, sym, name.title()))
    layer.setRenderer(QgsCategorizedSymbolRenderer("satellite", cats))
    pal = QgsPalLayerSettings()
    pal.fieldName = "product"
    fmt = QgsTextFormat()
    fmt.setSize(8)
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(0.8)
    buf.setColor(QColor("white"))
    fmt.setBuffer(buf)
    pal.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    layer.setLabelsEnabled(True)
    layer.triggerRepaint()


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
                     basemap="Esri World Imagery", feedback=None) -> dict:
    """CSV + live-tracker HTML + one field-sheet PDF (sheet, timeline, how-to-read pages) for one ROI."""
    from ..core.records import write_csv
    from ..exports.html_tracker import export_tracker_html
    from ..exports.map_png import export_field_sheet_png
    from ..exports.timeline_png import export_timeline_png
    from ..exports._paint import PdfTarget
    from ..exports.field_guide import render_field_guide

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
    def build_pdf(p):                           # vector text: page 1 sheet, page 2 timeline, then how to read it
        pdf = PdfTarget(p)
        try:
            export_field_sheet_png(None, passes, roi_geom, roi_name, tz, basemap=basemap, tles=tles, target=pdf)
            export_timeline_png(None, passes, roi_name, tz, start_unix, end_unix, tles, target=pdf)
            render_field_guide(passes, roi_name, tz, target=pdf)
        finally:
            pdf.close()
    step("Field sheet, all pages (PDF)", build_pdf, out / f"field_sheet_{stem}.pdf")
    return files


__all__ = ["data_dir", "tle_dir", "plan_dir", "iter_rois", "resolve_tz", "satellites_from_enum",
           "PassLayerStyler", "style_pass_layer", "write_field_pack", "enrich_local", "WGS84"]
