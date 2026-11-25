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
