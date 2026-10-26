"""Small QPainter helpers that work on Qt5 and Qt6."""

from qgis.PyQt.QtCore import QRectF, Qt
from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter, QPen, QBrush

from ..core.compat import enum

ALIGN_LEFT = enum(Qt, "AlignmentFlag", "AlignLeft")
ALIGN_RIGHT = enum(Qt, "AlignmentFlag", "AlignRight")
ALIGN_HCENTER = enum(Qt, "AlignmentFlag", "AlignHCenter")
ALIGN_VCENTER = enum(Qt, "AlignmentFlag", "AlignVCenter")
ALIGN_TOP = enum(Qt, "AlignmentFlag", "AlignTop")
ALIGN_CENTER = enum(Qt, "AlignmentFlag", "AlignCenter")
WORD_WRAP = enum(Qt, "TextFlag", "TextWordWrap")
NO_PEN = enum(Qt, "PenStyle", "NoPen")
DASH = enum(Qt, "PenStyle", "DashLine")
NO_BRUSH = enum(Qt, "BrushStyle", "NoBrush")
FMT_RGB32 = enum(QImage, "Format", "Format_RGB32")
FMT_ARGB32 = enum(QImage, "Format", "Format_ARGB32_Premultiplied")
FMT_GRAY8 = enum(QImage, "Format", "Format_Grayscale8")
ANTIALIAS = enum(QPainter, "RenderHint", "Antialiasing")
TEXT_AA = enum(QPainter, "RenderHint", "TextAntialiasing")
SMOOTH = enum(QPainter, "RenderHint", "SmoothPixmapTransform")
BOLD = enum(QFont, "Weight", "Bold")
DEMIBOLD = enum(QFont, "Weight", "DemiBold")
NORMAL = enum(QFont, "Weight", "Normal")

INK = QColor("#17232B")
MUTED = QColor("#5B6B74")
LINE = QColor("#CAD3D7")
PAPER = QColor("#FFFFFF")
WARN_BG = QColor("#FFF3CD")
WARN_FG = QColor("#664D03")
WARN_EDGE = QColor("#F0AD4E")


def enum_rgb888():
    return enum(QImage, "Format", "Format_RGB888")


def as_int(flags) -> int:
    """Qt5 flags → int(flags); Qt6 enum.Flag → .value."""
    try:
        return int(flags)
    except TypeError:
        return flags.value


def font(px: float, weight=None, family="Segoe UI") -> QFont:
    f = QFont(family)
    f.setPixelSize(max(1, int(round(px))))
    f.setWeight(weight if weight is not None else NORMAL)
    return f


def new_image(w: int, h: int, fill=PAPER) -> QImage:
    img = QImage(int(w), int(h), FMT_ARGB32)
    img.fill(fill)
    return img


def begin(img: QImage) -> QPainter:
    p = QPainter(img)
    p.setRenderHint(ANTIALIAS, True)
    p.setRenderHint(TEXT_AA, True)
    p.setRenderHint(SMOOTH, True)
    return p


def text(p: QPainter, x, y, w, h, s: str, f: QFont, color=INK, flags=None):
    p.setFont(f)
    p.setPen(QPen(color))
    p.drawText(QRectF(x, y, w, h), as_int(flags if flags is not None else (ALIGN_LEFT | ALIGN_TOP)), s)


def caution_box(p: QPainter, x, y, w, title: str, items: list, px: float) -> float:
    """Yellow caution box; returns its height."""
    f_t, f_b = font(px, BOLD), font(px * 0.92)
    lh = px * 1.45
    pad = px * 0.8
    # measure wrapped lines
    p.setFont(f_b)
    fm = p.fontMetrics()
    lines_h = 0.0
    rects = []
    for it in items:
        r = fm.boundingRect(0, 0, int(w - 2 * pad - px * 1.2), 10000, as_int(ALIGN_LEFT | WORD_WRAP), it)
        rects.append(r.height())
        lines_h += r.height() + px * 0.25
    h = pad * 2 + lh + lines_h
    p.setPen(QPen(WARN_EDGE, max(1.0, px * 0.12)))
    p.setBrush(QBrush(WARN_BG))
    p.drawRoundedRect(QRectF(x, y, w, h), px * 0.4, px * 0.4)
    p.setPen(NO_PEN)
    p.setBrush(QBrush(WARN_EDGE))
    p.drawRect(QRectF(x, y, px * 0.45, h))
    text(p, x + pad, y + pad, w - 2 * pad, lh, "⚠  " + title, f_t, WARN_FG)
    cy = y + pad + lh
    for it, rh in zip(items, rects):
        text(p, x + pad, cy, px, rh, "•", f_b, WARN_FG)
        text(p, x + pad + px * 1.2, cy, w - 2 * pad - px * 1.2, rh + 2, it, f_b, WARN_FG, ALIGN_LEFT | WORD_WRAP)
        cy += rh + px * 0.25
    return h


FIELD_CAUTIONS = [
    "Geometry shows the swath CAN image the site; acquisition is not guaranteed. Sentinel-2A (to 31 Dec 2026) images selected regions only.",
    "Timing is to ~1 s with fresh TLEs. Re-run 1–2 days before field work; check the TLE age.",
    "Passes with edge margin within ±5 km of 0 are marginal. Cloud cover is not forecast.",
]
