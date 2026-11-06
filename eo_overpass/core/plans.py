"""ESA Sentinel-2 acquisition plans: download (resilient to broken links), KML parsing, pass cross-check."""

import calendar
import re
import zipfile
import io
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from qgis.core import QgsGeometry, QgsPointXY

from .net import http_get

ESA_S2_PLAN_PAGE = "https://sentinels.copernicus.eu/copernicus/sentinel-2/acquisition-plans"
ESA_BASE_URL = "https://sentinels.copernicus.eu"
_PLAN_RE = re.compile(r"(s2[abc])_mp_acq__kml_(\d{8}t\d{6})_(\d{8}t\d{6})", re.I)
_UNIT = {"S2A": "SENTINEL-2A", "S2B": "SENTINEL-2B", "S2C": "SENTINEL-2C"}


def _ts(s: str) -> float:
    return calendar.timegm(datetime.strptime(s.upper(), "%Y%m%dT%H%M%S").timetuple())


def _iso(s):
    if not s:
        return None
    s = s.strip().rstrip("Z")
    fmt = "%Y-%m-%dT%H:%M:%S.%f" if "." in s else "%Y-%m-%dT%H:%M:%S"
    return calendar.timegm(datetime.strptime(s, fmt).timetuple()) + (float("0." + s.split(".")[1]) if "." in s else 0)


def fetch_plans(plan_dir, start_unix: float, end_unix: float, units=("S2A", "S2B", "S2C"),
                timeout: int = 90, log=print) -> list:
    """Download plan KMLs overlapping [start, end] into plan_dir; return local paths. Broken links are skipped."""
    plan_dir = Path(plan_dir)
    plan_dir.mkdir(parents=True, exist_ok=True)
    wanted = {u.lower() for u in units}
    html = http_get(ESA_S2_PLAN_PAGE, timeout).decode("utf-8", "replace")
    links = {}
    for m in re.finditer(r'href="([^"]*?(s2[abc])_mp_acq__kml_(\d{8}t\d{6})_(\d{8}t\d{6})[^"]*)"', html, re.I):
        href, sat, a, b = m.groups()
        if sat.lower() not in wanted or _ts(b) < start_unix or _ts(a) > end_unix:
            continue
        url = href if href.startswith("http") else ESA_BASE_URL + href
        links[f"{sat.lower()}_mp_acq__kml_{a.lower()}_{b.lower()}"] = url

    paths, failed = [], []
    for stem, url in sorted(links.items()):
        path = plan_dir / f"{stem}.kml"
        if path.exists():
            log(f"✔ {path.name} (cached)")
            paths.append(path)
            continue
        base = url.split("?")[0]
        plain = f"{ESA_BASE_URL}/documents/d/sentinel/{stem}"
        last = None
        for cand in dict.fromkeys([url, base + "?download=true", plain, plain + "?download=true"]):
            try:
                content = http_get(cand, timeout)
                if b"<kml" not in content[:2000] and content[:2] != b"PK":
                    raise ValueError("response is not KML/KMZ")
                path.write_bytes(content)
                log(f"⬇ {path.name} ({len(content) / 1e6:.1f} MB)")
                paths.append(path)
                break
            except Exception as exc:
                last = exc
        else:
            failed.append(stem)
            log(f"⚠ {stem}: download failed on ESA's link ({last}). Skipped — save it manually into {plan_dir}.")
    if not paths:
        log("⚠ No acquisition-plan KMLs available for this window (plans are published ~2 weeks ahead).")
    return paths


def _strip(tag):
    return tag.rsplit("}", 1)[-1]


def load_plans(source) -> list:
    """Parse plan KML/KMZ files (a folder, a file, or a list) into data-take dicts with QgsGeometry footprints."""
    if isinstance(source, (str, Path)) and Path(source).is_dir():
        files = sorted(list(Path(source).glob("*.kml")) + list(Path(source).glob("*.kmz")))
    elif isinstance(source, (str, Path)):
        files = [Path(source)]
    else:
        files = [Path(f) for f in source]

    out = []
    for f in files:
        raw = f.read_bytes()
        if raw[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                raw = z.read(next(n for n in z.namelist() if n.lower().endswith(".kml")))
        if b"<!DOCTYPE" in raw[:4096].upper() or b"<!ENTITY" in raw[:4096].upper():
            continue  # XML entity-expansion guard (Bandit B314): skip KML with DTD/entities
        root = ET.fromstring(raw)  # nosec B314
        doc_name = next((el.text or "" for el in root.iter() if _strip(el.tag) == "name"), "")
        m = re.search(r"from\s+(\d{8}T\d{6})\s+to\s+(\d{8}T\d{6})", doc_name, re.I) or _PLAN_RE.search(f.name)
        p0, p1 = (_ts(m.groups()[-2]), _ts(m.groups()[-1])) if m else (None, None)
        fm = _PLAN_RE.search(f.name)

        def walk(node, folders):
            for child in node:
                tag = _strip(child.tag)
                if tag in ("Document", "Folder"):
                    nm = next((c.text for c in child if _strip(c.tag) == "name"), None)
                    walk(child, folders + ([nm] if tag == "Folder" and nm else []))
                elif tag == "Placemark":
                    data = {d.get("name"): next((v.text for v in d if _strip(v.tag) == "value"), None)
                            for d in child.iter() if _strip(d.tag) == "Data"}
                    polys = []
                    for pg in (el for el in child.iter() if _strip(el.tag) == "Polygon"):
                        outer = next((el for el in pg.iter() if _strip(el.tag) == "outerBoundaryIs"), pg)
                        coords = next((el.text for el in outer.iter() if _strip(el.tag) == "coordinates"), "") or ""
                        pts = [tuple(map(float, c.split(",")[:2])) for c in coords.split()]
                        if len(pts) >= 3:
                            if max(p[0] for p in pts) - min(p[0] for p in pts) > 180:
                                pts = [((x + 360) if x < 0 else x, y) for x, y in pts]
                            polys.append(QgsGeometry.fromPolygonXY([[QgsPointXY(x, y) for x, y in pts]]).makeValid())
                    unit = next((fo for fo in folders if re.fullmatch(r"S2[ABC]", fo or "", re.I)), None)
                    unit = (unit or (fm.group(1) if fm else "")).upper()
                    out.append({
                        "satellite": _UNIT.get(unit, unit),
                        "plan_category": folders[-1] if folders else None,
                        "datatake_id": data.get("ID"), "mode": data.get("Mode"),
                        "obs_start": _iso(data.get("ObservationTimeStart")),
                        "obs_stop": _iso(data.get("ObservationTimeStop")),
                        "orbit_relative": int(data["OrbitRelative"]) if data.get("OrbitRelative") else None,
                        "plan_file": f.name, "plan_start": p0, "plan_end": p1,
                        "geometry": QgsGeometry.unaryUnion(polys) if polys else None,
                    })
        walk(root, [])
    return out


def crosscheck(passes: list, plans: list, roi_geom: QgsGeometry, tolerance_s: float = 120.0) -> list:
    """Set esa_plan_status / esa_datatake_id / esa_relative_orbit on each Sentinel-2 pass (in place)."""
    roi_alt = QgsGeometry(roi_geom)
    roi_alt.translate(360.0, 0.0)
    for p in passes:
        if p["mission"] != "Sentinel-2":
            p.update(esa_plan_status="n/a", esa_datatake_id="", esa_relative_orbit=None)
            continue
        unit = [d for d in plans if d["satellite"] == p["satellite"]]
        spans = {(d["plan_start"], d["plan_end"]) for d in unit if d["plan_start"] is not None}
        if not any(a <= p["overpass_utc"] <= b for a, b in spans):
            p.update(esa_plan_status="no plan loaded", esa_datatake_id="", esa_relative_orbit=None)
            continue
        cand = [d for d in unit if d["obs_start"] is not None and d["geometry"] is not None
                and d["obs_start"] <= p["roi_exit_utc"] + tolerance_s
                and d["obs_stop"] >= p["roi_entry_utc"] - tolerance_s
                and (d["geometry"].intersects(roi_geom) or d["geometry"].intersects(roi_alt))]
        if not cand:
            p.update(esa_plan_status="not planned", esa_datatake_id="", esa_relative_orbit=None)
            continue
        nominal = [d for d in cand if (d["plan_category"] or "").upper() == "NOMINAL"]
        best = (nominal or cand)[0]
        p.update(esa_plan_status=f"planned ({best['plan_category']})", esa_datatake_id=best["datatake_id"] or "",
                 esa_relative_orbit=best["orbit_relative"])
    return passes


def datatakes_at(plans: list, satellite: str, t_unix: float) -> list:
    """All data-takes of a unit in progress at t (geometry ignored) — verifies a 'not planned' result."""
    return [d for d in plans if d["satellite"] == satellite and d["obs_start"] is not None
            and d["obs_start"] <= t_unix <= d["obs_stop"]]
