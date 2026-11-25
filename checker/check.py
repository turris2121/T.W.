#!/usr/bin/env python3
"""Tsunami & earthquake watch for family trips.

Checks public feeds (USGS earthquakes, NOAA tsunami warning centers, GDACS),
compares them with the trip locations, sends push notifications through ntfy,
and writes data/alerts.json for the map.

Standard library only. Python 3.9+.

Usage:
  python3 checker/check.py                 # normal run (sends if NTFY_TOPIC set)
  python3 checker/check.py --dry-run       # print what would be sent
  python3 checker/check.py --dry-run --test-location "Khao Lak,8.64,98.25" --hours 168
  python3 checker/check.py --force-test    # send one test notification
"""

import argparse
import datetime as dt
import email.utils
import json
import math
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCATIONS_FILE = os.path.join(ROOT, "config", "locations.json")
STATE_FILE = os.path.join(ROOT, "checker", "state.json")
ALERTS_FILE = os.path.join(ROOT, "data", "alerts.json")

USGS_FEED = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson"
NOAA_FEEDS = {
    "PTWC (Pacific Tsunami Warning Center)": "https://www.tsunami.gov/events/xml/PHEBAtom.xml",
    "NTWC (US National Tsunami Warning Center)": "https://www.tsunami.gov/events/xml/PAAQAtom.xml",
}
GDACS_FEED = "https://www.gdacs.org/xml/rss.xml"
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
SITE_URL = os.environ.get("SITE_URL", "")
USER_AGENT = "tsunami-watch/1.0 (family travel safety)"

TSUNAMI_SPEED_KMH = 700  # typical deep-ocean tsunami speed
STATE_KEEP_DAYS = 14

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#",
    "gdacs": "http://www.gdacs.org",
}

ACTION_LINE = ("If you are near the coast: go to high ground or far inland NOW. "
               "Do not wait for an official warning. Do not go to look at the sea.")


# ---------------------------------------------------------------- helpers

def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def fmt_minutes(km):
    minutes = km / TSUNAMI_SPEED_KMH * 60
    if minutes < 60:
        return "about %d min" % max(5, round(minutes / 5) * 5)
    return "about %.1f h" % (minutes / 60)


# ---------------------------------------------------------------- locations

def load_locations(test_location=None):
    """Trip locations. LOCATIONS_JSON env (GitHub secret) wins over the file,
    so the itinerary can stay private in a public repo."""
    if test_location:
        name, lat, lon = [p.strip() for p in test_location.split(",")]
        return [{"name": name, "lat": float(lat), "lon": float(lon), "coastal": True}]
    raw = os.environ.get("LOCATIONS_JSON")
    data = json.loads(raw) if raw else load_json(LOCATIONS_FILE, {"locations": []})
    return data.get("locations", [])


def is_active(loc, today):
    """Active from 2 days before the trip until 1 day after. No dates = always."""
    try:
        start = dt.date.fromisoformat(loc["from"]) - dt.timedelta(days=2) if loc.get("from") else None
        end = dt.date.fromisoformat(loc["to"]) + dt.timedelta(days=1) if loc.get("to") else None
    except ValueError:
        return True
    return (start is None or today >= start) and (end is None or today <= end)


# ---------------------------------------------------------------- feeds

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


def _text(el, path):
    found = el.find(path, NS)
    return (found.text or "").strip() if found is not None and found.text else ""


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
        feed_title = _text(root, "atom:title")
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
                lat = float(_text(entry, "geo:lat"))
                lon = float(_text(entry, "geo:long"))
            except ValueError:
                lat = lon = None
            updated = _text(entry, "atom:updated") or _text(root, "atom:updated")
            msgs.append({
                "id": "noaa:%s:%s" % (_text(entry, "atom:id") or updated, feed_title),
                "center": center,
                "bulletin": re.sub(r"\s+", " ", feed_title),
                "category": (cat.group(1).strip() if cat else "Information"),
                "region": _text(entry, "atom:title"),
                "mag": float(mag.group(1)) if mag else None,
                "lat": lat, "lon": lon,
                "time": updated,
                "url": link or "https://www.tsunami.gov/",
            })
    return msgs


def get_gdacs():
    root = ET.fromstring(fetch(GDACS_FEED))
    events = []
    for item in root.iter("item"):
        etype = _text(item, "gdacs:eventtype")
        if etype not in ("EQ", "TS"):
            continue
        try:
            lat = float(_text(item, "geo:Point/geo:lat"))
            lon = float(_text(item, "geo:Point/geo:long"))
        except ValueError:
            continue
        pub = _text(item, "pubDate")
        try:
            t = email.utils.parsedate_to_datetime(pub)
        except (TypeError, ValueError):
            t = now_utc()
        events.append({
            "id": "gdacs:%s" % _text(item, "guid"),
            "type": etype,
            "level": _text(item, "gdacs:alertlevel") or "Green",
            "title": _text(item, "title"),
            "lat": lat, "lon": lon,
            "time": iso(t),
            "url": _text(item, "link").replace("http://", "https://"),
            "current": _text(item, "gdacs:iscurrent").lower() == "true",
        })
    return events


# ---------------------------------------------------------------- rules

def quake_rules(q, loc, dist):
    """Return (level, reason) or None. level: 5 urgent tsunami, 4 high, 3 info."""
    coastal = loc.get("coastal", True)
    shallow = q["depth"] <= 100
    m = q["mag"]
    if coastal and shallow and m >= 6.5 and dist <= 1000:
        return 5, "Strong shallow quake close enough to send a tsunami (could arrive in as little as %s)" % fmt_minutes(dist)
    if coastal and m >= 7.5 and dist <= 3000:
        return 5, "Major quake; a tsunami could cross the ocean (could arrive in as little as %s)" % fmt_minutes(dist)
    if coastal and m >= 8.0 and dist <= 12000:
        return 4, "Great earthquake; distant tsunami possible (could arrive in as little as %s). Check official alerts" % fmt_minutes(dist)
    if m >= 5.5 and dist <= 200:
        return 4, "Strong shaking possible. Expect aftershocks"
    if coastal and q["tsunami_flag"] and dist <= 3000:
        return 4, "USGS flagged this quake for tsunami evaluation"
    return None


def evaluate(locations, quakes, noaa, gdacs, since):
    """Produce a list of notification candidates, each with a stable key."""
    out = []

    for q in quakes:
        if q["time"] < since:
            continue
        hits = []
        for loc in locations:
            d = haversine_km(q["lat"], q["lon"], loc["lat"], loc["lon"])
            r = quake_rules(q, loc, d)
            if r:
                hits.append((r[0], loc["name"], d, r[1]))
        if not hits:
            continue
        hits.sort(key=lambda h: (-h[0], h[2]))
        level = hits[0][0]
        lines = ["M%.1f %s" % (q["mag"], q["place"]),
                 "Depth %d km - %s UTC" % (q["depth"], q["time"].strftime("%d %b %H:%M")), ""]
        for lv, name, d, reason in hits:
            lines.append("%s: %d km away. %s." % (name, d, reason))
        if level >= 5:
            lines += ["", ACTION_LINE]
        out.append({
            "key": "usgs:%s:L%d" % (q["id"], level),
            "level": level,
            "title": "%s M%.1f quake near %s" % ("TSUNAMI RISK -" if level >= 5 else "Earthquake -", q["mag"], hits[0][1]),
            "body": "\n".join(lines),
            "url": q["url"],
            "tags": "ocean,warning" if level >= 5 else "warning",
        })

    for m in noaa:
        if m["category"].lower() == "information" and m["mag"] is not None and m["mag"] < 6.5:
            continue
        if m["lat"] is None:
            continue
        try:
            if parse_iso(m["time"]) < since:
                continue
        except ValueError:
            pass
        official_threat = m["category"].lower() != "information"
        reach = 12000 if official_threat else 3000
        near = []
        for loc in locations:
            if not loc.get("coastal", True):
                continue
            d = haversine_km(m["lat"], m["lon"], loc["lat"], loc["lon"])
            if d <= reach:
                near.append((d, loc["name"]))
        if not near:
            continue
        near.sort()
        level = 5 if official_threat else 3
        body = ["%s: %s" % (m["center"], m["bulletin"]),
                "Category: %s. Region: %s%s" % (m["category"], m["region"],
                                                 (" (M%.1f)" % m["mag"]) if m["mag"] else ""), ""]
        body += ["%s: %d km away (as little as %s if a wave reaches it)" % (n, d, fmt_minutes(d)) for d, n in near]
        if official_threat:
            body += ["", "Follow local authorities.", ACTION_LINE]
        out.append({
            "key": "%s:%s" % (m["id"], m["category"]),
            "level": level,
            "title": "OFFICIAL TSUNAMI %s - %s" % (m["category"].upper(), m["region"]) if official_threat
                     else "Tsunami info statement - %s" % m["region"],
            "body": "\n".join(body),
            "url": m["url"],
            "tags": "rotating_light,ocean" if official_threat else "information_source",
        })

    for g in gdacs:
        if g["level"] not in ("Orange", "Red") or not g["current"]:
            continue
        try:
            if parse_iso(g["time"]) < since:
                continue
        except ValueError:
            pass
        near = sorted((haversine_km(g["lat"], g["lon"], l["lat"], l["lon"]), l["name"]) for l in locations)
        near = [n for n in near if n[0] <= 3000]
        if not near:
            continue
        out.append({
            "key": "%s:%s" % (g["id"], g["level"]),
            "level": 5 if g["level"] == "Red" else 4,
            "title": "GDACS %s alert near %s" % (g["level"].upper(), near[0][1]),
            "body": "%s\n\n%s" % (g["title"], "\n".join("%s: %d km away" % (n, d) for d, n in near)),
            "url": g["url"],
            "tags": "warning",
        })

    return out


# ---------------------------------------------------------------- notify

def send_ntfy(topic, note):
    priority = {5: "urgent", 4: "high"}.get(note["level"], "default")
    headers = {
        "User-Agent": USER_AGENT,
        # HTTP headers must be latin-1; keep titles ASCII
        "Title": note["title"].encode("ascii", "replace").decode(),
        "Priority": priority,
        "Tags": note["tags"],
    }
    if note.get("url"):
        headers["Click"] = note["url"]
    actions = []
    if SITE_URL:
        actions.append("view, Open map, %s" % SITE_URL)
        actions.append("view, What to do, %s" % SITE_URL.rstrip("/") + "/safety.html")
    if actions:
        headers["Actions"] = "; ".join(actions)
    req = urllib.request.Request("%s/%s" % (NTFY_SERVER.rstrip("/"), topic),
                                 data=note["body"].encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending; don't save state")
    ap.add_argument("--hours", type=float, default=24, help="look-back window (default 24)")
    ap.add_argument("--test-location", help='"Name,lat,lon" to use instead of the config')
    ap.add_argument("--force-test", action="store_true", help="send a test notification and exit")
    args = ap.parse_args()

    topic = os.environ.get("NTFY_TOPIC", "").strip()

    if args.force_test:
        if not topic:
            sys.exit("Set NTFY_TOPIC to send a test.")
        status = send_ntfy(topic, {
            "level": 3, "tags": "white_check_mark",
            "title": "Tsunami Watch test",
            "body": "Notifications are working. Real alerts arrive with urgent priority.",
            "url": SITE_URL,
        })
        print("Test sent, HTTP %s" % status)
        return

    now = now_utc()
    since = now - dt.timedelta(hours=args.hours)
    today = now.date()
    all_locs = load_locations(args.test_location)
    locations = [l for l in all_locs if is_active(l, today)]
    print("Active locations: %s" % (", ".join(l["name"] for l in locations) or "none"))

    errors = []
    quakes, gdacs = [], []
    try:
        quakes = get_usgs()
    except Exception as e:
        errors.append("USGS: %s" % e)
    noaa = get_noaa()
    try:
        gdacs = get_gdacs()
    except Exception as e:
        errors.append("GDACS: %s" % e)
    for e in errors:
        print("WARN: " + e, file=sys.stderr)

    # -------- notifications
    state = load_json(STATE_FILE, {"sent": {}})
    sent = state.setdefault("sent", {})
    notes = evaluate(locations, quakes, noaa, gdacs, since)
    new = [n for n in notes if n["key"] not in sent]
    if not topic and not args.dry_run:
        print("NTFY_TOPIC not set: printing only.")
    for n in sorted(new, key=lambda n: -n["level"]):
        print("\n=== [level %d] %s\n%s\n%s" % (n["level"], n["title"], n["body"], n["url"]))
        if args.dry_run or not topic:
            continue
        try:
            send_ntfy(topic, n)
            sent[n["key"]] = iso(now)
        except Exception as e:
            print("WARN: send failed: %s" % e, file=sys.stderr)
    if not new:
        print("No new alerts.")

    if args.dry_run or args.test_location:
        return

    cutoff = now - dt.timedelta(days=STATE_KEEP_DAYS)
    state["sent"] = {k: v for k, v in sent.items() if parse_iso(v) >= cutoff}
    save_json(STATE_FILE, state)

    # -------- data for the map (only rewritten when content changes,
    #          so the workflow commits/deploys only when something happened)
    week = now - dt.timedelta(days=7)
    official = [m for m in noaa if m["category"].lower() != "information"]
    payload = {
        "official_tsunami": [m for m in official if _safe_time(m["time"]) >= now - dt.timedelta(hours=48)],
        "tsunami_info": [m for m in noaa if m not in official and _safe_time(m["time"]) >= now - dt.timedelta(hours=48)],
        "gdacs": [g for g in gdacs if g["level"] in ("Orange", "Red") and _safe_time(g["time"]) >= week],
        "recent_notifications": [{"title": n["title"], "level": n["level"], "url": n["url"]}
                                 for n in notes if n["key"] in state["sent"]],
        "feed_errors": errors,
    }
    old = load_json(ALERTS_FILE, {})
    old_cmp = {k: v for k, v in old.items() if k != "updated"}
    if old_cmp != payload:
        payload["updated"] = iso(now)
        save_json(ALERTS_FILE, payload)
        print("alerts.json updated")


def _safe_time(s):
    try:
        return parse_iso(s)
    except ValueError:
        return now_utc()


if __name__ == "__main__":
    main()
