"""Processing algorithm: predict Landsat / Sentinel-2 overpasses of ROI features (one tool does it all)."""

import time
from pathlib import Path

from qgis.core import (QgsGeometry, QgsProcessingAlgorithm, QgsProcessingException, QgsProcessingParameterBoolean,
                       QgsProcessingParameterDateTime, QgsProcessingParameterEnum, QgsProcessingParameterFeatureSink,
                       QgsProcessingParameterFeatureSource, QgsProcessingParameterField,
                       QgsProcessingParameterFolderDestination, QgsProcessingParameterNumber,
                       QgsProcessingParameterString)

from ..core.orbit import build_orbits
from ..core.predict import predict_overpasses
from ..core.products import attribute_products
from ..core.records import enrich_local, pass_fields, pass_to_feature, product_fields, product_to_feature
from ..core.tle import fetch_tles
from ..exports.map_png import BASEMAPS
from . import _compat as C
from .common import (WGS84, PassLayerStyler, ProductLayerStyler, add_basemap_parameter, add_satellite_parameters,
                     add_selected_basemap, fmt_local_stamp, iter_rois, plan_dir, resolve_tz,
                     satellites_from_parameters, tle_dir, write_field_pack)

GUIDE_URL = "https://github.com/julianManning/eo-overpass-qgis/blob/main/docs/EO_Overpass_Predictor_User_Guide.pdf"


def guide_url() -> str:
    """The user guide bundled with the plugin (works offline), else the copy on GitHub."""
    local = Path(__file__).resolve().parents[1] / "docs" / "user_guide.pdf"
    return local.as_uri() if local.exists() else GUIDE_URL

HELP = (
    "<p style='color:#664d03;background:#fff3cd;padding:4px'>⚠ <b>Results expire.</b> Every output is stamped with "
    "the time it was produced and a <b>re-run-by</b> date (default 3 days later). Orbits drift and ESA revises "
    "plans: re-run 1–2 days before field work. Geometry shows the swath <i>can</i> image the site — acquisition is "
    "not guaranteed (Sentinel-2A is in its extension phase and images selected regions only). Cloud cover is not forecast.</p>"
    "<p>Predicts when <b>Landsat 8/9</b> and <b>Sentinel-2A/B/C</b> will image each ROI feature. The instrument "
    "swath (MSI 290 km, OLI 185 km) is built with WGS84 geodesics from SGP4 orbits and intersected with the ROI in "
    "an ROI-centred equal-area projection.</p>"
    "<p><b>Outputs</b></p><ul>"
    "<li><b>Overpasses</b>: one swath strip per pass — ROI coverage %, entry/exit and scene-centre times (UTC, plus "
    "local date/time in the ROI's own time zone, DST-correct), view angle, sun elevation, edge margin, Sentinel-2 "
    "relative orbit or Landsat WRS-2 path, the products to download and the ESA plan status. Styled by satellite and "
    "time-enabled for the Temporal Controller.</li>"
    "<li><b>Products</b>: footprints of the Sentinel-2 MGRS tiles and Landsat WRS-2 scenes each pass delivers over "
    "the ROI.</li>"
    "<li><b>Field pack</b> (optional folder), per ROI: CSV, standalone live-tracker web page and one printable A4 "
    "<b>field sheet PDF</b> — map and pass table, the pass timeline, then pages explaining every symbol and column. Optional: choose a folder to create it; the field-sheet map and the optional QGIS basemap default to Esri World Imagery.</li>"
    "<li><b>Live tracker</b> (opens by itself after a run with the ROI you gave Predict; press ▶ Live to animate): satellites, tracks, swaths and night side on your map, "
    "plus the next 14 days of passes in UTC and local time with direction, view angle, ESA plan status and TLE age.</li></ul>"
    "<p>Sentinel-2 passes are cross-checked against ESA's published acquisition plans in the same run "
    "(planned / not planned / no plan loaded). Orbital elements (TLEs) are downloaded automatically and cached; "
    "paste your own under <i>Advanced</i>.</p>"
    "<p><b>Start</b> is taken in your computer's time zone; results are stored in UTC.</p>"
    "<p>Full user guide (PDF): press <b>Help</b> below, or <i>Plugins ▸ EO Overpass ▸ Help (user guide)</i>.</p>")


class PredictOverpassesAlgorithm(QgsProcessingAlgorithm):
    INPUT, NAME_FIELD, DISSOLVE = "INPUT", "NAME_FIELD", "DISSOLVE"
    START, DAYS = "START", "DAYS"
    DAYTIME, MIN_SUN, MIN_COV, TZ = "DAYTIME_DESCENDING_ONLY", "MIN_SUN_ELEVATION", "MIN_COVERAGE", "TIMEZONE"
    ESA, TLE_AGE, TLE_TEXT = "CROSSCHECK_ESA", "TLE_MAX_AGE_HOURS", "TLE_TEXT"
    STRIP, VALID = "STRIP_LENGTH_KM", "VALID_DAYS"
    OUTPUT, PRODUCTS, PACK, BASEMAP = "OUTPUT", "PRODUCTS", "FIELD_PACK", "BASEMAP"

    def name(self):
        return "predict_overpasses"

    def displayName(self):
        return "Predict overpasses"

    def group(self):
        return ""

    def groupId(self):
        return ""

    def shortHelpString(self):
        return HELP

    def helpUrl(self):
        return guide_url()

    def createInstance(self):
        return PredictOverpassesAlgorithm()

    def _advanced(self, param):
        try:
            from qgis.core import Qgis
            flag = Qgis.ProcessingParameterFlag.Advanced
        except AttributeError:
            from qgis.core import QgsProcessingParameterDefinition
            flag = QgsProcessingParameterDefinition.FlagAdvanced
        param.setFlags(param.flags() | flag)
        self.addParameter(param)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, "Region(s) of interest (ROI)", [C.src_type("VectorPolygon"), C.src_type("VectorPoint")]))
        self.addParameter(QgsProcessingParameterField(
            self.NAME_FIELD, "ROI name field (if empty, a name or site field is used)", parentLayerParameterName=self.INPUT, optional=True))
        self.addParameter(QgsProcessingParameterBoolean(self.DISSOLVE, "Treat all features as one ROI", defaultValue=False))
        self.addParameter(QgsProcessingParameterDateTime(
            self.START, "Start (your computer's time zone; default now)", optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.DAYS, "Window length (days)", C.num_type("Double"), defaultValue=16, minValue=0.1, maxValue=120))
        add_satellite_parameters(self)
        self.addParameter(QgsProcessingParameterBoolean(
            self.DAYTIME, "Only passes where imaging is expected (daylit, descending)", defaultValue=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_COV, "Minimum ROI coverage (%)", C.num_type("Double"), defaultValue=0.0, minValue=0, maxValue=100))
        self.addParameter(QgsProcessingParameterBoolean(
            self.ESA, "Cross-check Sentinel-2 passes against ESA acquisition plans", defaultValue=True))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, "Overpasses"))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.PRODUCTS, "Products (Sentinel-2 tiles / Landsat path-rows)", optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterFolderDestination(
            self.PACK, "Save field pack to (field sheet PDF/PNGs, CSV, web tracker)",
            optional=True, createByDefault=False))
        self.addParameter(QgsProcessingParameterEnum(
            self.BASEMAP, "Field sheet background map", options=list(BASEMAPS), defaultValue=list(BASEMAPS).index("Esri World Imagery")))
        add_basemap_parameter(self)
        # ---- advanced
        self._advanced(QgsProcessingParameterNumber(
            self.VALID, "Re-run-by stamp: results valid for (days)", C.num_type("Double"), defaultValue=3,
            minValue=0.5, maxValue=30))
        self._advanced(QgsProcessingParameterNumber(
            self.STRIP, "Swath strip length drawn (km)", C.num_type("Double"), defaultValue=350, minValue=50,
            maxValue=5000))
        self._advanced(QgsProcessingParameterNumber(
            self.MIN_SUN, "Minimum sun elevation (°)", C.num_type("Double"), defaultValue=0.0, minValue=-90, maxValue=90))
        self._advanced(QgsProcessingParameterString(
            self.TZ, "Time-zone override (IANA, e.g. Australia/Melbourne; blank = from ROI)", optional=True))
        self._advanced(QgsProcessingParameterNumber(
            self.TLE_AGE, "Re-download TLEs older than (hours)", C.num_type("Double"), defaultValue=12, minValue=0))
        self._advanced(QgsProcessingParameterString(
            self.TLE_TEXT, "Manual TLE text (overrides download for those satellites)", multiLine=True, optional=True))

    def prepareAlgorithm(self, parameters, context, feedback):
        self._params = parameters
        return True

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException("Invalid ROI layer.")
        name_field = self.parameterAsString(parameters, self.NAME_FIELD, context)
        dissolve = self.parameterAsBool(parameters, self.DISSOLVE, context)
        qdt = self.parameterAsDateTime(parameters, self.START, context)
        start = (qdt.toMSecsSinceEpoch() / 1000.0) if qdt and qdt.isValid() else float(int(time.time() // 60 * 60))
        days = self.parameterAsDouble(parameters, self.DAYS, context)
        sats = satellites_from_parameters(self, parameters, context)
        daytime = self.parameterAsBool(parameters, self.DAYTIME, context)
        min_sun = self.parameterAsDouble(parameters, self.MIN_SUN, context)
        min_cov = self.parameterAsDouble(parameters, self.MIN_COV, context)
        tz_override = self.parameterAsString(parameters, self.TZ, context)
        do_esa = self.parameterAsBool(parameters, self.ESA, context)
        max_age = self.parameterAsDouble(parameters, self.TLE_AGE, context)
        tle_text = self.parameterAsString(parameters, self.TLE_TEXT, context)
        strip_km = self.parameterAsDouble(parameters, self.STRIP, context)
        valid_days = self.parameterAsDouble(parameters, self.VALID, context)
        pack = self.parameterAsString(parameters, self.PACK, context) if parameters.get(self.PACK) else ""
        basemap = list(BASEMAPS)[self.parameterAsEnum(parameters, self.BASEMAP, context)]
        generated = time.time()
        valid_until = generated + valid_days * 86400.0

        feedback.pushInfo("Orbital elements (TLE):")
        try:
            tles = fetch_tles(tle_dir(), sats, max_age_hours=max_age, tle_text=tle_text,
                              log=lambda m: feedback.pushInfo("  " + m))
        except Exception as exc:
            raise QgsProcessingException(str(exc))
        orbits = build_orbits(tles)

        fields = pass_fields()
        sink, dest_id = self.parameterAsSink(parameters, self.OUTPUT, context, fields, C.wkb("Polygon"), WGS84)
        if sink is None:
            raise QgsProcessingException("Could not create the output layer.")
        pfields = product_fields()
        psink, pdest = self.parameterAsSink(parameters, self.PRODUCTS, context, pfields, C.wkb("Polygon"), WGS84)

        plans = None
        if do_esa and any(s.startswith("SENTINEL-2") for s in sats):
            from ..core.plans import fetch_plans, load_plans
            feedback.pushInfo("ESA Sentinel-2 acquisition plans:")
            try:
                fetch_plans(plan_dir(), start, start + days * 86400,
                            units=[s.replace("SENTINEL-", "S") for s in sats if s.startswith("SENTINEL-2")],
                            log=lambda m: feedback.pushInfo("  " + m))
            except Exception as exc:
                feedback.pushWarning(f"  Plan download failed ({exc}); using any KMLs already in {plan_dir()}.")
            plans = load_plans(plan_dir())
            feedback.pushInfo(f"  {len(plans)} planned data-takes loaded.")

        rois = list(iter_rois(source, name_field, dissolve, context, feedback))
        if not rois:
            raise QgsProcessingException("The ROI layer has no valid geometries.")
        from ..core import last_roi          # lets the live tracker reuse this ROI without a map layer
        last_roi.save(rois[0][0] if len(rois) == 1 else (source.sourceName() or "ROI"),
                      QgsGeometry.unaryUnion([g for _, g in rois]))
        total, first = 0, None
        for k, (roi_name, geom) in enumerate(rois):
            if feedback.isCanceled():
                break
            tz = resolve_tz(geom, tz_override, feedback)
            feedback.pushInfo(f"\nROI '{roi_name}' — time zone {tz}")
            sub = _SubFeedback(feedback, k, len(rois))
            passes = predict_overpasses(geom, orbits, tles, start, days, sats, tz, roi_name, daytime, min_sun,
                                        min_cov, strip_length_km=strip_km, feedback=sub)
            passes = [enrich_local(p) for p in passes]
            if not passes:
                tips = [f"extend the window (now {days:g} days)"]
                if daytime:
                    tips.append("untick 'Only passes where imaging is expected'")
                if min_cov > 0:
                    tips.append(f"lower 'Minimum ROI coverage' (now {min_cov:g}%)")
                if min_sun > 0:
                    tips.append(f"lower 'Minimum sun elevation' (now {min_sun:g}°)")
                feedback.pushWarning(f"  No passes for '{roi_name}'. Try to " + ", ".join(tips) + ", or tick more satellites.")
            if plans is not None:
                from ..core.plans import crosscheck
                crosscheck(passes, plans, geom)
            for i, ps in enumerate(passes, 1):
                ps["generated_utc"], ps["valid_until_utc"] = generated, valid_until
                prods = attribute_products(ps, orbits[ps["satellite"]], geom)
                if ps.get("esa_relative_orbit"):
                    ps["relative_orbit"] = ps["esa_relative_orbit"]
                sink.addFeature(pass_to_feature(ps, fields), C.fast_insert())
                if psink is not None:
                    for pr in prods:
                        psink.addFeature(product_to_feature(pr, ps, i, pfields), C.fast_insert())
                orbit_lbl = (f"R{ps['relative_orbit']:03d}" if ps["mission"] == "Sentinel-2" else
                             f"path {ps['relative_orbit']:03d}") if ps.get("relative_orbit") else ""
                feedback.pushInfo(f"  {i:>2}. {ps['satellite']:<12} {ps['local_date']} {ps['local_time']} "
                                  f"{ps['utc_offset']}  {ps['roi_coverage_pct']:5.1f}% ROI  {orbit_lbl:<9} "
                                  f"{ps.get('products') or ''}  {ps.get('esa_plan_status') or ''}")
            total += len(passes)
            if passes and (first is None or passes[0]["overpass_utc"] < first[0]["overpass_utc"]):
                first = passes
            if pack:
                feedback.pushInfo(f"  Field pack for '{roi_name}':")
                write_field_pack(pack, roi_name, geom, passes, tles, orbits, tz, start, start + days * 86400,
                                 basemap, feedback)
        feedback.pushInfo(f"\n{total} overpass(es) predicted.")
        feedback.pushWarning(
            f"⚠ Produced {fmt_local_stamp(generated)}. Re-run by {fmt_local_stamp(valid_until)} for the most accurate "
            "results — orbits drift and ESA revises plans. Acquisition is not guaranteed; cloud is not forecast.")
        if context.willLoadLayerOnCompletion(dest_id):
            context.layerToLoadOnCompletionDetails(dest_id).setPostProcessor(PassLayerStyler.create())
        if pdest and context.willLoadLayerOnCompletion(pdest):
            context.layerToLoadOnCompletionDetails(pdest).setPostProcessor(ProductLayerStyler.create())
        nxt = first[0] if first else None
        self._summary = (total, f"{nxt['satellite'].title()} {nxt['local_date']} {nxt['local_time']}" if nxt else "",
                         pack)
        out = {self.OUTPUT: dest_id}
        if pdest:
            out[self.PRODUCTS] = pdest
        if pack:
            out[self.PACK] = pack
        return out

    def postProcessAlgorithm(self, context, feedback):
        add_selected_basemap(self, self._params, context, feedback)
        self._show_summary()
        self._open_tracker()
        return {}

    def _open_tracker(self):
        """Show the live tracker dock with this run's ROI (main thread; skipped outside the QGIS GUI)."""
        try:
            from qgis.utils import iface, plugins
            plugin = plugins.get(__package__.split('.')[0]) if iface is not None else None
            if plugin is not None and hasattr(plugin, 'open_tracker'):
                plugin.open_tracker()
        except Exception:                                   # cosmetic only: never fail a finished run  # nosec B110
            pass

    def _show_summary(self):
        """Message-bar summary with a button to open the field pack folder (skipped outside the QGIS GUI)."""
        try:
            from qgis.utils import iface
            total, nxt, pack = self._summary
            if iface is None:
                return
            from qgis.PyQt.QtCore import QUrl
            from qgis.PyQt.QtGui import QDesktopServices
            from qgis.PyQt.QtWidgets import QPushButton
            msg = f"{total} overpass(es) predicted" + (f"; next: {nxt}." if nxt else ".")
            item = iface.messageBar().createMessage("EO Overpass", msg)
            if pack:
                btn = QPushButton("Open field pack folder")
                btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(pack)))
                item.layout().addWidget(btn)
            iface.messageBar().pushWidget(item, duration=0)
        except Exception:                                   # cosmetic only: never fail a finished run  # nosec B110
            pass


class _SubFeedback:
    """Maps per-ROI progress onto the overall progress bar."""

    def __init__(self, fb, k, n):
        self.fb, self.k, self.n = fb, k, n

    def isCanceled(self):
        return self.fb.isCanceled()

    def setProgress(self, pct):
        self.fb.setProgress((self.k + pct / 100.0) / self.n * 100.0)
