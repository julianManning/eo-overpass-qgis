"""Qt5/Qt6 and QGIS 3/4 compatibility helpers (scoped vs unscoped enums)."""


def enum(owner, scope: str, name: str):
    """Return owner.scope.name (Qt6 / QGIS 4 style) or owner.name (Qt5 / QGIS 3 style)."""
    scoped = getattr(owner, scope, None)
    if scoped is not None and hasattr(scoped, name):
        return getattr(scoped, name)
    return getattr(owner, name)


def is_point_geometry(geom) -> bool:
    """True for (multi)point geometries — robust to QGIS 3 int / QGIS 4 enum geometry types."""
    from qgis.core import QgsWkbTypes
    return QgsWkbTypes.geometryDisplayString(geom.type()).lower().startswith("point")
