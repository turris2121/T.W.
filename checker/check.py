#!/usr/bin/env python3
"""Travel hazard watch for family trips.

Every run (every 5 min on GitHub Actions):
  - USGS earthquakes, NOAA tsunami bulletins, GDACS disasters
    (cyclone, flood, wildfire, volcano, drought, quake, tsunami)
At most hourly:
  - CDC travel health notices + WHO outbreak news for trip countries
  - Open-Meteo forecast extremes (heat, cold, wind, rain) for trip places
  - Smithsonian/USGS weekly volcano reports
Plus a pre-trip briefing push 3 days before each trip's start date.

Sends push notifications through ntfy and writes data/alerts.json for the map.
Standard library only. Python 3.9+.

Usage:
  python3 checker/check.py                 # normal run (sends if NTFY_TOPIC set)
  python3 checker/check.py --dry-run       # print what would be sent
  python3 checker/check.py --dry-run --test-location "Khao Lak,8.64,98.25" --hours 168
  python3 checker/check.py --briefing --test-location "Khao Lak,8.64,98.25"
  python3 checker/check.py --force-test    # send one test notification
"""

import argparse
import datetime as dt
import os
import sys
import urllib.request

import health
import risk_profile
import sources
import weather
from common import (ALERTS_FILE, PROFILES_FILE, STATE_FILE, USER_AGENT, haversine_km, iso, is_active,
                    load_json, load_locations, now_utc, parse_iso, place_hash, reverse_geocode,
                    safe_time, save_json, trip_dates)

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
SITE_URL = os.environ.get("SITE_URL", "")

TSUNAMI_SPEED_KMH = 700  # typical deep-ocean tsunami speed
STATE_KEEP_DAYS = 21
SLOW_EVERY_MIN = 55      # health / weather / volcano reports
BRIEFING_DAYS_BEFORE = 3

ACTION_LINE = ("If you are near the coast: go to high ground or far inland NOW. "
               "Do not wait for an official warning. Do not go to look at the sea.")

# GDACS: alert radius per event type (km); Orange -> high, Red -> urgent
GDACS_RADIUS = {"EQ": 3000, "TS": 3000, "TC": 1000, "FL": 300, "VO": 300, "WF": 100}
GDACS_ADVICE = {
    "TC": "Follow local evacuation orders. Stay indoors away from windows; avoid the coast (storm surge).",
    "FL": "Move to higher ground. Never walk or drive through flood water.",
    "VO": "Respect exclusion zones; ash can close airports and harm lungs (wear a mask).",
    "WF": "Follow evacuation orders early; avoid smoke (stay indoors, N95 mask).",
    "EQ": "Expect aftershocks. If near the coast and shaking was strong: go to high ground.",
    "TS": ACTION_LINE,
}


def fmt_minutes(km):
    minutes = km / TSUNAMI_SPEED_KMH * 60
    if minutes < 60:
        return "about %d min" % max(5, round(minutes / 5) * 5)
    return "about %.1f h" % (minutes / 60)


# ---------------------------------------------------------------- quake / tsunami rules

def quake_rules(q, loc, dist):
    """Return (level, reason) or None. level: 5 urgent, 4 high, 3 info."""
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


def eval_quakes(locations, quakes, since):
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
    return out


def eval_noaa(locations, noaa, since):
    out = []
    for m in noaa:
        if m["category"].lower() == "information" and m["mag"] is not None and m["mag"] < 6.5:
            continue
        if m["lat"] is None or safe_time(m["time"]) < since:
            continue
        official_threat = m["category"].lower() != "information"
        reach = 12000 if official_threat else 3000
        near = sorted((haversine_km(m["lat"], m["lon"], l["lat"], l["lon"]), l["name"])
                      for l in locations if l.get("coastal", True))
        near = [n for n in near if n[0] <= reach]
        if not near:
            continue
        body = ["%s: %s" % (m["center"], m["bulletin"]),
                "Category: %s. Region: %s%s" % (m["category"], m["region"],
                                                 (" (M%.1f)" % m["mag"]) if m["mag"] else ""), ""]
        body += ["%s: %d km away (as little as %s if a wave reaches it)" % (n, d, fmt_minutes(d)) for d, n in near]
        if official_threat:
            body += ["", "Follow local authorities.", ACTION_LINE]
        out.append({
            "key": "%s:%s" % (m["id"], m["category"]),
            "level": 5 if official_threat else 3,
            "title": "OFFICIAL TSUNAMI %s - %s" % (m["category"].upper(), m["region"]) if official_threat
                     else "Tsunami info statement - %s" % m["region"],
            "body": "\n".join(body),
            "url": m["url"],
            "tags": "rotating_light,ocean" if official_threat else "information_source",
        })
    return out


# ---------------------------------------------------------------- other hazards

def eval_gdacs(locations, gdacs, since):
    out = []
    for g in gdacs:
        radius = GDACS_RADIUS.get(g["type"])
        if not radius or g["level"] not in ("Orange", "Red") or not g["current"]:
            continue
        if safe_time(g["time"]) < since:
            continue
        near = sorted((haversine_km(g["lat"], g["lon"], l["lat"], l["lon"]), l["name"]) for l in locations
                      if g["type"] != "TS" or l.get("coastal", True))
        near = [n for n in near if n[0] <= radius]
        if not near:
            continue
        out.append({
            "key": "%s:%s" % (g["id"], g["level"]),
            "level": 5 if g["level"] == "Red" else 4,
            "title": "%s %s alert near %s" % (g["kind"].upper(), g["level"].upper(), near[0][1]),
            "body": "%s\n\n%s\n\n%s" % (g["title"], "\n".join("%s: %d km away" % (n, d) for d, n in near),
                                        GDACS_ADVICE.get(g["type"], "")),
            "url": g["url"],
            "tags": {"TC": "cyclone", "FL": "droplet", "VO": "volcano", "WF": "fire"}.get(g["type"], "warning"),
        })
    return out


def eval_volcanoes(locations, reports):
    out = []
    for v in reports:
        erupting = "erupt" in v["status"].lower()
        radius = 100 if erupting else 50
        near = sorted((haversine_km(v["lat"], v["lon"], l["lat"], l["lon"]), l["name"]) for l in locations)
        near = [n for n in near if n[0] <= radius]
        if not near:
            continue
        out.append({
            "key": v["id"],
            "level": 4 if erupting else 3,
            "title": "Volcano %s: %s near %s" % ("ERUPTION" if erupting else "unrest", v["volcano"], near[0][1]),
            "body": "%s\n\n%s\n\n%s" % (v["title"], "\n".join("%s: %d km away" % (n, d) for d, n in near), GDACS_ADVICE["VO"]),
            "url": v["url"],
            "tags": "volcano",
        })
    return out


def eval_health(locations, countries, notices):
    out = []
    for n in notices:
        if n["global"] or (n["source"] == "CDC" and n["level"] < 2):
            continue
        places = [l["name"] for l in locations if countries.get(place_hash(l)) and
                  health.mentions_country(n["subject"], countries[place_hash(l)])]
        if not places:
            continue
        lvl = n["level"] or 2
        out.append({
            "key": n["id"],
            "level": 4 if lvl >= 3 else 3,
            "title": "Health notice for %s: %s" % (places[0], n["title"]),
            "body": "%s %s\n\n%s\n\nAffects: %s" % (
                n["source"], ("Level %d" % n["level"]) if n["level"] else "outbreak news",
                n["summary"], ", ".join(places)),
            "url": n["url"],
            "tags": "microbe",
        })
    return out


def eval_weather(locations, forecasts, today):
    out = []
    for loc in locations:
        fc = forecasts.get(place_hash(loc))
        if not fc:
            continue
        start, end = trip_dates(loc)
        for e in weather.extremes(fc, start, end, today=today):
            out.append({
                "key": "wx:%s:%s:%s:L%d" % (place_hash(loc), e["date"], e["field"], e["level"]),
                "level": e["level"],
                "title": "%s at %s on %s" % (e["label"], loc["name"], e["date"]),
                "body": "%s forecast for %s on %s.\n\n%s" % (e["label"], loc["name"], e["date"], e["advice"]),
                "url": SITE_URL,
                "tags": "thermometer" if "temperature" in e["field"] else "cloud_with_rain" if "precip" in e["field"] else "dash",
            })
    return out


def briefing_for(loc, notices, forecast, profiles):
    p = next((x for x in profiles if risk_profile.same_place(x, loc) and not risk_profile.needs_refresh(x, loc)), None)
    p = p or risk_profile.build(loc)
    lines = [risk_profile.describe(p)]
    if forecast:
        lines.append(weather.summary(forecast))
    hn = [n for n in health.notices_for_country(notices, p.get("country", "")) if not n["global"]]
    if hn:
        lines.append("Health notices: " + "; ".join(n["title"] for n in hn[:4]))
    lines.append("Before you go: save local emergency numbers, find the hotel's evacuation route, agree a family meeting point.")
    return {
        "level": 3,
        "title": "Trip briefing: %s (from %s)" % (loc["name"], loc.get("from", "now")),
        "body": "\n".join(lines),
        "url": (SITE_URL.rstrip("/") + "/safety.html") if SITE_URL else "",
        "tags": "briefcase",
    }


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
    if SITE_URL:
        headers["Actions"] = "view, Open map, %s; view, What to do, %s" % (SITE_URL, SITE_URL.rstrip("/") + "/safety.html")
    req = urllib.request.Request("%s/%s" % (NTFY_SERVER.rstrip("/"), topic),
                                 data=note["body"].encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status


# ---------------------------------------------------------------- main

def try_source(name, fn, errors, default):
    try:
        return fn()
    except Exception as e:
        errors.append("%s: %s" % (name, e))
        print("WARN: %s failed: %s" % (name, e), file=sys.stderr)
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending; don't save state")
    ap.add_argument("--hours", type=float, default=24, help="look-back window (default 24)")
    ap.add_argument("--test-location", help='"Name,lat,lon" to use instead of the config')
    ap.add_argument("--briefing", action="store_true", help="print the trip briefing for each place and exit")
    ap.add_argument("--force-test", action="store_true", help="send a test notification and exit")
    args = ap.parse_args()

    topic = os.environ.get("NTFY_TOPIC", "").strip()

    if args.force_test:
        if not topic:
            sys.exit("Set NTFY_TOPIC to send a test.")
        status = send_ntfy(topic, {
            "level": 3, "tags": "white_check_mark",
            "title": "Hazard Watch test",
            "body": "Notifications are working. Real alerts arrive with high or urgent priority.",
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
    profiles = load_json(PROFILES_FILE, {"profiles": []}).get("profiles", [])

    if args.briefing:
        notices = health.get_cdc() + health.get_who()
        for loc in all_locs:
            print("\n" + briefing_for(loc, notices, weather.get_forecast(loc["lat"], loc["lon"]), profiles)["body"])
        return

    state = load_json(STATE_FILE, {"sent": {}})
    sent = state.setdefault("sent", {})
    old_alerts = load_json(ALERTS_FILE, {})
    errors = []

    # -------- fast sources (every run)
    quakes = try_source("USGS", sources.get_usgs, errors, [])
    noaa = sources.get_noaa()
    gdacs = try_source("GDACS", sources.get_gdacs, errors, [])
    notes = eval_quakes(locations, quakes, since) + eval_noaa(locations, noaa, since) + eval_gdacs(locations, gdacs, since)

    # -------- slow sources (hourly)
    last_slow = state.get("last_slow")
    run_slow = args.dry_run or not last_slow or (now - safe_time(last_slow)).total_seconds() >= SLOW_EVERY_MIN * 60
    volcanoes = old_alerts.get("volcanoes", [])
    notices = old_alerts.get("health", [])
    if run_slow:
        volcanoes = try_source("Volcano reports", sources.get_volcano_reports, errors, volcanoes)
        notices = (try_source("CDC", health.get_cdc, errors, [n for n in notices if n["source"] == "CDC"]) +
                   try_source("WHO", health.get_who, errors, [n for n in notices if n["source"] == "WHO"]))
        countries, forecasts = {}, {}
        for loc in locations:
            h = place_hash(loc)
            prof = next((p for p in profiles if risk_profile.same_place(p, loc)), None)
            countries[h] = loc.get("country") or (prof or {}).get("country") or reverse_geocode(loc["lat"], loc["lon"]).get("country", "")
            forecasts[h] = try_source("Forecast", lambda: weather.get_forecast(loc["lat"], loc["lon"]), errors, None)
        notes += eval_volcanoes(locations, volcanoes)
        notes += eval_health(locations, countries, notices)
        notes += eval_weather(locations, forecasts, today)

        # pre-trip briefing, once per trip
        for loc in all_locs:
            start, _ = trip_dates(loc)
            if not start or not (start - dt.timedelta(days=BRIEFING_DAYS_BEFORE) <= today <= start):
                continue
            key = "brief:%s:%s" % (place_hash(loc), loc["from"])
            if key in sent:
                continue
            b = try_source("Briefing", lambda: briefing_for(loc, notices, forecasts.get(place_hash(loc)), profiles), errors, None)
            if b:
                b["key"] = key
                notes.append(b)
        if not args.dry_run:
            state["last_slow"] = iso(now)

    # -------- notifications
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

    # -------- data for the map (rewritten only when content changes, so the
    #          workflow commits/deploys only when something happened).
    #          No place names here: this file is public.
    week = now - dt.timedelta(days=7)
    official = [m for m in noaa if m["category"].lower() != "information"]
    map_gdacs = [g for g in gdacs if g["type"] in GDACS_RADIUS or g["type"] == "DR"]
    map_gdacs = [g for g in map_gdacs if (g["level"] in ("Orange", "Red") and safe_time(g["time"]) >= week)
                 or (g["current"] and g["type"] in ("TC", "FL", "VO"))]
    payload = {
        "official_tsunami": [m for m in official if safe_time(m["time"]) >= now - dt.timedelta(hours=48)],
        "tsunami_info": [m for m in noaa if m not in official and safe_time(m["time"]) >= now - dt.timedelta(hours=48)],
        "gdacs": map_gdacs,
        "volcanoes": volcanoes,
        "health": notices,
        "feed_errors": errors,
    }
    old_cmp = {k: v for k, v in old_alerts.items() if k != "updated"}
    if old_cmp != payload:
        payload["updated"] = iso(now)
        save_json(ALERTS_FILE, payload)
        print("alerts.json updated")

    # -------- regional risk profiles for the public places (cached; weekly refresh)
    try:
        if risk_profile.refresh_public():
            print("risk_profiles.json updated")
    except Exception as e:
        print("WARN: risk profiles: %s" % e, file=sys.stderr)


if __name__ == "__main__":
    main()
