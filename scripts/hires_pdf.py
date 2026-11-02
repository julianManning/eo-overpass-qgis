"""Word downsamples every picture in a PDF export to about 200 ppi, which blurs the field-sheet figures.
This puts the original pictures from the .docx back into the PDF (long side capped at 2400 px, JPEG quality 95).

    python scripts/hires_pdf.py guide.docx word_export.pdf out.pdf

Needs pikepdf (pip install pikepdf), numpy and QGIS's PyQt (run with QGIS's Python)."""

import sys
import zipfile

import numpy as np
import pikepdf
from qgis.PyQt.QtCore import QBuffer, QByteArray, Qt
from qgis.PyQt.QtGui import QColor, QImage, QPainter

CAP = 2400


def _flat_rgb(img: QImage) -> QImage:
    """Opaque RGB888 copy (transparent areas become white)."""
    out = QImage(img.width(), img.height(), QImage.Format.Format_RGB888)
    out.fill(QColor("white"))
    p = QPainter(out)
    p.drawImage(0, 0, img)
    p.end()
    return out


def _rgb_bytes(img: QImage) -> bytes:
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    a = np.frombuffer(img.constBits().asstring(bpl * h), dtype=np.uint8).reshape(h, bpl)[:, :w * 3]
    return a.tobytes()


def _array(img: QImage, size) -> np.ndarray:
    s = _flat_rgb(img).scaled(size[0], size[1], Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation).convertToFormat(QImage.Format.Format_RGB888)
    return np.frombuffer(_rgb_bytes(s), dtype=np.uint8).astype(np.int16).reshape(size[1], size[0], 3)


def _pdf_pixels(obj) -> np.ndarray | None:
    w, h = int(obj.Width), int(obj.Height)
    filt = str(obj.get("/Filter", ""))
    if "DCTDecode" in filt:
        img = QImage.fromData(QByteArray(obj.read_raw_bytes()))
        return _array(img, (w, h)) if not img.isNull() else None
    if "FlateDecode" in filt and str(obj.get("/ColorSpace", "")) == "/DeviceRGB":
        raw = np.frombuffer(obj.read_bytes(), dtype=np.uint8)
        return raw[:w * h * 3].reshape(h, w, 3).astype(np.int16) if raw.size >= w * h * 3 else None
    return None


def upgrade(docx, pdf_in, pdf_out) -> int:
    media = []
    with zipfile.ZipFile(docx) as z:
        for n in z.namelist():
            if n.startswith("word/media/") and n.lower().endswith(".png"):
                img = QImage.fromData(QByteArray(z.read(n)))
                if not img.isNull():
                    media.append((n, img))
    pdf = pikepdf.open(pdf_in)
    done = 0
    for obj in pdf.objects:
        if not isinstance(obj, pikepdf.Stream) or obj.get("/Subtype") != "/Image" or "/Width" not in obj:
            continue
        w, h = int(obj.Width), int(obj.Height)
        if w < 400:
            continue
        cands = [(n, im) for n, im in media if im.width() > w * 1.15 and abs(im.width() / im.height() - w / h) < 0.01 * w / h]
        if not cands:
            continue
        have = _pdf_pixels(obj)
        if have is not None and len(cands) > 1:
            cands.sort(key=lambda c: float(np.abs(_array(c[1], (w, h)) - have).mean()))
        n, im = cands[0]
        k = min(1.0, CAP / max(im.width(), im.height()))
        size = (round(im.width() * k), round(im.height() * k))
        flat = _flat_rgb(im)
        if k < 1:
            flat = flat.scaled(size[0], size[1], Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation).convertToFormat(QImage.Format.Format_RGB888)
        buf = QBuffer()                                  # JPEG q95: visually lossless, ~5x smaller than lossless
        buf.open(QBuffer.OpenModeFlag.WriteOnly)
        flat.save(buf, "JPEG", 95)
        obj.write(bytes(buf.data()), filter=pikepdf.Name.DCTDecode)
        obj.Width, obj.Height = flat.width(), flat.height()
        obj.ColorSpace, obj.BitsPerComponent = pikepdf.Name.DeviceRGB, 8
        for key in ("/SMask", "/DecodeParms", "/Mask"):
            if key in obj:
                del obj[key]
        done += 1
    pdf.save(pdf_out, compress_streams=True)
    return done


if __name__ == "__main__":
    print(upgrade(*sys.argv[1:4]), "pictures restored")
