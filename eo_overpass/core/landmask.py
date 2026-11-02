"""Offline land/sea background from Natural Earth 10 m land + minor islands (public domain).

data/land_10m.bin: header (JSON, zlib) with one entry per ring [offset, length, n, is_hole, xmin, ymin, xmax, ymax]
(coordinates in 1/q degree), then zlib-compressed zig-zag varint deltas. Only rings touching the requested
extent are decoded, so a regional map loads in a fraction of a second.
"""

import json
import struct
import zlib
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "data" / "land_10m.bin"
_CACHE = {}

LAND = "#BCC5BA"            # same palette as the live tracker web page
COAST = "#A3AEA2"
OCEAN = "#DAE5E9"


def _load():
    if "hdr" not in _CACHE:
        raw = _PATH.read_bytes()
        magic, nh, nb = struct.unpack("<4sII", raw[:12])
        if magic != b"EOL1":
            raise ValueError("land_10m.bin: bad header")
        _CACHE["hdr"] = json.loads(zlib.decompress(raw[12:12 + nh]))
        _CACHE["body"] = zlib.decompress(raw[12 + nh:12 + nh + nb])
    return _CACHE["hdr"], _CACHE["body"]


def _decode(body, off, length):
    out, vals, v, shift = [], [], 0, 0
    for b in body[off:off + length]:
        v |= (b & 0x7F) << shift
        if b & 0x80:
            shift += 7
            continue
        vals.append((v >> 1) ^ -(v & 1))
        v, shift = 0, 0
    x = y = 0
    for i in range(0, len(vals), 2):
        x += vals[i]
        y += vals[i + 1]
        out.append((x, y))
    return out


def rings(bbox=None):
    """[(is_hole, [(lon, lat), …]), …] for rings intersecting bbox = (xmin, ymin, xmax, ymax) in degrees."""
    hdr, body = _load()
    q = hdr["q"]
    if bbox is not None:
        bx0, by0, bx1, by1 = (round(c * q) for c in bbox)
    res = []
    for off, ln, _n, hole, x0, y0, x1, y1 in hdr["rings"]:
        if bbox is not None and (x1 < bx0 or x0 > bx1 or y1 < by0 or y0 > by1):
            continue
        res.append((bool(hole), [(x / q, y / q) for x, y in _decode(body, off, ln)]))
    return res


def land_layer(bbox=None, name="Land (Natural Earth 10 m)"):
    """Styled memory layer: land in the tracker's palette, lakes/holes drawn as water on top."""
    from qgis.core import (QgsCategorizedSymbolRenderer, QgsFeature, QgsFillSymbol, QgsGeometry,
                           QgsPointXY, QgsRendererCategory, QgsVectorLayer)
    from .records import _qfield
    lyr = QgsVectorLayer("Polygon?crs=EPSG:4326", name, "memory")
    pr = lyr.dataProvider()
    pr.addAttributes([_qfield("water", "int")])
    lyr.updateFields()
    feats = []
    x0, y0, x1, y1 = bbox if bbox is not None else (-180.0, -90.0, 180.0, 90.0)
    sea = QgsFeature(lyr.fields())                    # ocean fill, so the layer reads as land/sea on any canvas
    sea.setGeometry(QgsGeometry.fromPolygonXY([[QgsPointXY(x0, y0), QgsPointXY(x1, y0), QgsPointXY(x1, y1),
                                                 QgsPointXY(x0, y1), QgsPointXY(x0, y0)]]))
    sea.setAttribute("water", 2)
    feats.append(sea)
    for hole, pts in rings(bbox):
        f = QgsFeature(lyr.fields())
        f.setGeometry(QgsGeometry.fromPolygonXY([[QgsPointXY(x, y) for x, y in pts]]))
        f.setAttribute("water", int(hole))
        feats.append(f)
    pr.addFeatures(feats)
    land = QgsFillSymbol.createSimple({"color": LAND, "outline_color": COAST, "outline_width": "0.15"})
    water = QgsFillSymbol.createSimple({"color": OCEAN, "outline_color": COAST, "outline_width": "0.15"})
    ocean = QgsFillSymbol.createSimple({"color": OCEAN, "outline_style": "no"})
    land.symbolLayer(0).setRenderingPass(1)           # ocean, then land, then lakes on top
    water.symbolLayer(0).setRenderingPass(2)
    r = QgsCategorizedSymbolRenderer("water", [QgsRendererCategory(2, ocean, "Ocean"), QgsRendererCategory(0, land, "Land"),
                                               QgsRendererCategory(1, water, "Lakes")])
    r.setUsingSymbolLevels(True)
    lyr.setRenderer(r)
    return lyr
