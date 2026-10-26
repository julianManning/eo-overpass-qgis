"""Processing algorithms: refresh TLEs, ESA plan cross-check, export field pack."""

from collections import defaultdict

from qgis.core import (QgsProcessingAlgorithm, QgsProcessingException,
                       QgsProcessingOutputString, QgsProcessingParameterBoolean, QgsProcessingParameterEnum,
                       QgsProcessingParameterFeatureSink, QgsProcessingParameterFeatureSource,
                       QgsProcessingParameterField, QgsProcessingParameterFile,
                       QgsProcessingParameterFolderDestination, QgsProcessingParameterString)

from ..core.catalogue import SATELLITE_NAMES
from ..core.orbit import build_orbits
from ..core.records import feature_to_pass, pass_fields, pass_to_feature
from ..core.tle import fetch_tles
from ..exports.map_png import BASEMAPS
from . import _compat as C
from .common import (WGS84, PassLayerStyler, iter_rois, plan_dir, resolve_tz, satellites_from_enum, tle_dir,
                     write_field_pack)


class _Base(QgsProcessingAlgorithm):
    def group(self):
        return ""

    def groupId(self):
        return ""

    def createInstance(self):
        return type(self)()


def _passes_by_roi(source):
    by = defaultdict(list)
    for f in source.getFeatures():
        p = feature_to_pass(f)
        by[p.get("roi_name") or "ROI"].append(p)
    for v in by.values():
        v.sort(key=lambda p: p["overpass_utc"] or 0)
    return by


# ----------------------------------------------------------------------------------------------------------
class RefreshTlesAlgorithm(_Base):
    SATS, TEXT, FORCE, REPORT = "SATELLITES", "TLE_TEXT", "FORCE", "REPORT"

    def name(self):
        return "refresh_tles"

    def displayName(self):
        return "Refresh orbital elements (TLEs)"

    def shortHelpString(self):
        return ("<p>Downloads current TLEs from CelesTrak (mirror fallback) into the plugin cache. Paste your own "
                "TLE text (e.g. from Space-Track or n2yo) to override; collapsed column spacing is repaired and "
                "checksums are verified.</p><p>⚠ TLEs older than ~7 days reduce timing accuracy. CelesTrak asks "
                "users not to poll more often than every ~2 hours.</p>")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterEnum(self.SATS, "Satellites", options=SATELLITE_NAMES,
                                                     allowMultiple=True, defaultValue=list(range(len(SATELLITE_NAMES)))))
        self.addParameter(QgsProcessingParameterString(self.TEXT, "Manual TLE text (optional)", multiLine=True,
                                                       optional=True))
        self.addParameter(QgsProcessingParameterBoolean(self.FORCE, "Force download (ignore cache)", defaultValue=False))
        self.addOutput(QgsProcessingOutputString(self.REPORT, "Report"))

    def processAlgorithm(self, parameters, context, feedback):
        sats = satellites_from_enum(self.parameterAsEnums(parameters, self.SATS, context))
        text = self.parameterAsString(parameters, self.TEXT, context)
        force = self.parameterAsBool(parameters, self.FORCE, context)
        lines = []
        try:
            fetch_tles(tle_dir(), sats, max_age_hours=0 if force else 12, tle_text=text,
                       log=lambda m: (lines.append(m), feedback.pushInfo(m)))
        except Exception as exc:
            raise QgsProcessingException(str(exc))
        return {self.REPORT: "\n".join(lines)}


# ----------------------------------------------------------------------------------------------------------
class CrossCheckPlansAlgorithm(_Base):
    INPUT, ROIS, NAME_FIELD, DISSOLVE, KMLS, OUTPUT = "INPUT", "ROIS", "NAME_FIELD", "DISSOLVE", "KML_FOLDER", "OUTPUT"

    def name(self):
        return "crosscheck_s2_plans"

    def displayName(self):
        return "Cross-check Sentinel-2 acquisition plans"

    def shortHelpString(self):
        return ("<p>Checks each Sentinel-2 pass in an <i>Overpasses</i> layer against ESA's published acquisition "
                "plans and sets <code>esa_plan_status</code>: <b>planned (NOMINAL)</b>, <b>not planned</b>, "
                "<b>no plan loaded</b> (beyond the ~2-week planning horizon or a missing file), or <b>n/a</b> "
                "(Landsat).</p><p>⚠ <i>no plan loaded</i> means unknown, not negative. ESA plan footprints are "
                "simplified; a <i>not planned</i> edge pass can be a footprint artefact. Re-run close to the date — "
                "ESA revises plans.</p><p>Use the same ROI layer and name field as the prediction.</p>")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(self.INPUT, "Overpasses layer", [C.src_type("VectorPolygon")]))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.ROIS, "Region(s) of interest", [C.src_type("VectorPolygon"), C.src_type("VectorPoint")]))
        self.addParameter(QgsProcessingParameterField(self.NAME_FIELD, "ROI name field", parentLayerParameterName=self.ROIS,
                                                      optional=True))
        self.addParameter(QgsProcessingParameterBoolean(self.DISSOLVE, "Treat all ROI features as one ROI", defaultValue=False))
        self.addParameter(QgsProcessingParameterFile(self.KMLS, "Folder of plan KMLs (blank = download)",
                                                     behavior=_folder_behaviour(), optional=True))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, "Overpasses (plan-checked)"))

    def processAlgorithm(self, parameters, context, feedback):
        from ..core.plans import crosscheck, fetch_plans, load_plans
        src = self.parameterAsSource(parameters, self.INPUT, context)
        rois = dict(iter_rois(self.parameterAsSource(parameters, self.ROIS, context),
                              self.parameterAsString(parameters, self.NAME_FIELD, context),
                              self.parameterAsBool(parameters, self.DISSOLVE, context), context))
        by = _passes_by_roi(src)
        folder = self.parameterAsFile(parameters, self.KMLS, context) or str(plan_dir())
        all_t = [p["overpass_utc"] for v in by.values() for p in v if p["overpass_utc"]]
        if not self.parameterAsFile(parameters, self.KMLS, context) and all_t:
            try:
                fetch_plans(folder, min(all_t) - 3600, max(all_t) + 3600, log=feedback.pushInfo)
            except Exception as exc:
                feedback.pushWarning(f"Plan download failed ({exc}); using cached KMLs.")
        plans = load_plans(folder)
        feedback.pushInfo(f"{len(plans)} planned data-takes loaded.")
        fields = pass_fields()
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields, C.wkb("Polygon"), WGS84)
        for name, passes in by.items():
            geom = rois.get(name)
            if geom is None:
                feedback.pushWarning(f"No ROI feature named '{name}' — its passes are copied unchecked.")
            else:
                crosscheck(passes, plans, geom)
            for p in passes:
                sink.addFeature(pass_to_feature(p, fields), C.fast_insert())
                feedback.pushInfo(f"{name}: {p['satellite']} {p['local_date']} {p['local_time']} → "
                                  f"{p.get('esa_plan_status') or 'unchecked'}")
        if context.willLoadLayerOnCompletion(dest):
            context.layerToLoadOnCompletionDetails(dest).setPostProcessor(PassLayerStyler.create())
        return {self.OUTPUT: dest}


# ----------------------------------------------------------------------------------------------------------
class ExportFieldPackAlgorithm(_Base):
    INPUT, ROIS, NAME_FIELD, DISSOLVE, PACK, BASEMAP = "INPUT", "ROIS", "NAME_FIELD", "DISSOLVE", "FIELD_PACK", "BASEMAP"

    def name(self):
        return "export_field_pack"

    def displayName(self):
        return "Export field pack (CSV, HTML tracker, PNGs)"

    def shortHelpString(self):
        return ("<p>Writes, for each ROI in an <i>Overpasses</i> layer: a CSV report (UTC and local times), a "
                "standalone live-tracker web page, a pass-timeline PNG, a printable A4 field sheet PNG (map of the "
                "next passes + table + cautions) and an orbit-globe PNG.</p><p>Uses the cached TLEs from the last "
                "prediction. ⚠ Outputs are a snapshot — regenerate them 1–2 days before field work.</p>")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(self.INPUT, "Overpasses layer", [C.src_type("VectorPolygon")]))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.ROIS, "Region(s) of interest", [C.src_type("VectorPolygon"), C.src_type("VectorPoint")]))
        self.addParameter(QgsProcessingParameterField(self.NAME_FIELD, "ROI name field", parentLayerParameterName=self.ROIS,
                                                      optional=True))
        self.addParameter(QgsProcessingParameterBoolean(self.DISSOLVE, "Treat all ROI features as one ROI", defaultValue=False))
        self.addParameter(QgsProcessingParameterFolderDestination(self.PACK, "Field pack folder"))
        self.addParameter(QgsProcessingParameterEnum(self.BASEMAP, "Field-sheet basemap", options=list(BASEMAPS), defaultValue=1))

    def processAlgorithm(self, parameters, context, feedback):
        src = self.parameterAsSource(parameters, self.INPUT, context)
        rois = dict(iter_rois(self.parameterAsSource(parameters, self.ROIS, context),
                              self.parameterAsString(parameters, self.NAME_FIELD, context),
                              self.parameterAsBool(parameters, self.DISSOLVE, context), context))
        folder = self.parameterAsString(parameters, self.PACK, context)
        basemap = list(BASEMAPS)[self.parameterAsEnum(parameters, self.BASEMAP, context)]
        by = _passes_by_roi(src)
        try:
            tles = fetch_tles(tle_dir(), max_age_hours=1e9, log=lambda m: None)
            orbits = build_orbits(tles)
        except Exception as exc:
            feedback.pushWarning(f"No cached TLEs ({exc}); the HTML tracker and globe PNG are skipped.")
            tles, orbits = {}, {}
        for name, passes in by.items():
            geom = rois.get(name)
            if geom is None:
                feedback.pushWarning(f"No ROI feature named '{name}' — skipped.")
                continue
            tz = passes[0].get("timezone") or resolve_tz(geom, "", feedback)
            t = [p["overpass_utc"] for p in passes]
            feedback.pushInfo(f"Field pack for '{name}':")
            if not tles:
                from ..core.records import write_csv
                from ..exports.timeline_png import export_timeline_png
                from .common import safe_name
                from pathlib import Path
                out = Path(folder) / safe_name(name)
                out.mkdir(parents=True, exist_ok=True)
                write_csv(out / f"overpasses_{safe_name(name)}.csv", passes)
                export_timeline_png(out / f"timeline_{safe_name(name)}.png", passes, name, tz, min(t), max(t))
            else:
                # passes rebuilt from a layer have no track coordinates; the field sheet still renders swaths
                write_field_pack(folder, name, geom, passes, tles, orbits, tz, min(t), max(t), basemap, feedback)
        return {self.PACK: folder}


def _folder_behaviour():
    from qgis.core import Qgis, QgsProcessingParameterFile
    b = getattr(Qgis, "ProcessingFileParameterBehavior", None)
    return b.Folder if b is not None and hasattr(b, "Folder") else QgsProcessingParameterFile.Folder
