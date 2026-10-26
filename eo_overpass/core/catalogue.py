"""Mission catalogue: nadir-looking optical sensors with nominal swath widths."""

SATELLITES = {
    "LANDSAT 8": {
        "norad_id": 39084, "mission": "Landsat", "sensor": "OLI/TIRS", "swath_km": 185.0,
        "lat_limits": (-81.8, 81.8), "colour": "#F28E2B",
        "note": "USGS LTAP schedules >99% of daytime land and near-shore coastal scenes.",
    },
    "LANDSAT 9": {
        "norad_id": 49260, "mission": "Landsat", "sensor": "OLI-2/TIRS-2", "swath_km": 185.0,
        "lat_limits": (-81.8, 81.8), "colour": "#E15759",
        "note": "USGS LTAP schedules >99% of daytime land and near-shore coastal scenes.",
    },
    "SENTINEL-2A": {
        "norad_id": 40697, "mission": "Sentinel-2", "sensor": "MSI", "swath_km": 290.0,
        "lat_limits": (-56.0, 82.8), "colour": "#76B7B2",
        "note": "Extension campaign (to 31 Dec 2026): selected regions only — check the ESA plan.",
    },
    "SENTINEL-2B": {
        "norad_id": 42063, "mission": "Sentinel-2", "sensor": "MSI", "swath_km": 290.0,
        "lat_limits": (-56.0, 82.8), "colour": "#4E79A7",
        "note": "Nominal constellation (with S2C). Open ocean is not acquired.",
    },
    "SENTINEL-2C": {
        "norad_id": 60989, "mission": "Sentinel-2", "sensor": "MSI", "swath_km": 290.0,
        "lat_limits": (-56.0, 82.8), "colour": "#59A14F",
        "note": "Nominal constellation (with S2B). Open ocean is not acquired.",
    },
}

SATELLITE_NAMES = list(SATELLITES)

_SHORT = {"L8": "LANDSAT 8", "L9": "LANDSAT 9", "S2A": "SENTINEL-2A", "S2B": "SENTINEL-2B", "S2C": "SENTINEL-2C"}


def resolve_satellites(satellites=None) -> list:
    """Normalise names, short codes ("S2B") or NORAD ids to catalogue keys."""
    if not satellites:
        return list(SATELLITES)
    by_id = {v["norad_id"]: k for k, v in SATELLITES.items()}
    out = []
    for s in satellites:
        if isinstance(s, int) and s in by_id:
            out.append(by_id[s])
            continue
        key = str(s).upper().replace("_", " ").strip()
        key = _SHORT.get(key, key)
        if key not in SATELLITES:
            raise ValueError(f"Unknown satellite '{s}'. Known: {list(SATELLITES)}")
        out.append(key)
    return out


def hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
