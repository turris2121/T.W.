#!/usr/bin/env python3
"""One-time (yearly) snapshot of storm history used by the risk profiles.

  data/cyclone_tracks.json  - every tropical storm/hurricane track point since 1980
                              (NOAA IBTrACS), 6-hourly positions:
                              [lat*10, lon*10, yyyymmdd, max_wind_kt, storm_index]
  data/us_tornadoes.json    - US tornadoes since 1990 (NOAA SPC):
                              [lat*100, lon*100, yyyymmdd, ef_scale]

Usage:
  python3 checker/build_climatology.py --download-dir /some/empty/dir
  python3 checker/build_climatology.py --ibtracs FILE.csv --tornadoes FILE.csv
"""

import argparse
import csv
import io
import os
import sys
import urllib.request

from common import ROOT, USER_AGENT, save_json

IBTRACS_URL = ("https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/"
               "v04r01/access/csv/ibtracs.since1980.list.v04r01.csv")
TORNADO_URL = "https://www.spc.noaa.gov/wcm/data/1950-2025_actual_tornadoes.csv"
TS_KT = 34  # tropical-storm strength


def download(url, path):
    print("Downloading %s" % url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=600) as r, open(path, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return path


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def build_cyclones(path):
    best = {}  # (storm, 6-hourly synoptic time) -> row
    sids = {}
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        rd = csv.DictReader(f)
        next(rd)  # units row
        for r in rd:
            if r.get("NATURE") in ("ET", "DS"):
                continue  # skip extratropical / disturbance stages
            wind = num(r.get("USA_WIND")) or num(r.get("WMO_WIND"))
            if wind is None or wind < TS_KT:
                continue
            lat, lon = num(r.get("LAT")), num(r.get("LON"))
            if lat is None or lon is None:
                continue
            t = r["ISO_TIME"]
            if t[11:13] not in ("00", "06", "12", "18"):
                continue  # keep the 6-hourly fixes (a storm moves ~100-150 km between them)
            day = int(t[:10].replace("-", ""))
            sid = r["SID"]
            idx = sids.setdefault(sid, len(sids))
            key = (idx, t[:13])
            if key not in best or wind > best[key][3]:
                lon = ((lon + 180) % 360) - 180
                best[key] = [round(lat * 10), round(lon * 10), day, int(wind), idx]
    rows = sorted(best.values(), key=lambda x: (x[2], x[4]))
    out = os.path.join(ROOT, "data", "cyclone_tracks.json")
    save_json(out, {"source": "NOAA IBTrACS v04r01 since 1980", "fields": ["lat10", "lon10", "ymd", "kt", "storm"], "interval_h": 6,
                    "first_year": rows[0][2] // 10000, "last_year": rows[-1][2] // 10000, "rows": rows}, compact=True)
    print("%d 6-hourly storm positions from %d storms -> %s" % (len(rows), len(sids), out))


def build_tornadoes(path, since=1990):
    rows = []
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            try:
                yr = int(r["yr"])
            except (KeyError, ValueError):
                continue
            if yr < since:
                continue
            lat, lon, mag = num(r.get("slat")), num(r.get("slon")), num(r.get("mag"))
            if not lat or not lon:
                continue
            rows.append([round(lat * 100), round(lon * 100), yr * 10000 + int(r["mo"]) * 100 + int(r["dy"]),
                         int(mag) if mag is not None and mag >= 0 else -1])
    out = os.path.join(ROOT, "data", "us_tornadoes.json")
    save_json(out, {"source": "NOAA SPC tornado database", "fields": ["lat100", "lon100", "ymd", "ef"],
                    "first_year": since, "last_year": max(r[2] for r in rows) // 10000, "rows": rows}, compact=True)
    print("%d tornadoes -> %s" % (len(rows), out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download-dir", help="empty directory to download the source CSVs into")
    ap.add_argument("--ibtracs")
    ap.add_argument("--tornadoes")
    a = ap.parse_args()
    if a.download_dir:
        a.ibtracs = a.ibtracs or download(IBTRACS_URL, os.path.join(a.download_dir, "ibtracs.csv"))
        a.tornadoes = a.tornadoes or download(TORNADO_URL, os.path.join(a.download_dir, "tornadoes.csv"))
    if not (a.ibtracs or a.tornadoes):
        sys.exit("Give --download-dir or the CSV paths.")
    if a.ibtracs:
        build_cyclones(a.ibtracs)
    if a.tornadoes:
        build_tornadoes(a.tornadoes)


if __name__ == "__main__":
    main()
