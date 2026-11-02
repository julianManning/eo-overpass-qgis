"""About dialog: what the plugin does, version, author, links, citation and credits."""

import configparser
import datetime
import json
import platform
from pathlib import Path

from qgis.core import Qgis
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QGuiApplication, QIcon, QPixmap
from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout

from .core.compat import enum

HERE = Path(__file__).resolve().parent


def info() -> dict:
    """Release details kept in about.json (edit there, then run scripts/set_release_info.py)."""
    try:
        return json.loads((HERE / "about.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def nice_date(iso: str) -> str:
    try:
        return datetime.date.fromisoformat(iso).strftime("%d %B %Y").lstrip("0")
    except (TypeError, ValueError):
        return iso or ""


def metadata() -> dict:
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(HERE / "metadata.txt", encoding="utf-8")
    return dict(cp["general"]) if cp.has_section("general") else {}


def citation(md: dict, inf: dict = None) -> str:
    inf = inf if inf is not None else info()
    year = (inf.get("release_date") or "")[:4] or "n.d."
    doi = inf.get("doi")
    return (f"{inf.get('author', 'Manning, J.').split()[-1]}, {inf.get('author', 'J')[0]}. ({year}). "
            f"EO Overpass Predictor (QGIS plugin), version {md.get('version', '?')}. "
            + (f"https://doi.org/{doi}" if doi else inf.get("repository", "")))


def about_html(md: dict, inf: dict = None) -> str:
    inf = inf if inf is not None else info()
    v = md.get("version", "?")
    repo = inf.get("repository", "")
    doi = inf.get("doi")
    doi_line = f'<br>🏷 DOI: <a href="https://doi.org/{doi}">{doi}</a>' if doi else ""
    nb = inf.get("notebook_repository")
    nb_doi = inf.get("notebook_doi")
    nb_line = (f'<br>📓 Companion Python notebooks: <a href="{nb}">{nb.rstrip("/").split("/")[-1]}</a>'
               + (f' (DOI <a href="https://doi.org/{nb_doi}">{nb_doi}</a>)' if nb_doi else "")) if nb else ""
    return f"""
<p style="color:#5B6B74">Version <b>{v}</b> · released {nice_date(inf.get('release_date'))}
 · QGIS {Qgis.QGIS_VERSION.split('-')[0]} · Python {platform.python_version()}</p>

<p><b>When will Landsat 8/9 or Sentinel-2A/B/C actually image my site?</b> The plugin models each instrument's
swath (Sentinel-2 MSI 290 km, Landsat OLI 185 km) from SGP4 orbits and intersects it with your area of interest, giving
pass times in UTC and local time, % of the site covered, the orbit and product IDs to download (MGRS tiles,
WRS-2 path/rows), Sentinel-2 acquisition-plan status from ESA, a live tracker and printable field sheets.</p>

<p style="background:#FFF3CD;color:#664D03;padding:6px">⚠ Predictions are geometric and expire: re-run before
field work. Acquisition is not guaranteed and cloud is not forecast.</p>

<h3>Author</h3>
<p><b>{inf.get('author', '')}</b> · <a href="{inf.get('linkedin', '')}">LinkedIn</a></p>

<h3>Project</h3>
<p>💻 Source code: <a href="{repo}">{repo.replace('https://', '')}</a>
<br>🐞 Bug reports and ideas: <a href="{repo}/issues">GitHub issues</a>{doi_line}{nb_line}</p>

<h3>How to cite</h3>
<p>{citation(md, inf)}</p>

<h3>Data and credits</h3>
<p>Orbital elements: CelesTrak (US Space Force data via space-track.org) · Sentinel-2 acquisition plans: © ESA,
Copernicus · Coastlines and offline land/sea: Natural Earth (public domain) · Time-zone grid: derived from
timezone-boundary-builder, © OpenStreetMap contributors (ODbL) · Bundled libraries: sgp4 (MIT), geographiclib (MIT),
tzdata (Apache-2.0); satellite.js (MIT) in the web tracker · Basemap tiles: OpenStreetMap, Esri — each
provider's terms apply.</p>

<h3>Licence</h3>
<p>GNU General Public License v2.0 or later. Provided "as is", without warranty of any kind. Sentinel-2 is part of
the EU Copernicus programme; Landsat is a USGS/NASA mission. This plugin is an independent project, not affiliated
with ESA, USGS or NASA.</p>
"""


class AboutDialog(QDialog):
    def __init__(self, parent=None, open_guide=None):
        super().__init__(parent)
        md = metadata()
        self.setWindowTitle("About EO Overpass Predictor")
        self.setWindowIcon(QIcon(str(HERE / "icon.png")))
        self.resize(640, 720)
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(QPixmap(str(HERE / "icon.png")).scaled(
            88, 88, enum(Qt, "AspectRatioMode", "KeepAspectRatio"), enum(Qt, "TransformationMode", "SmoothTransformation")))
        head.addWidget(logo)
        title = QLabel(f"<span style='font-size:15pt;font-weight:600'>EO Overpass Predictor</span><br>"
                       f"<span style='color:#5B6B74'>Landsat 8/9 · Sentinel-2A/B/C overpasses for QGIS · "
                       f"v{md.get('version', '?')}</span>")
        head.addWidget(title, 1)
        lay.addLayout(head)
        text = QTextBrowser()
        text.setOpenExternalLinks(True)
        text.setHtml(about_html(md))
        lay.addWidget(text, 1)
        buttons = QDialogButtonBox()
        if open_guide is not None:
            b = QPushButton("User guide")
            b.clicked.connect(open_guide)
            buttons.addButton(b, enum(QDialogButtonBox, "ButtonRole", "ActionRole"))
        cite = QPushButton("Copy citation")
        cite.clicked.connect(lambda: QGuiApplication.clipboard().setText(citation(md)))
        buttons.addButton(cite, enum(QDialogButtonBox, "ButtonRole", "ActionRole"))
        close = buttons.addButton(enum(QDialogButtonBox, "StandardButton", "Close"))
        close.clicked.connect(self.accept)
        lay.addWidget(buttons)
