"""Push the release details in eo_overpass/about.json (DOI, release date, author, links) and the version in
eo_overpass/metadata.txt into every place that shows them:

  * the About dialog          — reads about.json directly, nothing to do
  * CITATION.cff              — version, date-released, doi
  * docs/EO_Overpass_Predictor_User_Guide.docx — title page, footer, "How to cite", plugin DOI line
  * the guide PDFs            — docs/EO_Overpass_Predictor_User_Guide.pdf and eo_overpass/docs/user_guide.pdf

Usage (from the repository folder):

    python scripts/set_release_info.py                       # apply what is in about.json / metadata.txt
    python scripts/set_release_info.py --doi 10.5281/zenodo.1234567 --date 2026-11-02

Then rebuild the plugin ZIP:  python scripts/package.py

PDF export uses LibreOffice (soffice) if installed, else Microsoft Word through the docx2pdf package
(pip install docx2pdf). If neither is available, open the .docx in Word, File > Save As > PDF, and copy the PDF to
both locations above.
"""

import argparse
import datetime
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
ABOUT = ROOT / "eo_overpass" / "about.json"
META = ROOT / "eo_overpass" / "metadata.txt"
CFF = ROOT / "CITATION.cff"
DOCX = ROOT / "docs" / "EO_Overpass_Predictor_User_Guide.docx"
PDF_TARGETS = [ROOT / "docs" / "EO_Overpass_Predictor_User_Guide.pdf", ROOT / "eo_overpass" / "docs" / "user_guide.pdf"]


def version() -> str:
    m = re.search(r"^version=(.*)$", META.read_text(encoding="utf-8"), re.M)
    return m.group(1).strip() if m else "?"


def citation(info: dict, ver: str) -> str:
    """Same text as eo_overpass/about.py citation()."""
    author = info.get("author", "Julian Manning")
    year = (info.get("release_date") or "")[:4] or "n.d."
    doi = info.get("doi")
    return (f"{author.split()[-1]}, {author[0]}. ({year}). EO Overpass Predictor (QGIS plugin), version {ver}. "
            + (f"https://doi.org/{doi}" if doi else info.get("repository", "")))


def nice_date(iso: str) -> str:
    return datetime.date.fromisoformat(iso).strftime("%d %B %Y").lstrip("0")


def update_cff(info, ver):
    s = CFF.read_text(encoding="utf-8")
    s = re.sub(r"^version: .*$", f"version: {ver}", s, flags=re.M)
    s = re.sub(r"^date-released: .*$", f"date-released: {info['release_date']}", s, flags=re.M)
    doi_line = f"doi: {info['doi']}" if info.get("doi") else "# doi: add the Zenodo DOI after the first release is archived"
    s, n = re.subn(r"^#? ?doi: .*$", doi_line, s, count=1, flags=re.M)      # top-level only (no indent)
    if n == 0:
        s = s.replace("\nversion:", f"\n{doi_line}\nversion:", 1)
    CFF.write_text(s, encoding="utf-8")
    print(f"✔ CITATION.cff: version {ver}, date {info['release_date']}, doi {info.get('doi') or '(none yet)'}")


def update_docx(info, ver):
    if not DOCX.exists():
        print(f"✘ {DOCX} not found")
        return False
    rules = [
        # (file in the docx, pattern, replacement, what)
        ("word/document.xml", r"Plugin version [^<]*? · Guide dated [^<]*", f"Plugin version {ver} · Guide dated {nice_date(info['release_date'])}", "title page"),
        ("word/document.xml", r"Manning, J\. \([^<]*?\)\. EO Overpass Predictor \(QGIS plugin\), version [^<]*", citation(info, ver), "citation"),
        ("word/footer*.xml", r"EO Overpass Predictor [0-9][^<]*? — User Guide · page ", f"EO Overpass Predictor {ver} — User Guide · page ", "footer"),
    ]
    tmp = Path(tempfile.mkdtemp())
    with zipfile.ZipFile(DOCX) as z:
        z.extractall(tmp)
        names = z.namelist()
    ok = True
    for pattern_file, pat, rep, what in rules:
        hits = 0
        for f in tmp.glob(pattern_file):
            s = f.read_text(encoding="utf-8")
            s, n = re.subn(pat, lambda _m: escape(rep), s)
            hits += n
            f.write_text(s, encoding="utf-8")
        if hits:
            print(f"✔ guide {what}: {rep}")
        else:
            ok = False
            print(f"⚠ guide {what}: text not found (edited by hand in Word?) — update it manually")
    out = DOCX.with_suffix(".tmp.docx")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:                                   # keep the original order ([Content_Types].xml first)
            z.write(tmp / n, n)
    out.replace(DOCX)
    shutil.rmtree(tmp, ignore_errors=True)
    return ok


def export_pdf() -> bool:
    outdir = Path(tempfile.mkdtemp())
    pdf = outdir / (DOCX.stem + ".pdf")
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    for cand in (r"C:\Program Files\LibreOffice\program\soffice.exe", r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
        if not soffice and Path(cand).exists():
            soffice = cand
    try:
        if soffice:
            subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(outdir), str(DOCX)],
                           check=True, capture_output=True, timeout=300)
        else:
            from docx2pdf import convert                  # uses Microsoft Word on Windows/macOS
            convert(str(DOCX), str(pdf))
    except Exception as exc:                              # noqa: BLE001
        print(f"⚠ PDF not exported ({exc}). Open the .docx in Word, File > Save As > PDF, and copy it to:")
        for t in PDF_TARGETS:
            print(f"    {t}")
        return False
    try:                                                  # Word downsamples pictures to ~200 ppi: restore the originals
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import hires_pdf
        hi = outdir / "hires.pdf"
        print(f"✔ {hires_pdf.upgrade(DOCX, pdf, hi)} pictures restored to full resolution")
        pdf = hi
    except Exception as exc:                              # noqa: BLE001
        print(f"⚠ kept Word's downsampled pictures ({exc}); pip install pikepdf and run with QGIS's Python for full resolution")
    for t in PDF_TARGETS:
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pdf, t)
        print(f"✔ PDF: {t}")
    shutil.rmtree(outdir, ignore_errors=True)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--doi", help="plugin DOI, e.g. 10.5281/zenodo.1234567 (use the 'all versions' concept DOI)")
    ap.add_argument("--date", help="release date, YYYY-MM-DD")
    ap.add_argument("--no-pdf", action="store_true", help="skip the PDF export")
    a = ap.parse_args()

    info = json.loads(ABOUT.read_text(encoding="utf-8"))
    if a.doi is not None:
        info["doi"] = a.doi.strip().replace("https://doi.org/", "")
    if a.date:
        datetime.date.fromisoformat(a.date)
        info["release_date"] = a.date
    ABOUT.write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ver = version()
    print(f"✔ about.json: release {info['release_date']}, doi {info.get('doi') or '(none yet)'} — About dialog reads this")
    update_cff(info, ver)
    ok = update_docx(info, ver)
    if not a.no_pdf:
        ok = export_pdf() and ok
    print("\nDone. Rebuild the plugin ZIP with:  python scripts/package.py" if ok else "\nFinished with warnings (see ⚠ above).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
