"""Plain-language descriptions of every output column — shared by the field sheet guide page and the docs."""

SHEET_SYMBOLS = [
    ("track", "Satellite ground track (dashed)",
     "The line directly beneath the satellite (nadir). Its colour is the satellite's colour."),
    ("swath", "Imaged swath (shaded, solid edges)",
     "The strip of ground the instrument images on this pass: 290 km wide for Sentinel-2, 185 km for Landsat. "
     "Anything inside the solid edges can be in the image."),
    ("roi", "Your area of interest (red outline)",
     "The ROI from your input layer. ROI % in the table is the share of this area inside the swath."),
    ("badge", "Pass number (numbered circle)",
     "Sits on the pass's ground track and matches the # column of the table."),
]

SHEET_COLUMNS = [
    ("#", "Pass number. Matches the numbered circle on the map."),
    ("Satellite", "Satellite name, in the same colour as its swath on the map."),
    ("Local date", "Date at the site, in the site's own time zone (found from its location)."),
    ("Local time", "When the swath first reaches the site → when it leaves it, in local time with daylight "
                   "saving applied. A point site has a single instant."),
    ("UTC", "Closest approach to the centre of the site, in UTC (≈ the image's scene-centre time in "
            "the product metadata)."),
    ("ROI %", "Share of the site's area inside the imaged swath on this pass. 100 = the whole site."),
    ("Orbit", "R### = Sentinel-2 relative orbit (1–143; repeats every 10 days per satellite). "
              "P### = Landsat WRS-2 path (1–233; repeats every 16 days). Use it to search image catalogues."),
    ("Product(s)", "The products that will contain the site. Sentinel-2 MGRS tiles are grouped by UTM zone and "
                   "latitude band: '55H: BT CU' means tiles T55HBT and T55HCU. Landsat scenes are path/row, "
                   "e.g. 093/086."),
    ("ESA plan", "Sentinel-2 only. planned = in ESA's published acquisition plan; not planned = ESA's plan for "
                 "that date does not cover the site; no plan loaded = ESA has not published a plan that far "
                 "ahead yet (about 2 weeks) — unknown, not negative; not checked = cross-check switched off; "
                 "n/a = Landsat."),
]

SATELLITE_FACTS = [
    # name, sensor, swath, pixel size, repeat
    ("Landsat 8", "OLI / TIRS", "185 km", "30 m (15 m pan, 100 m thermal)", "16 days (8 with Landsat 9)"),
    ("Landsat 9", "OLI-2 / TIRS-2", "185 km", "30 m (15 m pan, 100 m thermal)", "16 days (8 with Landsat 8)"),
    ("Sentinel-2A", "MSI", "290 km", "10 / 20 / 60 m", "10 days; extension phase: selected regions only"),
    ("Sentinel-2B", "MSI", "290 km", "10 / 20 / 60 m", "10 days (5 with Sentinel-2C)"),
    ("Sentinel-2C", "MSI", "290 km", "10 / 20 / 60 m", "10 days (5 with Sentinel-2B)"),
]

FIELD_TIPS = [
    "Be on site and ready a few minutes before the Local time window; the satellite crosses a typical "
    "site in seconds.",
    "Prefer passes with ROI % near 100. Low ROI % means the site sits near the swath edge.",
    "For Sentinel-2, a 'planned' ESA status is the strongest evidence the image will be taken.",
    "Check the cloud forecast the day before — this sheet does not forecast cloud.",
    "Re-run the prediction 1–2 days before going out, and by the RE-RUN BY date at the latest.",
]

CSV_COLUMNS = [
    ("roi_name", "Name of the site (from your name field, or the layer name)."),
    ("satellite, mission, sensor, norad_id", "Which satellite and instrument; norad_id is its catalogue number."),
    ("direction", "Ascending (northbound) or Descending (southbound). Both missions image in daylight on "
                  "descending passes."),
    ("imaging_expected", "True when the pass is descending, the sun is high enough and the site is within the "
                         "mission's latitude limits."),
    ("roi_coverage_pct", "Share of the site's area inside the swath on this pass."),
    ("roi_entry_utc, overpass_utc, roi_exit_utc", "Swath first touches the site / closest approach to the site's "
                                                  "centre (≈ scene centre) / swath leaves the site. UTC."),
    ("duration_s", "Seconds from entry to exit."),
    ("roi_entry_local, overpass_local, roi_exit_local", "The same instants in local time with an explicit UTC "
                                                         "offset, e.g. 2026-10-26T11:27:30+11:00."),
    ("local_date, local_time, local_weekday, timezone, utc_offset", "Spreadsheet-friendly local fields."),
    ("nadir_offset_km, roi_side_of_track", "Distance of the site's centre from the ground track, and which side "
                                           "(E / W / nadir)."),
    ("edge_margin_km", "Distance from the site's centre to the nearest swath edge. Within ±5 km of 0 is "
                       "marginal."),
    ("view_angle_deg", "Angle off nadir at which the instrument sees the site's centre."),
    ("max_elevation_deg", "Satellite's elevation above the horizon seen from the site."),
    ("sun_elevation_deg, sat_altitude_km", "Sun height above the horizon and satellite altitude at overpass."),
    ("tle_epoch_utc, tle_age_days", "Orbital elements (TLE) used and how old they are at the pass. Older = less "
                                    "accurate timing."),
    ("relative_orbit", "Sentinel-2 relative orbit, or Landsat WRS-2 path."),
    ("products", "Sentinel-2 MGRS tiles (T55HCU …) or Landsat path/rows (093/086 …) containing the site."),
    ("esa_plan_status, esa_datatake_id, esa_relative_orbit", "Result of the Sentinel-2 acquisition plan "
                                                             "cross-check, and ESA's data-take ID and orbit."),
    ("note", "Mission note, e.g. Sentinel-2A's limited acquisition."),
    ("generated_utc, valid_until_utc", "When the prediction was made and when it should be re-run by."),
]

PRODUCT_COLUMNS = [
    ("pass_no", "Pass number, matching the pass layer and field sheet."),
    ("product", "MGRS tile (T55HCU) or Landsat path/row (093/086)."),
    ("kind", "Product type and the footprint drawn."),
    ("relative_orbit, wrs_path, wrs_row", "Orbit / grid identifiers."),
    ("valid_until_utc", "Re-run-by time of the prediction."),
]
