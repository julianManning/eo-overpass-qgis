"""XYZ basemaps for field sheets and for adding to the QGIS project."""

from urllib.parse import quote

_XYZ = {
    "OpenStreetMap": ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", 19, "© OpenStreetMap contributors"),
    "Esri World Imagery": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                           19, "Esri, Maxar, Earthstar Geographics"),
}

OFFLINE = "Offline land/sea (Natural Earth)"     # bundled, no internet needed
BASEMAP_NAMES = list(_XYZ)                      # for "add to map" choices
SHEET_BASEMAPS = [OFFLINE] + BASEMAP_NAMES       # field-sheet choices (index 1 = OpenStreetMap)
ADD_CHOICES = ["Don't add"] + BASEMAP_NAMES + [OFFLINE]


def uri(name: str):
    if name not in _XYZ:
        return None
    url, zmax, _ = _XYZ[name]
    return f"type=xyz&url={quote(url, safe=':/{}')}&zmax={zmax}&zmin=0"


def attribution(name: str) -> str:
    if name == OFFLINE:
        return "Natural Earth"
    return _XYZ[name][2] if name in _XYZ else ""


def add_basemap_to_project(name: str, project=None):
    """Add an XYZ basemap at the bottom of the layer tree (skipped if already present). Main thread only."""
    from qgis.core import QgsProject, QgsRasterLayer
    project = project or QgsProject.instance()
    if name == OFFLINE:
        from .landmask import land_layer
        for lyr in project.mapLayers().values():
            if lyr.name() == OFFLINE:
                return lyr
        layer = land_layer(name=OFFLINE)
        project.addMapLayer(layer, False)
        project.layerTreeRoot().insertLayer(-1, layer)
        return layer
    src = uri(name)
    if not src:
        return None
    for lyr in project.mapLayers().values():
        if lyr.source() == src:
            return lyr
    layer = QgsRasterLayer(src, name, "wms")
    if not layer.isValid():
        return None
    project.addMapLayer(layer, False)
    project.layerTreeRoot().insertLayer(-1, layer)
    return layer
