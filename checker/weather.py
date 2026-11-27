"""Weather extremes from the Open-Meteo forecast (free, no key)."""

import datetime as dt

from common import fetch_json

FORECAST = ("https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
            "&daily=temperature_2m_max,temperature_2m_min,apparent_temperature_max,"
            "precipitation_sum,wind_gusts_10m_max,wind_speed_10m_max,weather_code&forecast_days=16&timezone=auto")

# (field, op, threshold, level, label, advice)
RULES = [
    ("apparent_temperature_max", ">=", 46, 5, "Extreme heat (feels like %.0f°C)", "Stay in shade/AC 11:00-16:00, drink water often, watch for heat stroke."),
    ("apparent_temperature_max", ">=", 41, 4, "Dangerous heat (feels like %.0f°C)", "Avoid midday sun, drink water often, rest in cool places."),
    ("temperature_2m_min", "<=", -20, 5, "Extreme cold (%.0f°C)", "Limit time outdoors; frostbite in minutes."),
    ("temperature_2m_min", "<=", -12, 4, "Severe cold (%.0f°C)", "Dress in layers, cover skin, check road conditions."),
    ("wind_gusts_10m_max", ">=", 118, 5, "Hurricane-force gusts (%.0f km/h)", "Shelter in a strong building away from windows; follow local authorities."),
    ("wind_gusts_10m_max", ">=", 90, 4, "Damaging wind gusts (%.0f km/h)", "Avoid trees, coastline and boats; secure loose objects."),
    ("wind_gusts_10m_max", ">=", 70, 3, "Strong wind gusts (%.0f km/h)", "Boat trips and ferries may be cancelled; dangerous surf and rip currents; beware falling branches."),
    ("wind_speed_10m_max", ">=", 89, 4, "Storm-force sustained winds (%.0f km/h)", "Stay indoors away from windows; don't drive high-sided vehicles."),
    ("wind_speed_10m_max", ">=", 62, 3, "Gale-force winds (%.0f km/h)", "Rough seas: avoid boats, kayaks and swimming; walking can be difficult."),
    ("weather_code", "in", (96, 99), 3, "Thunderstorms with hail%.0s", "Go indoors when you hear thunder; stay off beaches, water and hilltops; hail can damage cars."),
    ("precipitation_sum", ">=", 200, 5, "Extreme rain (%.0f mm/day)", "Flash floods and landslides likely. Avoid rivers, valleys, slopes and driving through water."),
    ("precipitation_sum", ">=", 100, 4, "Very heavy rain (%.0f mm/day)", "Flash flood risk. Stay away from rivers and steep slopes."),
]


def get_forecast(lat, lon):
    d = fetch_json(FORECAST % (lat, lon))["daily"]
    days = []
    for i, day in enumerate(d["time"]):
        days.append({k: d[k][i] for k in d})
    return days


def extremes(days, start=None, end=None, horizon_days=3, today=None):
    """Worst condition per day within the next `horizon_days` (forecast is
    reliable) and within the trip window if one is given."""
    today = today or dt.date.today()
    out = []
    for day in days:
        date = dt.date.fromisoformat(day["time"])
        if date < today or date > today + dt.timedelta(days=horizon_days):
            continue
        if (start and date < start) or (end and date > end):
            continue
        matched = set()
        for field, op, thr, level, label, advice in RULES:  # ordered worst first per field
            v = day.get(field)
            if v is None or field in matched:
                continue
            if (op == ">=" and v >= thr) or (op == "<=" and v <= thr) or (op == "in" and v in thr):
                matched.add(field)
                out.append({"date": day["time"], "field": field, "level": level,
                            "label": label % v, "advice": advice})
    return sorted(out, key=lambda e: (e["date"], -e["level"]))


def summary(days, n=7):
    """One-line outlook for briefings."""
    ds = days[:n]
    if not ds:
        return "No forecast."
    tmax = max(d["temperature_2m_max"] for d in ds if d["temperature_2m_max"] is not None)
    tmin = min(d["temperature_2m_min"] for d in ds if d["temperature_2m_min"] is not None)
    rain_days = sum(1 for d in ds if (d["precipitation_sum"] or 0) >= 5)
    gust = max((d["wind_gusts_10m_max"] or 0) for d in ds)
    return "Next %d days: %.0f to %.0f°C, %d rainy day(s), gusts up to %.0f km/h." % (len(ds), tmin, tmax, rain_days, gust)
