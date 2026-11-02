"""Processing provider: EO Overpass."""

from pathlib import Path

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithms.predict_overpasses import PredictOverpassesAlgorithm

ICON = str(Path(__file__).resolve().parent / "icon.png")


class EOOverpassProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        self.addAlgorithm(PredictOverpassesAlgorithm())

    def id(self):
        return "eooverpass"

    def name(self):
        return "EO Overpass"

    def longName(self):
        return "EO Overpass Predictor (Landsat / Sentinel-2)"

    def icon(self):
        return QIcon(ICON)
