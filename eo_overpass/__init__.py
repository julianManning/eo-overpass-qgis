"""EO Overpass Predictor — QGIS plugin (Landsat 8/9 and Sentinel-2A/B/C overpass prediction)."""

import os
import sys

# Bundled pure-Python dependencies (sgp4, geographiclib, tzdata). Appended, not prepended:
# if the user's QGIS Python already has them (e.g. the faster compiled sgp4), those are used.
_EXT = os.path.join(os.path.dirname(__file__), "ext")
if _EXT not in sys.path:
    sys.path.append(_EXT)


def classFactory(iface):
    from .plugin import EOOverpassPlugin
    return EOOverpassPlugin(iface)
