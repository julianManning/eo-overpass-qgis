"""Build dist/eo_overpass.zip for 'Install from ZIP' or upload to plugins.qgis.org."""
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "eo_overpass"
DIST = ROOT / "dist"
DIST.mkdir(exist_ok=True)
out = DIST / "eo_overpass.zip"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(SRC.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            z.write(p, p.relative_to(ROOT))
print(f"Built {out} ({out.stat().st_size / 1e6:.1f} MB)")
