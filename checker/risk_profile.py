#!/usr/bin/env python3
"""Regional hazard profile for each trip place: "how likely is it here?"

Sources:
  - ThinkHazard! (World Bank GFDRR): 11 hazard levels per province, with the
    probability behind each level ("2% chance ... in the next 50 years").
  - Open-Meteo historical archive: how often the trip dates were very hot,
    very wet or very windy over the past 20 years.
  - USGS earthquake catalog: M6+ quakes nearby since 1976 -> chance during the trip.
  - NOAA NCEI: past tsunamis within 500 km (data/historical_tsunamis.json).

Usage:
  python3 checker/risk_profile.py                     # refresh data/risk_profiles.json (public places)
  python3 checker/risk_profile.py --test-location "Khao Lak,8.64,98.25" [--from 2026-12-20 --to 2027-01-03]
"""

import argparse
import datetime as dt
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse

from common import (HISTORY_FILE, PROFILES_FILE, ROOT, in_us, fetch_json, haversine_km, iso, load_json,
                    now_utc, parse_test_location, public_locations, reverse_geocode, safe_time,
                    save_json, trip_dates)
from health import norm

TH_SEARCH = "https://thinkhazard.org/en/administrativedivision?q=%s"
TH_REPORT = "https://thinkhazard.org/en/report/%s.json"
TH_DETAIL = "https://thinkhazard.org/en/report/%s/%s.json"
TH_PAGE = "https://thinkhazard.org/en/report/%s"
ARCHIVE = ("https://archive-api.open-meteo.com/v1/archive?latitude=%.4f&longitude=%.4f"
           "&start_date=%s&end_date=%s&timezone=auto"
           "&daily=temperature_2m_max,apparent_temperature_max,precipitation_sum,wind_gusts_10m_max")
USGS_COUNT = ("https://earthquake.usgs.gov/fdsnws/event/1/count?format=geojson&latitude=%.4f"
              "&longitude=%.4f&maxradiuskm=%d&minmagnitude=%.1f&starttime=%s")

LEVEL_RANK = {"HIG": 4, "MED": 3, "LOW": 2, "VLO": 1}
REFRESH_DAYS = 7
TH_SPACING = 5.0    # ThinkHazard answers 404 for ~20 s after a burst
TH_COOLDOWN = 25.0

# What a traveler should take from each ThinkHazard hazard
TRAVEL_TIP = {
    "FL": "Avoid riverbanks and low ground after heavy rain.",
    "UF": "Streets can flood fast in downpours; don't walk or drive through water.",
    "CF": "Storm surge possible; know the route to higher ground from your hotel.",
    "EQ": "Know 'drop, cover, hold on'; choose modern, well-built accommodation.",
    "LS": "Avoid steep slopes, mountain roads and hillside stays during heavy rain.",
    "TS": "Learn the evacuation route; strong or long shaking = go to high ground.",
    "VA": "Check volcano status; carry masks for ash; respect exclusion zones.",
    "CY": "Check cyclone season and forecasts; follow evacuation orders.",
    "DG": "Water shortages possible; carry bottled water.",
    "EH": "Plan activities for early morning/evening; hydrate; watch for heat stroke.",
    "WF": "Check fire alerts before hiking; leave early if told to evacuate.",
}


_last_th = [0.0]


def th_get(url):
    """Paced ThinkHazard request; waits out its throttle (which looks like a 404)."""
    for attempt in range(3):
        wait = _last_th[0] + TH_SPACING - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_th[0] = time.time()
        try:
            return fetch_json(url, retries=0)
        except urllib.error.HTTPError as e:
            if e.code != 404 or attempt == 2:
                raise
            time.sleep(TH_COOLDOWN)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(TH_SPACING)


def _letters(s):
    return re.sub(r"[^a-z]", "", norm(s))


def _strip_admin_words(s):
    s = re.sub(r"\b(province|region|state|prefecture|district|county|municipality|governorate|"
               r"department|autonomous community of|community of|metropolitan|city of|special region of)\b",
               "", s or "", flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" -,")


def _variants(name):
    base = _strip_admin_words(name)
    out = []
    for v in (base, base.replace("-", ""), base.replace("-", " "), base.split(" ")[0] if " " in base else ""):
        if v and v not in out and len(v) >= 3:
            out.append(v)
    return out


def _search(q):
    return th_get(TH_SEARCH % urllib.parse.quote(q)).get("data", [])


def find_division(geo, local=None):
    """Most specific ThinkHazard division for a reverse-geocoded place.
    `local` = the same place with local-language names, tried second."""
    country = geo.get("country", "")
    c = _letters(country)

    def same_country(r):
        a = _letters(r.get("admin0", ""))
        return a and c and (a == c or a.startswith(c) or c.startswith(a))

    def close(a, b):
        """Same name, allowing small spelling differences (Phang-nga / Phangnga)."""
        a, b = _letters(_strip_admin_words(a)), _letters(_strip_admin_words(b))
        if not a or not b:
            return False
        if a == b:
            return True
        short, long_ = sorted((a, b), key=len)
        return long_.startswith(short) and len(short) / len(long_) >= 0.8

    names = [geo] + ([local] if local and local != geo else [])
    tried = set()
    for g, field, level in [(g, f, l) for f, l in (("district", "admin2"), ("region", "admin1")) for g in names]:
        region = g.get("region", "")
        for q in _variants(g.get(field, "")):
            if q in tried:
                continue
            tried.add(q)
            for r in _search(q):
                if not same_country(r):
                    continue
                if (level == "admin2" and r.get("admin2") and close(r["admin2"], q)
                        and (not region or close(r.get("admin1", ""), region))):
                    return r
                if level == "admin1" and not r.get("admin2") and close(r.get("admin1", ""), q):
                    return r
    for r in _search(country):  # whole-country fallback
        if same_country(r) and not r.get("admin1"):
            return r
    return None


def probability_sentence(text):
    text = re.sub(r"\s+", " ", (text or "").replace("**", ""))
    m = re.search(r"This means that (.+?(?<! c)\.)(?=\s|$)", text)
    if not m:
        return ""
    s = m.group(1).strip()
    s = s.replace("in your project area", "in this area").replace("your project area", "this area")
    return s[0].upper() + s[1:]


def thinkhazard(geo, lat, lon):
    local = reverse_geocode(lat, lon, lang="")
    div = find_division(geo, local)
    if not div:
        return None
    report = th_get(TH_REPORT % div["code"])
    hazards = []
    for h in report:
        code = h["hazardtype"]["mnemonic"]
        lvl = h["hazardlevel"]["mnemonic"]
        item = {"code": code, "name": h["hazardtype"]["hazardtype"],
                "level": h["hazardlevel"]["title"], "rank": LEVEL_RANK.get(lvl, 0),
                "tip": TRAVEL_TIP.get(code, "")}
        if item["rank"] >= 2:  # fetch the probability text for Low and above
            try:
                d = th_get(TH_DETAIL % (div["code"], code))
                item["probability"] = probability_sentence(d.get("hazard_category", {}).get("general_recommendation", ""))
            except Exception:
                pass
        hazards.append(item)
    hazards.sort(key=lambda x: -x["rank"])
    area = ", ".join(x for x in (div.get("admin2"), div.get("admin1"), div.get("admin0")) if x)
    return {"area": area, "url": div.get("url") or TH_PAGE % div["code"], "hazards": hazards}


def _window(start, end, today):
    """(month, day) pairs of the trip; without dates, the next 14 days."""
    if not start:
        start, end = today, today + dt.timedelta(days=13)
    end = end or start + dt.timedelta(days=13)
    if (end - start).days > 92:
        end = start + dt.timedelta(days=92)
    days, d = set(), start
    while d <= end:
        days.add((d.month, d.day))
        d += dt.timedelta(days=1)
    return days, start, end


def climate(lat, lon, start, end, today, years=20):
    window, ws, we = _window(start, end, today)
    first = dt.date(today.year - years, 1, 1)
    last = dt.date(today.year - 1, 12, 31)
    d = fetch_json(ARCHIVE % (lat, lon, first.isoformat(), last.isoformat()), timeout=90)["daily"]
    rows = []
    for i, t in enumerate(d["time"]):
        day = dt.date.fromisoformat(t)
        if (day.month, day.day) in window:
            rows.append({k: d[k][i] for k in d})
    n = len(rows)
    if not n:
        return None

    def pct(field, thr):
        vals = [r[field] for r in rows if r[field] is not None]
        return round(100.0 * sum(1 for v in vals if v >= thr) / max(1, len(vals)))

    def avg(field):
        vals = [r[field] for r in rows if r[field] is not None]
        return round(sum(vals) / max(1, len(vals)), 1)

    return {
        "window": "%s to %s" % (ws.strftime("%d %b"), we.strftime("%d %b")),
        "years": "%d-%d" % (first.year, last.year),
        "days_sampled": n,
        "avg_max_c": avg("temperature_2m_max"),
        "avg_rain_mm": avg("precipitation_sum"),
        "pct_feels_35": pct("apparent_temperature_max", 35),
        "pct_feels_41": pct("apparent_temperature_max", 41),
        "pct_rain_20": pct("precipitation_sum", 20),
        "pct_rain_50": pct("precipitation_sum", 50),
        "pct_gust_60": pct("wind_gusts_10m_max", 60),
    }


def poisson(rate_per_year, days):
    return 1 - math.exp(-rate_per_year * days / 365.0)


def quake_odds(lat, lon, trip_days, since_year=1976):
    years = now_utc().year - since_year
    near = fetch_json(USGS_COUNT % (lat, lon, 300, 6.0, "%d-01-01" % since_year))["count"]
    far = fetch_json(USGS_COUNT % (lat, lon, 1000, 7.0, "%d-01-01" % since_year))["count"]
    r_near, r_far = near / years, far / years
    return {
        "since": since_year,
        "m6_300km": near, "m7_1000km": far,
        "p_year_m6_300km": round(100 * poisson(r_near, 365), 1),
        "p_trip_m6_300km": round(100 * poisson(r_near, trip_days), 2),
        "p_trip_m7_1000km": round(100 * poisson(r_far, trip_days), 2),
        "trip_days": trip_days,
    }


def tsunami_history(lat, lon, radius=1000):
    hist = load_json(HISTORY_FILE, [])
    near = [h for h in hist if haversine_km(h["lat"], h["lon"], lat, lon) <= radius]
    deadly = [h for h in near if h.get("deaths")]
    worst = max(deadly, key=lambda h: h["deaths"]) if deadly else None
    return {
        "radius_km": radius, "count": len(near), "deadly": len(deadly),
        "worst": ({"year": worst["y"], "place": worst["place"], "deaths": worst["deaths"]} if worst else None),
    }


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_CACHE = {}


def _data(name):
    if name not in _CACHE:
        _CACHE[name] = load_json(os.path.join(ROOT, "data", name), {"rows": []})
    return _CACHE[name]


def _complete_years(d):
    first, last = d.get("first_year", 1980), d.get("last_year", 1980)
    if last >= now_utc().year:
        last = now_utc().year - 1  # current season incomplete
    return first, last


def season_months(month_counts, share=0.9, min_storms=10):
    """Shortest run of consecutive months (wrapping the year) holding `share` of
    the storms. Empty when storms are too rare to speak of a season."""
    total = sum(month_counts)
    if total < min_storms:
        return []
    for length in range(1, 13):
        best = None
        for start in range(12):
            got = sum(month_counts[(start + i) % 12] for i in range(length))
            if got >= share * total and (best is None or got > best[0]):
                best = (got, start)
        if best:
            return [MONTHS[(best[1] + i) % 12] for i in range(length)]
    return MONTHS[:]


def _padded(start, end, today, pad=15):
    """Trip window widened by `pad` days each side (a 2-week window alone is
    too small a sample for rare events). Returns (window set, trip_days, padded_days)."""
    _, ws, we = _window(start, end, today)
    trip_days = (we - ws).days + 1
    window, _, _ = _window(ws - dt.timedelta(days=pad), we + dt.timedelta(days=pad), today)
    return window, trip_days, len(window), ws, we


def cyclone_odds(lat, lon, start, end, today, radius=200):
    """Chance that a tropical storm / hurricane passes within `radius` km during
    the trip, from NOAA IBTrACS 6-hourly tracks since 1980. Rate = storms seen
    within the trip dates +/- 15 days, per day, scaled to the trip length."""
    d = _data("cyclone_tracks.json")
    first, last = _complete_years(d)
    window, trip_days, pad_days, ws, we = _padded(start, end, today)
    storms_ts, storms_hu, storms_near, monthly = set(), set(), set(), [0] * 12
    seen_month = set()
    dlat = radius / 111.0 + 0.5
    for la10, lo10, ymd, kt, storm in d["rows"]:
        la, lo = la10 / 10.0, lo10 / 10.0
        if abs(la - lat) > 6 or not first <= ymd // 10000 <= last:
            continue
        dist = haversine_km(la, lo, lat, lon)
        m, dd = ymd // 100 % 100, ymd % 100
        if dist <= 500 and (storm, m) not in seen_month:
            seen_month.add((storm, m))
            monthly[m - 1] += 1
        if abs(la - lat) > dlat or dist > radius:
            continue
        storms_near.add(storm)
        if (m, dd) in window:
            storms_ts.add(storm)
            if kt >= 64:
                storms_hu.add(storm)
    n = last - first + 1

    def p_trip(count):
        rate_per_day = count / float(n * pad_days)
        return round(100 * (1 - math.exp(-rate_per_day * trip_days)), 1)

    season = season_months(monthly)
    trip_months = {ws.month, we.month}
    return {
        "radius_km": radius, "years": "%d-%d" % (first, last), "n_years": n,
        "window": "%s to %s" % (ws.strftime("%d %b"), we.strftime("%d %b")),
        "storms_within_radius": len(storms_near),
        "storms_near_dates": len(storms_ts), "hurricanes_near_dates": len(storms_hu),
        "p_trip_storm": p_trip(len(storms_ts)),
        "p_trip_hurricane": p_trip(len(storms_hu)),
        "season": season,
        "trip_in_season": bool(season) and any(MONTHS[m - 1] in season for m in trip_months),
        "storms_500km_total": sum(monthly),
    }


def tornado_odds(lat, lon, start, end, today, radius=50):
    """US only: tornadoes that touched down within `radius` km since 1990 (NOAA SPC)."""
    if not in_us(lat, lon):
        return None
    d = _data("us_tornadoes.json")
    first, last = _complete_years(d)
    window, ws, we = _window(start, end, today)
    n = last - first + 1
    total = strong = 0
    years_trip, trip_count = set(), 0
    for la100, lo100, ymd, ef in d["rows"]:
        la, lo = la100 / 100.0, lo100 / 100.0
        if abs(la - lat) > 1 or not first <= ymd // 10000 <= last:
            continue
        if haversine_km(la, lo, lat, lon) > radius:
            continue
        total += 1
        strong += ef >= 2
        if (ymd // 100 % 100, ymd % 100) in window:
            trip_count += 1
            years_trip.add(ymd // 10000)
    return {
        "radius_km": radius, "years": "%d-%d" % (first, last), "n_years": n,
        "per_year": round(total / n, 1), "strong_ef2plus": strong,
        "window": "%s to %s" % (ws.strftime("%d %b"), we.strftime("%d %b")),
        "in_window": trip_count, "p_trip": round(100.0 * len(years_trip) / n, 1),
    }


def build(loc, today=None):
    today = today or dt.date.today()
    start, end = trip_dates(loc)
    trip_days = ((end - start).days + 1) if (start and end) else 14
    p = {"name": loc["name"], "lat": loc["lat"], "lon": loc["lon"],
         "from": loc.get("from"), "to": loc.get("to"), "coastal": loc.get("coastal", True),
         "updated": iso(now_utc()), "errors": []}
    geo = reverse_geocode(loc["lat"], loc["lon"])
    p["country"], p["region"] = geo.get("country", ""), geo.get("region", "")
    for key, fn in (("thinkhazard", lambda: thinkhazard(geo, loc["lat"], loc["lon"]) if geo else None),
                    ("climate", lambda: climate(loc["lat"], loc["lon"], start, end, today)),
                    ("quakes", lambda: quake_odds(loc["lat"], loc["lon"], trip_days)),
                    ("tsunamis", lambda: tsunami_history(loc["lat"], loc["lon"])),
                    ("cyclones", lambda: cyclone_odds(loc["lat"], loc["lon"], start, end, today)),
                    ("tornadoes", lambda: tornado_odds(loc["lat"], loc["lon"], start, end, today))):
        try:
            p[key] = fn()
        except Exception as e:
            p[key] = None
            p["errors"].append("%s: %s" % (key, e))
    return p


def same_place(a, b):
    return (a.get("name") == b.get("name") and abs(a["lat"] - b["lat"]) < 1e-4
            and abs(a["lon"] - b["lon"]) < 1e-4)


def needs_refresh(p, loc):
    if p.get("from") != loc.get("from") or p.get("to") != loc.get("to") or p.get("errors"):
        return True
    return (now_utc() - safe_time(p.get("updated"))).days >= REFRESH_DAYS


def refresh_public(force=False):
    """Keep data/risk_profiles.json in sync with config/locations.json.
    Returns True if the file changed."""
    locs = public_locations()
    old = load_json(PROFILES_FILE, {"profiles": []}).get("profiles", [])
    out, changed = [], False
    for loc in locs:
        prev = next((p for p in old if same_place(p, loc)), None)
        if prev and not force and not needs_refresh(prev, loc):
            out.append(prev)
            continue
        print("Building risk profile: %s" % loc["name"])
        out.append(build(loc))
        changed = True
    if len(out) != len(old):
        changed = True
    if changed:
        save_json(PROFILES_FILE, {"profiles": out})
    return changed


# ---------------------------------------------------------------- text

def pct_text(v):
    return "under 0.1%" if v < 0.1 else "about %s%%" % (round(v, 1) if v < 10 else round(v))


def season_text(months):
    if not months:
        return "storms are rare here"
    return "all year" if len(months) == 12 else "%s-%s" % (months[0], months[-1])


def describe(p, max_hazards=8):
    """Readable briefing text (for push notifications and the CLI)."""
    lines = ["%s (%s)" % (p["name"], ", ".join(x for x in (p.get("region"), p.get("country")) if x))]
    th = p.get("thinkhazard")
    if th:
        top = [h for h in th["hazards"] if h["rank"] >= 3][:max_hazards]
        if top:
            lines.append("Main hazards (%s):" % th["area"])
            for h in top:
                lines.append("- %s: %s. %s" % (h["name"], h["level"].upper(), h.get("probability", "")))
        else:
            lines.append("No high or medium hazards listed for %s." % th["area"])
    c = p.get("climate")
    if c:
        lines.append("Weather on %s (%s): avg high %.0f°C; %d%% of days felt 35°C+, %d%% had 20 mm+ rain, %d%% had gusts 60 km/h+."
                     % (c["window"], c["years"], c["avg_max_c"], c["pct_feels_35"], c["pct_rain_20"], c["pct_gust_60"]))
    q = p.get("quakes")
    if q:
        lines.append("Quakes since %d: %d of M6+ within 300 km, %d of M7+ within 1000 km. Chance of an M6+ within 300 km during a %d-day stay: %s."
                     % (q["since"], q["m6_300km"], q["m7_1000km"], q["trip_days"], pct_text(q["p_trip_m6_300km"])))
    cy = p.get("cyclones")
    if cy and cy["storms_500km_total"]:
        lines.append("Tropical storms/hurricanes: chance one passes within %d km during your dates (%s): %s, hurricane-strength %s (%s storms around these dates since %s). Season here: %s%s."
                     % (cy["radius_km"], cy["window"], pct_text(cy["p_trip_storm"]), pct_text(cy["p_trip_hurricane"]),
                        cy["storms_near_dates"], cy["years"][:4], season_text(cy["season"]),
                        " - your dates are IN season" if cy["trip_in_season"] else ""))
    elif cy is not None:
        lines.append("Tropical storms/hurricanes: none passed within 500 km since 1980.")
    tn = p.get("tornadoes")
    if tn:
        lines.append("Tornadoes within %d km (%s): %.1f per year, %d strong (EF2+). On %s: %d in total, in %s%% of years."
                     % (tn["radius_km"], tn["years"], tn["per_year"], tn["strong_ef2plus"], tn["window"], tn["in_window"], tn["p_trip"]))
    t = p.get("tsunamis")
    if t and p.get("coastal", True) and t["count"]:
        w = t["worst"]
        lines.append("Tsunamis since 1900 from sources within %d km: %d (%d deadly%s)." % (t["radius_km"],
            t["count"], t["deadly"], (", worst %d %s, %s deaths" % (w["year"], w["place"], "{:,}".format(w["deaths"]))) if w else ""))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-location", help='"Name,lat,lon" - print a profile, save nothing')
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--force", action="store_true", help="rebuild all public profiles")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.test_location:
        loc = parse_test_location(args.test_location)[0]
        loc.update({k: v for k, v in (("from", args.start), ("to", args.end)) if v})
        p = build(loc)
        if args.json:
            import json
            print(json.dumps(p, indent=2, ensure_ascii=False))
        print(describe(p))
        if p["errors"]:
            print("Errors: %s" % p["errors"], file=sys.stderr)
        return
    print("risk_profiles.json %s" % ("updated" if refresh_public(args.force) else "up to date"))


if __name__ == "__main__":
    main()
