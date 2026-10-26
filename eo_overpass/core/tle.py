"""Two-line element (TLE) parsing, validation, caching and retrieval."""

import calendar
import time
from pathlib import Path

from .catalogue import SATELLITES, resolve_satellites
from .net import http_get

CELESTRAK_GP_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=TLE"
TLE_MIRROR_URL = "https://tle.ivanstanojevic.me/api/tle/{norad_id}"


def _checksum(line: str) -> int:
    total = 0
    for c in line[:68]:
        if c.isdigit():
            total += int(c)
        elif c == "-":
            total += 1
    return total % 10


def normalise_tle_line(line: str) -> str:
    """Validate a TLE line and rebuild its fixed-width layout if web copy-paste collapsed the spacing."""
    line = line.strip()
    if len(line) == 69 and line[68].isdigit() and _checksum(line) == int(line[68]):
        return line
    t = line.split()
    if t and t[0] == "1":
        if len(t) == 8:
            t.insert(2, "")
        if len(t) != 9:
            raise ValueError(f"Cannot parse TLE line 1: {line!r}")
        rebuilt = (f"1 {t[1]:<6} {t[2]:<8} {t[3]:>14} {t[4]:>10} "
                   f"{t[5]:>8} {t[6]:>8} {t[7]:>1} {t[8]:>5}")
    elif t and t[0] == "2":
        if len(t) == 8:
            tail = t[7]
        elif len(t) == 9:
            tail = f"{t[7]:>11}{t[8]:>6}"
        else:
            raise ValueError(f"Cannot parse TLE line 2: {line!r}")
        rebuilt = (f"2 {t[1]:>5} {t[2]:>8} {t[3]:>8} {t[4]:>7} "
                   f"{t[5]:>8} {t[6]:>8} {tail:>17}")
    else:
        raise ValueError(f"Not a TLE data line: {line!r}")
    if len(rebuilt) != 69 or _checksum(rebuilt) != int(rebuilt[68]):
        raise ValueError(f"TLE checksum failed: {line!r}")
    return rebuilt


def parse_tle_text(text: str) -> dict:
    """Parse 2- or 3-line TLE text (any spacing) → {norad_id: {"name", "line1", "line2"}}."""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    out, i = {}, 0
    while i < len(lines):
        ln = lines[i].strip()
        name = None
        if not ln.startswith("1 "):
            name = ln.lstrip("0 ").strip() or None
            i += 1
            if i >= len(lines):
                break
            ln = lines[i].strip()
        if ln.startswith("1 ") and i + 1 < len(lines) and lines[i + 1].strip().startswith("2 "):
            l1 = normalise_tle_line(ln)
            l2 = normalise_tle_line(lines[i + 1])
            out[int(l1[2:7])] = {"name": name, "line1": l1, "line2": l2}
            i += 2
        else:
            i += 1
    return out


def tle_epoch_unix(line1: str) -> float:
    """TLE epoch (columns 19–32) as Unix seconds."""
    yy = int(line1[18:20])
    year = 2000 + yy if yy < 57 else 1900 + yy
    doy = float(line1[20:32])
    jan1 = calendar.timegm((year, 1, 1, 0, 0, 0, 0, 0, 0))
    return jan1 + (doy - 1.0) * 86400.0


def _download_tle(norad: int, timeout: int):
    errors = []
    try:
        txt = http_get(CELESTRAK_GP_URL.format(norad_id=norad), timeout).decode("utf-8", "replace")
        rec = parse_tle_text(txt).get(norad)
        if rec:
            return rec, "CelesTrak"
        errors.append(f"CelesTrak: {txt.strip()[:80] or 'empty response'}")
    except Exception as exc:
        errors.append(f"CelesTrak: {exc}")
    try:
        import json
        j = json.loads(http_get(TLE_MIRROR_URL.format(norad_id=norad), timeout))
        rec = parse_tle_text(f"{j['line1']}\n{j['line2']}").get(norad)
        if rec:
            return rec, "TLE API mirror"
        errors.append("mirror: unparseable")
    except Exception as exc:
        errors.append(f"mirror: {exc}")
    raise RuntimeError("; ".join(errors))


def fetch_tles(cache_dir, satellites=None, max_age_hours: float = 12.0, tle_text: str = None,
               timeout: int = 30, log=print) -> dict:
    """
    Current TLEs for the selected satellites, with a disk cache, mirror fallback and offline use.

    Returns {name: {"norad_id", "line1", "line2", "epoch_unix", "source"}}.
    """
    names = resolve_satellites(satellites)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    manual = parse_tle_text(tle_text) if tle_text and tle_text.strip() else {}
    out = {}
    for name in names:
        norad = SATELLITES[name]["norad_id"]
        cache = cache_dir / f"{norad}.tle"
        rec = source = None
        if norad in manual:
            rec, source = manual[norad], "manual"
            cache.write_text(f"{name}\n{rec['line1']}\n{rec['line2']}\n")
        else:
            age_h = (time.time() - cache.stat().st_mtime) / 3600 if cache.exists() else None
            if age_h is not None and age_h < max_age_hours:
                rec, source = parse_tle_text(cache.read_text()).get(norad), f"cache ({age_h:.1f} h old)"
            if rec is None:
                try:
                    rec, source = _download_tle(norad, timeout)
                    cache.write_text(f"{name}\n{rec['line1']}\n{rec['line2']}\n")
                except Exception as exc:
                    if cache.exists():
                        rec = parse_tle_text(cache.read_text()).get(norad)
                        source = f"stale cache ({age_h:.1f} h old; download failed)"
                    if rec is None:
                        raise RuntimeError(f"No TLE available for {name} ({norad}): {exc}") from exc
        epoch = tle_epoch_unix(rec["line1"])
        out[name] = {"norad_id": norad, "line1": rec["line1"], "line2": rec["line2"],
                     "epoch_unix": epoch, "source": source}
        age_d = (time.time() - epoch) / 86400
        flag = "  ⚠ older than 7 days" if age_d > 7 else ""
        log(f"{name:<12} NORAD {norad:<6} epoch {time.strftime('%Y-%m-%d %H:%M', time.gmtime(epoch))} UTC "
            f"({age_d:.1f} d old) — {source}{flag}")
    return out
