#!/usr/bin/env python3
"""Snapshot significant historical tsunamis (since 1900) from NOAA NCEI
into data/historical_tsunamis.json for the map. Run occasionally by hand."""
import json, os, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "historical_tsunamis.json")
API = "https://www.ngdc.noaa.gov/hazel/hazard-service/api/v1/tsunamis/events?minYear=1900&page=%d"

def get(page):
    req = urllib.request.Request(API % page, headers={"User-Agent": "tsunami-watch/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

events, page = [], 1
while True:
    d = get(page)
    for e in d["items"]:
        if e.get("eventValidity", 0) < 3 or e.get("latitude") is None:
            continue  # keep probable/definite only
        deaths = e.get("deathsTotal") or e.get("deaths") or 0
        height = e.get("maxWaterHeight") or 0
        if deaths < 1 and height < 1:
            continue  # keep damaging or >=1 m events
        events.append({
            "y": e["year"], "m": e.get("month"), "d": e.get("day"),
            "lat": e["latitude"], "lon": e["longitude"],
            "place": (e.get("locationName") or "").title(),
            "country": (e.get("country") or "").title(),
            "mag": e.get("eqMagnitude"), "h": height or None,
            "deaths": deaths or None, "id": e["id"],
        })
    if page >= d.get("totalPages", 1):
        break
    page += 1

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(events, f, separators=(",", ":"))
print("%d events -> %s" % (len(events), OUT))
