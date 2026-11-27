"""Shared helpers for the checker scripts (stdlib only, Python 3.9+)."""

import datetime as dt
import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCATIONS_FILE = os.path.join(ROOT, "config", "locations.json")
STATE_FILE = os.path.join(ROOT, "checker", "state.json")
ALERTS_FILE = os.path.join(ROOT, "data", "alerts.json")
PROFILES_FILE = os.path.join(ROOT, "data", "risk_profiles.json")
HISTORY_FILE = os.path.join(ROOT, "data", "historical_tsunamis.json")

USER_AGENT = "travel-hazard-watch/1.0 (family travel safety; github.com/turris2121/T.W.)"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#",
    "gdacs": "http://www.gdacs.org",
    "georss": "http://www.georss.org/georss",
}


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def safe_time(s):
    try:
        return parse_iso(s)
    except (TypeError, ValueError):
        return now_utc()


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fetch(url, timeout=30, retries=2, pause=2.0):
    """GET with a couple of retries (some services rate-limit bursts)."""
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, OSError) as e:
            last = e
            if attempt < retries:
                time.sleep(pause * (attempt + 1))
    raise last


def fetch_json(url, **kw):
    """fetch() + JSON parse; one extra try if a service returns an empty/garbled body."""
    try:
        return json.loads(fetch(url, **kw))
    except ValueError:
        time.sleep(2)
        return json.loads(fetch(url, **kw))


def text_of(el, path):
    found = el.find(path, NS)
    return (found.text or "").strip() if found is not None and found.text else ""


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data, compact=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
        else:
            json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def place_hash(loc):
    """Short stable id for a place that doesn't reveal it (state.json is public)."""
    raw = "%s|%.3f|%.3f" % (loc.get("name", ""), loc["lat"], loc["lon"])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


# ---------------------------------------------------------------- locations

def parse_test_location(s):
    name, lat, lon = [p.strip() for p in s.split(",")[:3]]
    return [{"name": name, "lat": float(lat), "lon": float(lon), "coastal": True}]


def public_locations():
    return load_json(LOCATIONS_FILE, {"locations": []}).get("locations", [])


def load_locations(test_location=None):
    """Trip places. LOCATIONS_JSON env (GitHub secret) wins over the file,
    so the itinerary can stay private in a public repo."""
    if test_location:
        return parse_test_location(test_location)
    raw = os.environ.get("LOCATIONS_JSON", "").strip()
    if raw:
        return json.loads(raw).get("locations", [])
    return public_locations()


def using_secret_locations():
    return bool(os.environ.get("LOCATIONS_JSON", "").strip())


def trip_dates(loc):
    try:
        start = dt.date.fromisoformat(loc["from"]) if loc.get("from") else None
        end = dt.date.fromisoformat(loc["to"]) if loc.get("to") else None
    except ValueError:
        return None, None
    return start, end


def is_active(loc, today):
    """Active from 2 days before the trip until 1 day after. No dates = always."""
    start, end = trip_dates(loc)
    start = start - dt.timedelta(days=2) if start else None
    end = end + dt.timedelta(days=1) if end else None
    return (start is None or today >= start) and (end is None or today <= end)


# ---------------------------------------------------------------- geocoding

_GEO_CACHE = {}


def reverse_geocode(lat, lon, lang="en"):
    """Country + province for a point (OpenStreetMap Nominatim; max 1 req/s).
    lang="" returns local-language names. Returns {"country", "country_code",
    "region", "district"} or {} on failure."""
    key = (round(lat, 3), round(lon, 3), lang)
    if key in _GEO_CACHE:
        return _GEO_CACHE[key]
    url = ("https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=10"
           "%s&lat=%.4f&lon=%.4f" % ("&accept-language=" + lang if lang else "", lat, lon))
    try:
        a = fetch_json(url).get("address", {})
    except Exception:
        return {}
    time.sleep(1.1)  # Nominatim usage policy
    out = {
        "country": a.get("country", ""),
        "country_code": (a.get("country_code") or "").upper(),
        "region": a.get("state") or a.get("province") or a.get("region") or a.get("state_district") or "",
        "district": a.get("county") or a.get("city") or a.get("town") or "",
    }
    _GEO_CACHE[key] = out
    return out


# US states and territories (NWS / SPC coverage), rough boxes
US_BOXES = [(24.0, 50.0, -125.0, -66.0), (51.0, 72.0, -170.0, -129.0), (18.5, 22.5, -161.0, -154.0),
            (17.5, 18.7, -67.5, -64.5), (13.0, 15.5, 144.0, 146.5), (-14.6, -14.0, -171.0, -169.0)]


def in_us(lat, lon):
    return any(a <= lat <= b and c <= lon <= d for a, b, c, d in US_BOXES)
