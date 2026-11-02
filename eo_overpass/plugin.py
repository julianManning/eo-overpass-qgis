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
        ]
        for text, alg_id, toolbar in entries:
            act = QAction(icon, text, self.iface.mainWindow())
            act.triggered.connect(lambda _=False, a=alg_id: self._run(a))
            self.iface.addPluginToMenu(MENU, act)
            if toolbar:
                self.iface.addToolBarIcon(act)
            self.actions.append(act)
        for text, theme_icon, slot in (("Help (user guide)", "/mActionHelpContents.svg", self._open_guide),
                                       ("About", "/mActionHelpAbout.svg", self._about)):
            ic = QgsApplication.getThemeIcon(theme_icon)
            act = QAction(icon if ic.isNull() else ic, text, self.iface.mainWindow())
            act.triggered.connect(slot)
            self.iface.addPluginToMenu(MENU, act)
            self.actions.append(act)

    def _about(self):
        from .about import AboutDialog
        AboutDialog(self.iface.mainWindow(), open_guide=self._open_guide).exec()

    def _open_guide(self):
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtGui import QDesktopServices
        from .algorithms.predict_overpasses import guide_url
        QDesktopServices.openUrl(QUrl(guide_url()))

    def _run(self, alg_id):
        import processing
        processing.execAlgorithmDialog(alg_id, {})

    def _toggle_dock(self, on):
        if self.dock is None:
            from .live.dock import LiveTrackerDock
            self.dock = LiveTrackerDock(self.iface)
            self.iface.addDockWidget(_right_area(), self.dock)
        self.dock.setVisible(on)
        if on:
            self.dock.raise_()
        else:
            self.dock.btn_live.setChecked(False)

    def open_tracker(self):
        """Called after Predict overpasses: show the dock for the ROI just predicted (paused until ▶ Live)."""
        self._toggle_dock(True)
        self.dock.open_for_last_roi()

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
