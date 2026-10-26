"""Pass records ↔ QGIS fields/features ↔ CSV (one definition shared by every output)."""

import csv
from datetime import datetime, timezone

from qgis.core import QgsFeature, QgsField, QgsFields
from qgis.PyQt.QtCore import QDateTime, Qt

from .timezones import to_local, utc_offset_str

# (name, kind) — kinds: str, int, float, bool, dt
FIELD_SPEC = [
    ("roi_name", "str"), ("satellite", "str"), ("mission", "str"), ("sensor", "str"), ("norad_id", "int"),
    ("direction", "str"), ("imaging_expected", "bool"), ("roi_coverage_pct", "float"),
    ("roi_entry_utc", "dt"), ("overpass_utc", "dt"), ("roi_exit_utc", "dt"), ("duration_s", "float"),
    ("overpass_local", "str"), ("local_date", "str"), ("local_time", "str"), ("local_weekday", "str"),
    ("timezone", "str"), ("utc_offset", "str"),
    ("nadir_offset_km", "float"), ("roi_side_of_track", "str"), ("edge_margin_km", "float"),
    ("view_angle_deg", "float"), ("max_elevation_deg", "float"), ("sun_elevation_deg", "float"),
    ("sat_altitude_km", "float"), ("tle_epoch_utc", "dt"), ("tle_age_days", "float"),
    ("esa_plan_status", "str"), ("esa_datatake_id", "str"), ("esa_relative_orbit", "int"), ("note", "str"),
]
_TIME_FIELDS = {"roi_entry_utc", "overpass_utc", "roi_exit_utc", "tle_epoch_utc"}


def _qfield(name, kind):
    try:
        from qgis.PyQt.QtCore import QMetaType
        t = {"str": QMetaType.Type.QString, "int": QMetaType.Type.Int, "float": QMetaType.Type.Double,
             "bool": QMetaType.Type.Bool, "dt": QMetaType.Type.QDateTime}[kind]
        return QgsField(name, t)
    except (TypeError, AttributeError):
        from qgis.PyQt.QtCore import QVariant
        t = {"str": QVariant.String, "int": QVariant.Int, "float": QVariant.Double,
             "bool": QVariant.Bool, "dt": QVariant.DateTime}[kind]
        return QgsField(name, t)


def pass_fields() -> QgsFields:
    fields = QgsFields()
    for name, kind in FIELD_SPEC:
        fields.append(_qfield(name, kind))
    return fields


def _utc_qdt(t_unix: float) -> QDateTime:
    from .compat import enum
    return QDateTime.fromMSecsSinceEpoch(int(round(t_unix * 1000)), enum(Qt, "TimeSpec", "UTC"))


def enrich_local(p: dict) -> dict:
    """Add local-time string fields for the ROI time zone (DST-correct per pass)."""
    loc = to_local(p["overpass_utc"], p["timezone"])
    p["overpass_local"] = loc.isoformat(timespec="seconds")
    p["local_date"] = loc.strftime("%Y-%m-%d")
    p["local_time"] = loc.strftime("%H:%M:%S")
    p["local_weekday"] = loc.strftime("%A")
    p["utc_offset"] = utc_offset_str(loc)
    return p


def pass_to_feature(p: dict, fields: QgsFields) -> QgsFeature:
    f = QgsFeature(fields)
    for name, kind in FIELD_SPEC:
        v = p.get(name)
        if v is None or v == "" and kind != "str":
            f.setAttribute(name, None)
        elif kind == "dt":
            f.setAttribute(name, _utc_qdt(v))
        else:
            f.setAttribute(name, v)
    f.setGeometry(p["geometry"])
    return f


def feature_to_pass(f: QgsFeature) -> dict:
    """Rebuild a pass dict from a feature produced by this plugin (used by follow-on tools)."""
    p = {}
    for name, kind in FIELD_SPEC:
        idx = f.fields().indexOf(name)
        v = f.attribute(idx) if idx >= 0 else None
        if hasattr(v, "isNull") and v.isNull():
            v = None
        if kind == "dt" and v is not None:
            v = v.toMSecsSinceEpoch() / 1000.0 if isinstance(v, QDateTime) else v
        p[name] = v
    p["geometry"] = f.geometry()
    return p


def _iso_utc(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t is not None else ""


def write_csv(path, passes: list):
    """CSV with ISO-8601 UTC ('Z') and local (explicit offset) times, one row per pass."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([n for n, _ in FIELD_SPEC] + ["roi_entry_local", "roi_exit_local"])
        for p in passes:
            row = []
            for name, kind in FIELD_SPEC:
                v = p.get(name)
                row.append(_iso_utc(v) if name in _TIME_FIELDS else ("" if v is None else v))
            tz = p["timezone"]
            row += [to_local(p["roi_entry_utc"], tz).isoformat(timespec="seconds"),
                    to_local(p["roi_exit_utc"], tz).isoformat(timespec="seconds")]
            w.writerow(row)
    return path
