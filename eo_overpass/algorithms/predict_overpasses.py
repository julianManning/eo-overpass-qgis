"""Processing algorithm: predict Landsat / Sentinel-2 overpasses of ROI features."""

import time

from qgis.core import (QgsProcessingAlgorithm, QgsProcessingException, QgsProcessingParameterBoolean,
                       QgsProcessingParameterDateTime, QgsProcessingParameterEnum, QgsProcessingParameterFeatureSink,
                       QgsProcessingParameterFeatureSource, QgsProcessingParameterField,
                       QgsProcessingParameterFolderDestination, QgsProcessingParameterNumber,
                       QgsProcessingParameterString)

from ..core.catalogue import SATELLITE_NAMES
from ..core.orbit import build_orbits
from ..core.predict import predict_overpasses
from ..core.records import enrich_local, pass_fields, pass_to_feature
from ..core.tle import fetch_tles
from ..exports.map_png import BASEMAPS
from . import _compat as C
from .common import (WGS84, PassLayerStyler, iter_rois, plan_dir, resolve_tz, satellites_from_enum, tle_dir,
                     write_field_pack)


class PredictOverpassesAlgorithm(QgsProcessingAlgorithm):
    INPUT, NAME_FIELD, DISSOLVE = "INPUT", "NAME_FIELD", "DISSOLVE"
    START, DAYS, SATS = "START", "DAYS", "SATELLITES"
    DAYTIME, MIN_SUN, MIN_COV, TZ = "DAYTIME_DESCENDING_ONLY", "MIN_SUN_ELEVATION", "MIN_COVERAGE", "TIMEZONE"
    ESA, TLE_AGE = "CROSSCHECK_ESA", "TLE_MAX_AGE_HOURS"
    OUTPUT, PACK, BASEMAP = "OUTPUT", "FIELD_PACK", "BASEMAP"

    def name(self):
        return "predict_overpasses"

    def displayName(self):
        return "Predict overpasses"

    def group(self):
        return ""

    def groupId(self):
        return ""

    def shortHelpString(self):
        return (
            "<p>Predicts when <b>Landsat 8/9</b> and <b>Sentinel-2A/B/C</b> will image each ROI feature: the "
            "instrument swath (MSI 290 km, OLI 185 km) is built with WGS84 geodesics from SGP4 orbits and intersected "
            "with the ROI in an ROI-centred equal-area projection.</p>"
            "<p><b>Outputs</b>: one swath polygon per pass with ROI coverage %, entry/exit and scene-centre times "
            "(UTC fields as date-times; local date/time in the ROI's IANA time zone, DST-correct), view angle, "
            "sun elevation, edge margin and TLE age. The layer is styled by satellite and time-enabled for the "
            "Temporal Controller.</p>"
            "<p><b>Field pack</b> (optional folder): per ROI a CSV report, a standalone live-tracker web page, a "
            "pass-timeline PNG, a printable A4 field sheet PNG and an orbit-globe PNG.</p>"
            "<p><b>Start time</b> is taken in your computer's time zone; results are stored in UTC.</p>"
            "<p>⚠ Geometry shows the swath <i>can</i> image the site; acquisition is not guaranteed. Enable the ESA "
            "cross-check for Sentinel-2 (Sentinel-2A images selected regions only until 31 Dec 2026). Timing is to "
            "~1 s with fresh TLEs — re-run 1–2 days before field work. Cloud cover is not forecast.</p>")

    def createInstance(self):
        return PredictOverpassesAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, "Region(s) of interest", [C.src_type("VectorPolygon"), C.src_type("VectorPoint")]))
        self.addParameter(QgsProcessingParameterField(
            self.NAME_FIELD, "ROI name field", parentLayerParameterName=self.INPUT, optional=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.DISSOLVE, "Treat all features as one ROI", defaultValue=False))
        self.addParameter(QgsProcessingParameterDateTime(
            self.START, "Start (your computer's time zone; default now)", optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.DAYS, "Window length (days)", C.num_type("Double"), defaultValue=16, minValue=0.1, maxValue=120))
        self.addParameter(QgsProcessingParameterEnum(
            self.SATS, "Satellites", options=SATELLITE_NAMES, allowMultiple=True,
            defaultValue=list(range(len(SATELLITE_NAMES)))))
        self.addParameter(QgsProcessingParameterBoolean(
            self.DAYTIME, "Only passes where imaging is expected (daylit, descending)", defaultValue=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_SUN, "Minimum sun elevation (°)", C.num_type("Double"), defaultValue=0.0, minValue=-90, maxValue=90))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_COV, "Minimum ROI coverage (%)", C.num_type("Double"), defaultValue=0.0, minValue=0, maxValue=100))
        self.addParameter(QgsProcessingParameterString(
            self.TZ, "Time-zone override (IANA, e.g. Australia/Melbourne; blank = from ROI)", optional=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.ESA, "Cross-check Sentinel-2 passes against ESA acquisition plans", defaultValue=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.TLE_AGE, "Re-download TLEs older than (hours)", C.num_type("Double"), defaultValue=12, minValue=0))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, "Overpasses"))
        self.addParameter(QgsProcessingParameterFolderDestination(
            self.PACK, "Field pack folder (CSV, HTML tracker, PNGs)", optional=True, createByDefault=False))
        self.addParameter(QgsProcessingParameterEnum(
            self.BASEMAP, "Field-sheet basemap", options=list(BASEMAPS), defaultValue=1))

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException("Invalid ROI layer.")
        name_field = self.parameterAsString(parameters, self.NAME_FIELD, context)
        dissolve = self.parameterAsBool(parameters, self.DISSOLVE, context)
        qdt = self.parameterAsDateTime(parameters, self.START, context)
        start = (qdt.toMSecsSinceEpoch() / 1000.0) if qdt and qdt.isValid() else float(int(time.time() // 60 * 60))
        days = self.parameterAsDouble(parameters, self.DAYS, context)
        sats = satellites_from_enum(self.parameterAsEnums(parameters, self.SATS, context))
        daytime = self.parameterAsBool(parameters, self.DAYTIME, context)
        min_sun = self.parameterAsDouble(parameters, self.MIN_SUN, context)
        min_cov = self.parameterAsDouble(parameters, self.MIN_COV, context)
        tz_override = self.parameterAsString(parameters, self.TZ, context)
        do_esa = self.parameterAsBool(parameters, self.ESA, context)
        max_age = self.parameterAsDouble(parameters, self.TLE_AGE, context)
        pack = self.parameterAsString(parameters, self.PACK, context) if parameters.get(self.PACK) else ""
        basemap = list(BASEMAPS)[self.parameterAsEnum(parameters, self.BASEMAP, context)]

        feedback.pushInfo("Orbital elements (TLE):")
        try:
            tles = fetch_tles(tle_dir(), sats, max_age_hours=max_age, log=lambda m: feedback.pushInfo("  " + m))
        except Exception as exc:
            raise QgsProcessingException(str(exc))
        orbits = build_orbits(tles)

        fields = pass_fields()
        sink, dest_id = self.parameterAsSink(parameters, self.OUTPUT, context, fields, C.wkb("Polygon"), WGS84)
        if sink is None:
            raise QgsProcessingException("Could not create the output layer.")

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
        total = 0
        for k, (roi_name, geom) in enumerate(rois):
            if feedback.isCanceled():
                break
            tz = resolve_tz(geom, tz_override, feedback)
            feedback.pushInfo(f"\nROI '{roi_name}' — time zone {tz}")
            sub = _SubFeedback(feedback, k, len(rois))
            passes = predict_overpasses(geom, orbits, tles, start, days, sats, tz, roi_name, daytime, min_sun,
                                        min_cov, feedback=sub)
            passes = [enrich_local(p) for p in passes]
            if plans is not None:
                from ..core.plans import crosscheck
                crosscheck(passes, plans, geom)
            for ps in passes:
                sink.addFeature(pass_to_feature(ps, fields), C.fast_insert())
                feedback.pushInfo(f"  {ps['satellite']:<12} {ps['local_date']} {ps['local_time']} {ps['utc_offset']}  "
                                  f"{ps['roi_coverage_pct']:5.1f}% ROI  {ps.get('esa_plan_status') or ''}")
            total += len(passes)
            if pack:
                feedback.pushInfo(f"  Field pack for '{roi_name}':")
                write_field_pack(pack, roi_name, geom, passes, tles, orbits, tz, start, start + days * 86400,
                                 basemap, feedback)
        feedback.pushInfo(f"\n{total} overpass(es) predicted.")
        if context.willLoadLayerOnCompletion(dest_id):
            context.layerToLoadOnCompletionDetails(dest_id).setPostProcessor(PassLayerStyler.create())
        out = {self.OUTPUT: dest_id}
        if pack:
            out[self.PACK] = pack
        return out


class _SubFeedback:
    """Maps per-ROI progress onto the overall progress bar."""

    def __init__(self, fb, k, n):
        self.fb, self.k, self.n = fb, k, n

    def isCanceled(self):
        return self.fb.isCanceled()

    def setProgress(self, pct):
        self.fb.setProgress((self.k + pct / 100.0) / self.n * 100.0)
