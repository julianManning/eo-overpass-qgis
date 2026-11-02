"""The ROI most recently given to Predict overpasses, so the live tracker can use it without a map layer."""

from qgis.core import QgsGeometry, QgsSettings

_KEY = "eo_overpass/last_roi"
_mem = None


def save(name: str, geom: QgsGeometry) -> None:
    """Remember (name, EPSG:4326 geometry); also kept in QGIS settings so it survives a restart."""
    global _mem
    wkt = geom.asWkt(6)
    _mem = (name, wkt)
    s = QgsSettings()
    s.setValue(f"{_KEY}/name", name)
    s.setValue(f"{_KEY}/wkt", wkt if len(wkt) < 2_000_000 else "")


def load():
    """(name, geometry) or None."""
    name, wkt = _mem if _mem else (QgsSettings().value(f"{_KEY}/name", ""), QgsSettings().value(f"{_KEY}/wkt", ""))
    g = QgsGeometry.fromWkt(wkt) if wkt else None
    return (name, g) if g is not None and not g.isNull() else None
