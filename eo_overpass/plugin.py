"""QGIS plugin entry: registers the Processing provider, menu entries and the live-tracker dock."""

from pathlib import Path

from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QIcon

try:                                             # Qt6 moved QAction to QtGui
    from qgis.PyQt.QtGui import QAction
except ImportError:
    from qgis.PyQt.QtWidgets import QAction

from .provider import EOOverpassProvider

ICON = str(Path(__file__).resolve().parent / "icon.png")
MENU = "&EO Overpass"


class EOOverpassPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.dock = None
        self.actions = []

    def initProcessing(self):
        self.provider = EOOverpassProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()
        icon = QIcon(ICON)
        entries = [
            ("Predict overpasses…", "eooverpass:predict_overpasses", True),
            ("Cross-check Sentinel-2 acquisition plans…", "eooverpass:crosscheck_s2_plans", False),
            ("Export field pack…", "eooverpass:export_field_pack", False),
            ("Refresh orbital elements (TLEs)…", "eooverpass:refresh_tles", False),
        ]
        for text, alg_id, toolbar in entries:
            act = QAction(icon, text, self.iface.mainWindow())
            act.triggered.connect(lambda _=False, a=alg_id: self._run(a))
            self.iface.addPluginToMenu(MENU, act)
            if toolbar:
                self.iface.addToolBarIcon(act)
            self.actions.append(act)
        live = QAction(icon, "Live tracker", self.iface.mainWindow())
        live.setCheckable(True)
        live.toggled.connect(self._toggle_dock)
        self.iface.addPluginToMenu(MENU, live)
        self.iface.addToolBarIcon(live)
        self.actions.append(live)
        self.live_action = live

    def _run(self, alg_id):
        import processing
        processing.execAlgorithmDialog(alg_id, {})

    def _toggle_dock(self, on):
        if self.dock is None:
            from .live.dock import LiveTrackerDock
            self.dock = LiveTrackerDock(self.iface)
            self.iface.addDockWidget(_right_area(), self.dock)
            self.dock.visibilityChanged.connect(lambda v: self.live_action.setChecked(v))
        self.dock.setVisible(on)
        if not on:
            self.dock.btn_live.setChecked(False)

    def unload(self):
        for act in self.actions:
            self.iface.removePluginMenu(MENU, act)
            self.iface.removeToolBarIcon(act)
        if self.dock is not None:
            self.dock.timer.stop()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)


def _right_area():
    from qgis.PyQt.QtCore import Qt
    from .core.compat import enum
    return enum(Qt, "DockWidgetArea", "RightDockWidgetArea")
