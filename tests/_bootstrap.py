import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.append(os.path.join(ROOT, "eo_overpass", "ext"))
from qgis.core import QgsApplication
_app = QgsApplication([], True)
_app.initQgis()
