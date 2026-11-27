"""Live hazard feeds: USGS quakes, NOAA tsunami bulletins, GDACS disasters,
Smithsonian/USGS weekly volcano reports."""

import datetime as dt
import email.utils
import json
import re
import sys
import xml.etree.ElementTree as ET

from common import NS, fetch, iso, now_utc, text_of

USGS_FEED = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson"
NOAA_FEEDS = {
    "PTWC (Pacific Tsunami Warning Center)": "https://www.tsunami.gov/events/xml/PHEBAtom.xml",
    "NTWC (US National Tsunami Warning Center)": "https://www.tsunami.gov/events/xml/PAAQAtom.xml",
}
GDACS_FEED = "https://www.gdacs.org/xml/rss.xml"
VOLCANO_FEED = "https://volcano.si.edu/news/WeeklyVolcanoRSS.xml"

GDACS_TYPES = {
    "EQ": "Earthquake", "TS": "Tsunami", "TC": "Tropical cyclone", "FL": "Flood",
    "VO": "Volcano", "WF": "Wildfire", "DR": "Drought",
}


def _rss_time(s):
    try:
        return email.utils.parsedate_to_datetime(s).astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return now_utc()


def get_usgs():
    data = json.loads(fetch(USGS_FEED))
    quakes = []
    for f in data.get("features", []):
        p = f["properties"]
        lon, lat, depth = f["geometry"]["coordinates"]
        if p.get("mag") is None:
            continue
        quakes.append({
            "id": f["id"],
            "mag": float(p["mag"]),
            "place": p.get("place") or "Unknown location",
            "time": dt.datetime.fromtimestamp(p["time"] / 1000, dt.timezone.utc),
            "lat": lat, "lon": lon, "depth": depth or 0,
            "tsunami_flag": bool(p.get("tsunami")),
            "url": p.get("url", ""),
        })
    return quakes


def get_noaa():
    """Tsunami bulletins from the US warning centers (PTWC covers the Pacific,
    Caribbean and gives guidance worldwide)."""
    msgs = []
    for center, url in NOAA_FEEDS.items():
        try:
            root = ET.fromstring(fetch(url))
        except Exception as e:  # one center failing must not stop the run
            print("WARN: %s feed failed: %s" % (center, e), file=sys.stderr)
            continue
        feed_title = text_of(root, "atom:title")
        for entry in root.findall("atom:entry", NS):
            sum_el = entry.find("atom:summary", NS)
            summary = ET.tostring(sum_el if sum_el is not None else entry, encoding="unicode", method="text")
            cat = re.search(r"Category:\s*([A-Za-z ]+?)\s*(?:Bulletin|$)", summary)
            mag = re.search(r"Magnitude:\s*([\d.]+)", summary)
            link = ""
            for l in entry.findall("atom:link", NS):  # prefer the readable text bulletin
                if l.get("href") and (l.get("title") == "Bulletin" or not link):
                    link = l.get("href").strip()
            try:
                lat = float(text_of(entry, "geo:lat"))
                lon = float(text_of(entry, "geo:long"))
            except ValueError:
                lat = lon = None
            updated = text_of(entry, "atom:updated") or text_of(root, "atom:updated")
            msgs.append({
                "id": "noaa:%s:%s" % (text_of(entry, "atom:id") or updated, feed_title),
                "center": center,
                "bulletin": re.sub(r"\s+", " ", feed_title),
                "category": (cat.group(1).strip() if cat else "Information"),
                "region": text_of(entry, "atom:title"),
                "mag": float(mag.group(1)) if mag else None,
                "lat": lat, "lon": lon,
                "time": updated,
                "url": link or "https://www.tsunami.gov/",
            })
    return msgs


def get_gdacs():
    """All current GDACS events (earthquake, tsunami, cyclone, flood, volcano,
    wildfire, drought) with their Green/Orange/Red alert level."""
    root = ET.fromstring(fetch(GDACS_FEED))
    events = []
    for item in root.iter("item"):
        etype = text_of(item, "gdacs:eventtype")
        if etype not in GDACS_TYPES:
            continue
        try:
            lat = float(text_of(item, "geo:Point/geo:lat"))
            lon = float(text_of(item, "geo:Point/geo:long"))
        except ValueError:
            continue
        events.append({
            "id": "gdacs:%s" % text_of(item, "guid"),
            "type": etype,
            "kind": GDACS_TYPES[etype],
            "level": text_of(item, "gdacs:alertlevel") or "Green",
            "title": text_of(item, "title"),
            "name": text_of(item, "gdacs:eventname"),
            "country": text_of(item, "gdacs:country"),
            "lat": lat, "lon": lon,
            "time": iso(_rss_time(text_of(item, "pubDate"))),
            "url": text_of(item, "link").replace("http://", "https://"),
            "current": text_of(item, "gdacs:iscurrent").lower() == "true",
            "eventid": text_of(item, "gdacs:eventid"),
            "episodeid": text_of(item, "gdacs:episodeid"),
        })
    return events


def get_volcano_reports():
    """Smithsonian / USGS Weekly Volcanic Activity Report (new unrest or eruptions)."""
    root = ET.fromstring(fetch(VOLCANO_FEED))
    out = []
    for item in root.iter("item"):
        title = text_of(item, "title")
        pt = text_of(item, "georss:point").split()
        if len(pt) != 2:
            continue
        m = re.match(r"(.+?) \((.+?)\) - Report for (.+?) - (.+)$", title)
        out.append({
            "id": "gvp:%s:%s" % (text_of(item, "guid"), m.group(3) if m else title),
            "volcano": m.group(1) if m else title,
            "country": m.group(2) if m else "",
            "status": m.group(4) if m else "",
            "title": title,
            "lat": float(pt[0]), "lon": float(pt[1]),
            "time": iso(_rss_time(text_of(item, "pubDate"))),
            "url": text_of(item, "guid") or text_of(item, "link"),
        })
    return out


# ---------------------------------------------------------------- cyclone forecast geometry

GDACS_GEOMETRY = "https://www.gdacs.org/gdacsapi/api/polygons/getgeometry?eventtype=TC&eventid=%s&episodeid=%s"


def _thin(coords, max_pts):
    step = max(1, len(coords) // max_pts)
    pts = coords[::step]
    if pts[-1] != coords[-1]:
        pts.append(coords[-1])
    return [[round(c[1], 2), round(c[0], 2)] for c in pts]  # -> [lat, lon]


def get_tc_geometry(eventid, episodeid):
    """Forecast wind zones, uncertainty cone and track of a tropical cyclone
    (GDACS, from JTWC / NHC advisories). Returns
      {"zones": [{"kmh": 60|90|120, "class": "Green|Orange|Red", "polys": [{"time": iso, "ring": [[lat,lon],...]}]}],
       "cone": [[[lat,lon],...]], "track": [{"cat": "TD|TS|HU", "pts": [[lat,lon],...]}]}"""
    d = json.loads(fetch(GDACS_GEOMETRY % (eventid, episodeid), timeout=45))
    kmh_of = {"Poly_Green": 60, "Poly_Orange": 90, "Poly_Red": 120}
    zones, cone, track = {}, [], []
    for f in d.get("features", []):
        g, p = f.get("geometry") or {}, f.get("properties") or {}
        cls = p.get("Class", "")
        polys = []
        if g.get("type") == "Polygon":
            polys = [g["coordinates"]]
        elif g.get("type") == "MultiPolygon":
            polys = g["coordinates"]
        if cls in kmh_of:
            z = zones.setdefault(cls, {"kmh": kmh_of[cls], "class": cls[5:], "polys": []})
            for poly in polys:
                z["polys"].append({"time": p.get("polygondate", ""), "ring": _thin(poly[0], 150)})
        elif cls == "Poly_Cones":
            cone += [_thin(poly[0], 150) for poly in polys]
        elif g.get("type") == "LineString":
            track.append({"cat": p.get("polygonlabel") or "", "pts": _thin(g["coordinates"], 80)})
    return {"zones": sorted(zones.values(), key=lambda z: z["kmh"]), "cone": cone, "track": track}


def tc_exposure(geom, lat, lon):
    """Strongest forecast wind zone containing the point, and when it arrives first.
    Returns {"kmh", "class", "time"} or None."""
    for z in sorted(geom["zones"], key=lambda z: -z["kmh"]):
        times = sorted(p["time"] for p in z["polys"] if point_in_ring(lat, lon, p["ring"]))
        if times:
            return {"kmh": z["kmh"], "class": z["class"], "time": times[0]}
    return None


def track_distance_km(geom, lat, lon):
    pts = [pt for seg in geom["track"] for pt in seg["pts"]]
    if not pts:
        return None
    from common import haversine_km
    return min(haversine_km(a, b, lat, lon) for a, b in pts)


def point_in_ring(lat, lon, ring):
    """Ray casting; ring is [[lat, lon], ...]."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        yi, xi = ring[i]
        yj, xj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


# ---------------------------------------------------------------- US: NWS warnings, SPC tornado outlook

NWS_POINT = "https://api.weather.gov/alerts/active?point=%.4f,%.4f"
SPC_TORNADO = "https://www.spc.noaa.gov/products/outlook/day%dotlk_torn.nolyr.geojson"

# NWS event -> our level (5 urgent, 4 high, 3 normal); anything else is ignored
NWS_LEVELS = {
    "Tornado Warning": 5, "Extreme Wind Warning": 5, "Hurricane Warning": 5, "Storm Surge Warning": 5,
    "Tsunami Warning": 5, "Flash Flood Emergency": 5, "Typhoon Warning": 5,
    "Tornado Watch": 4, "Hurricane Watch": 4, "Typhoon Watch": 4, "Tropical Storm Warning": 4,
    "Severe Thunderstorm Warning": 4, "High Wind Warning": 4, "Flash Flood Warning": 4,
    "Storm Surge Watch": 4, "Tsunami Advisory": 4, "Blizzard Warning": 4, "Excessive Heat Warning": 4,
    "Extreme Heat Warning": 4, "Dust Storm Warning": 4,
    "Tropical Storm Watch": 3, "Severe Thunderstorm Watch": 3, "Wind Advisory": 3, "High Wind Watch": 3,
    "Winter Storm Warning": 3, "Flood Warning": 3, "Tsunami Watch": 3,
}


def get_nws_alerts(lat, lon):
    d = json.loads(fetch(NWS_POINT % (lat, lon), timeout=20, retries=1))
    out = []
    for f in d.get("features", []):
        p = f["properties"]
        lvl = NWS_LEVELS.get(p.get("event", ""))
        if not lvl:
            continue
        out.append({"id": "nws:%s" % p.get("id", f.get("id")), "event": p["event"], "level": lvl,
                    "headline": p.get("headline") or p["event"], "area": p.get("areaDesc", ""),
                    "ends": p.get("ends") or p.get("expires") or "",
                    "instruction": re.sub(r"\s+", " ", p.get("instruction") or "")[:400],
                    "url": "https://alerts.weather.gov/"})
    return out


def get_spc_tornado(lat, lon):
    """SPC tornado probability (% chance of a tornado within 25 miles) for day 1 and day 2."""
    out = []
    for day in (1, 2):
        d = json.loads(fetch(SPC_TORNADO % day, timeout=20, retries=1))
        best = None
        for f in d.get("features", []):
            p, g = f.get("properties", {}), f.get("geometry") or {}
            prob = p.get("DN") or 0
            polys = []
            if g.get("type") == "Polygon":
                polys = [g["coordinates"]]
            elif g.get("type") == "MultiPolygon":
                polys = g["coordinates"]
            for poly in polys:
                ring = [[c[1], c[0]] for c in poly[0]]
                if point_in_ring(lat, lon, ring) and (best is None or prob > best["prob"]):
                    best = {"day": day, "prob": int(prob), "valid": p.get("VALID_ISO", ""), "expire": p.get("EXPIRE_ISO", "")}
        if best and best["prob"] >= 2:
            out.append(best)
    return out
