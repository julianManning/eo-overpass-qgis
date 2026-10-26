"""Standalone live-tracker web page (browser-side SGP4 via satellite.js)."""

import json
import time
from pathlib import Path

from ..core.catalogue import SATELLITES

_DATA = Path(__file__).resolve().parents[1] / "data"


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def export_tracker_html(path, tles: dict, roi_geom, roi_name: str, tz_name: str, passes: list,
                        title: str = None, standalone: bool = True):
    """Write a self-updating tracker page embedding TLEs, ROI and predicted passes."""
    sats = [{"name": n, "line1": t["line1"], "line2": t["line2"], "colour": SATELLITES[n]["colour"],
             "swath_km": SATELLITES[n]["swath_km"], "mission": SATELLITES[n]["mission"],
             "epoch": _iso(t["epoch_unix"])} for n, t in tles.items()]
    plist = [{"sat": p["satellite"], "in": _iso(p["roi_entry_utc"]), "ca": _iso(p["overpass_utc"]),
              "out": _iso(p["roi_exit_utc"]), "cov": float(p["roi_coverage_pct"]), "dir": p["direction"],
              "exp": bool(p["imaging_expected"]), "plan": p.get("esa_plan_status") or None}
             for p in passes if p["satellite"] in tles]
    payload = {
        "title": title or f"{roi_name} Overpass Tracker", "tz": tz_name, "roi_name": roi_name,
        "roi": json.loads(roi_geom.asJson(6)), "sats": sats, "passes": plist,
        "generated": _iso(time.time()),
    }
    tpl = (_DATA / "live_tracker_template.html").read_text(encoding="utf-8")
    safe = lambda s: s.replace("</", "<\\/")
    html = (tpl.replace("__TITLE__", payload["title"])
            .replace("/*__PAYLOAD__*/null", safe(json.dumps(payload)))
            .replace("/*__LAND__*/null", (_DATA / "world_land_50m.json").read_text(encoding="utf-8")))
    if standalone:
        html = ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
                '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
                + html + "\n</html>\n")
    Path(path).write_text(html, encoding="utf-8")
    return Path(path)
