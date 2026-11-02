"""Field sheet guide page(s): how to read the map and table, satellite facts, CSV columns.
Also bundles the sheet and its guide into one printable A4 PDF."""

from qgis.PyQt.QtCore import QPointF, QRectF
from qgis.PyQt.QtGui import QBrush, QColor, QPen

from ..core.catalogue import SATELLITES
from ..core.glossary import CSV_COLUMNS, FIELD_TIPS, SATELLITE_FACTS, SHEET_COLUMNS, SHEET_SYMBOLS
from . import _paint as P

W, H, DPI = 2480, 3508, 300
U = W / 1000.0
M = 50 * U
BOTTOM = H - 80 * U
HEAD_BG = QColor("#EDF1F2")
ZEBRA = QColor("#F7F9FA")


def _wrap_h(p, f, w, s):
    p.setFont(f)
    return p.fontMetrics().boundingRect(0, 0, int(w), 100000, P.as_int(P.ALIGN_LEFT | P.WORD_WRAP), s).height()


def _symbol(p, kind, x, y):
    """Draw a 44×24 u legend symbol with its top-left at (x, y)."""
    col = QColor(SATELLITES["SENTINEL-2B"]["colour"])
    if kind == "track":
        pen = QPen(col, 2.5 * U)
        pen.setStyle(P.DASH)
        p.setPen(pen)
        p.drawLine(QPointF(x, y + 12 * U), QPointF(x + 44 * U, y + 12 * U))
    elif kind == "swath":
        p.setPen(QPen(col, 2 * U))
        fill = QColor(col)
        fill.setAlpha(60)
        p.setBrush(QBrush(fill))
        p.drawRect(QRectF(x, y + 2 * U, 44 * U, 20 * U))
    elif kind == "roi":
        p.setPen(QPen(QColor("#D7263D"), 3 * U))
        p.setBrush(QBrush(QColor(215, 38, 61, 40)))
        p.drawRect(QRectF(x, y + 2 * U, 44 * U, 20 * U))
    elif kind == "badge":
        p.setPen(QPen(P.PAPER, 2 * U))
        p.setBrush(QBrush(col))
        p.drawEllipse(QPointF(x + 22 * U, y + 12 * U), 13 * U, 13 * U)
        P.text(p, x + 9 * U, y - 1 * U, 26 * U, 26 * U, "1", P.font(14 * U, P.BOLD), P.PAPER, P.ALIGN_CENTER)


class _Flow:
    def __init__(self, roi_name, stamp, expired, target=None):
        self.roi_name, self.stamp, self.expired, self.target = roi_name, stamp, expired, target
        self.pages, self.p, self.y = [], None, 0.0
        self._new_page(first=True)

    def _new_page(self, first=False):
        if self.p is not None:
            self._footer()
            self.cv.finish()
        self.cv = P.Canvas(W, H, self.target, DPI)
        self.pages.append(self.cv)
        self.p = self.cv.p
        if first:
            P.text(self.p, M, 40 * U, W - 2 * M, 40 * U, "How to read the field sheet", P.font(30 * U, P.BOLD))
            P.text(self.p, M, 82 * U, W - 2 * M, 22 * U, f"{self.roi_name} · page 3 of the field sheet",
                   P.font(14.5 * U), P.MUTED)
            P.text(self.p, M, 103 * U, W - 2 * M, 22 * U, self.stamp, P.font(14 * U, P.BOLD),
                   QColor("#B42318") if self.expired else P.WARN_FG)
            self.y = 145 * U
        else:
            P.text(self.p, M, 40 * U, W - 2 * M, 24 * U, f"How to read the field sheet (continued) — {self.roi_name}",
                   P.font(16 * U, P.BOLD), P.MUTED)
            self.y = 85 * U

    def _footer(self):
        P.badge(self.p, M, H - 52.5 * U, 29 * U)
        P.text(self.p, M + 38 * U, H - 50 * U, W - 2 * M - 38 * U, 24 * U,
               f"Page {len(self.pages) + 2} · EO Overpass Predictor (QGIS)", P.font(11.5 * U), P.MUTED, P.ALIGN_LEFT)

    def need(self, h):
        if self.y + h > BOTTOM:
            self._new_page()

    def heading(self, s, follow_h=60 * U):
        self.need(40 * U + follow_h)
        P.text(self.p, M, self.y, W - 2 * M, 30 * U, s, P.font(19 * U, P.BOLD))
        self.y += 34 * U
        self.p.setPen(QPen(P.LINE, 1.2 * U))
        self.p.drawLine(QPointF(M, self.y), QPointF(W - M, self.y))
        self.y += 12 * U

    def symbols(self, items):
        f_t, f_b = P.font(13.5 * U, P.DEMIBOLD), P.font(13 * U)
        tx = M + 64 * U
        tw = W - M - tx
        for kind, title, desc in items:
            h = max(28 * U, 22 * U + _wrap_h(self.p, f_b, tw, desc)) + 10 * U
            self.need(h)
            _symbol(self.p, kind, M + 6 * U, self.y + 2 * U)
            P.text(self.p, tx, self.y, tw, 22 * U, title, f_t)
            P.text(self.p, tx, self.y + 22 * U, tw, h, desc, f_b, P.MUTED, P.ALIGN_LEFT | P.WORD_WRAP)
            self.y += h

    def table(self, headers, widths, rows, px=13.0, mono_first=False):
        scale = (W - 2 * M) / sum(widths)
        ws = [w * scale for w in widths]
        f_h = P.font(px * U, P.BOLD)
        f_b = P.font(px * U)
        f_m = P.font(px * 0.95 * U, P.DEMIBOLD, "Consolas") if mono_first else P.font(px * U, P.DEMIBOLD)
        pad = 7 * U

        def header():
            hh = 30 * U
            self.p.setPen(P.NO_PEN)
            self.p.setBrush(QBrush(HEAD_BG))
            self.p.drawRect(QRectF(M, self.y, W - 2 * M, hh))
            x = M
            for s, w in zip(headers, ws):
                P.text(self.p, x + pad, self.y, w - 2 * pad, hh, s, f_h, P.INK, P.ALIGN_LEFT | P.ALIGN_VCENTER)
                x += w
            self.y += hh

        self.need(30 * U + 40 * U)
        header()
        for i, row in enumerate(rows):
            cells = [c if isinstance(c, tuple) else (c, None) for c in row]
            fonts = [f_m if j == 0 else f_b for j in range(len(cells))]
            h = max(_wrap_h(self.p, f, w - 2 * pad, s) for (s, _), f, w in zip(cells, fonts, ws)) + 2 * pad
            if self.y + h > BOTTOM:
                self._new_page()
                header()
            if i % 2:
                self.p.setPen(P.NO_PEN)
                self.p.setBrush(QBrush(ZEBRA))
                self.p.drawRect(QRectF(M, self.y, W - 2 * M, h))
            x = M
            for (s, col), f, w in zip(cells, fonts, ws):
                P.text(self.p, x + pad, self.y + pad, w - 2 * pad, h, s, f, col or P.INK, P.ALIGN_LEFT | P.WORD_WRAP)
                x += w
            self.p.setPen(QPen(P.LINE, U))
            self.p.drawLine(QPointF(M, self.y + h), QPointF(W - M, self.y + h))
            self.y += h
        self.y += 22 * U

    def bullets(self, items):
        f = P.font(13 * U)
        for it in items:
            h = _wrap_h(self.p, f, W - 2 * M - 26 * U, it) + 8 * U
            self.need(h)
            P.text(self.p, M + 4 * U, self.y, 20 * U, h, "•", f)
            P.text(self.p, M + 26 * U, self.y, W - 2 * M - 26 * U, h, it, f, P.INK, P.ALIGN_LEFT | P.WORD_WRAP)
            self.y += h
        self.y += 18 * U

    def finish(self):
        self._footer()
        return [cv.finish() for cv in self.pages]


def render_field_guide(passes: list, roi_name: str, tz_name: str, target=None) -> list:
    """QImages (A4, 300 dpi) of the guide pages that follow the field sheet; with a PdfTarget they go into the PDF."""
    produced, rerun, expired = P.validity(passes, tz_name)
    stamp = (f"⚠ EXPIRED — valid until {rerun}. Re-run the prediction." if expired
             else f"⚠ Produced {produced} · RE-RUN BY {rerun}")
    fl = _Flow(roi_name, stamp, expired, target)
    fl.heading("The map", follow_h=80 * U)
    fl.symbols(SHEET_SYMBOLS)
    fl.y += 14 * U
    fl.heading("The pass table")
    fl.table(["Column", "Meaning"], [16, 84], SHEET_COLUMNS)
    fl.heading("Satellites")
    rows = [((n, QColor(SATELLITES[n.upper()]["colour"])), s, sw, px, rep) for n, s, sw, px, rep in SATELLITE_FACTS]
    fl.table(["Satellite", "Sensor", "Swath", "Pixel size", "Repeat / notes"], [15, 14, 9, 27, 35], rows)
    fl.heading("In the field")
    fl.bullets(FIELD_TIPS)
    fl.heading("CSV columns (overpasses_<site>.csv)")
    fl.table(["Column(s)", "Meaning"], [36, 64], CSV_COLUMNS, px=12.0, mono_first=True)
    return fl.finish()


def export_field_guide_pngs(stem_path, passes, roi_name, tz_name) -> list:
    """Save the guide pages next to the sheet as <stem>_page2.png, <stem>_page3.png …"""
    from pathlib import Path
    stem = Path(stem_path)
    out = []
    for i, img in enumerate(render_field_guide(passes, roi_name, tz_name), 3):
        path = stem.with_name(f"{stem.stem}_page{i}.png")
        img.save(str(path), "PNG")
        out.append(path)
    return out
