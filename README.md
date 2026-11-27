# ≋ Tsunami Watch (T.W.)

A free family safety tool for travel:
- **Live map** of earthquakes, tsunami alerts and current disasters: hurricane/typhoon tracks with forecast wind zones, floods, volcanoes, wildfires.
- **Phone notifications** when something serious happens near the places you're staying: quakes, tsunamis, disasters, dangerous weather, disease outbreaks.
- **Risk profile** for each place: how likely each hazard is in that region, the usual weather on your trip dates, and earthquake and tsunami history.
- A **pre-trip briefing** pushed 3 days before you leave.
- Clear "what to do" guidance that works offline.

> **Important:** nobody can predict earthquakes. This app *detects* them minutes after they happen and relays official tsunami bulletins. Alerts can be 5–30 minutes late. **Strong or long shaking near the coast, or the sea pulling back = go to high ground immediately.** Don't wait for any app.

---

## For family members (2 minutes)

1. **Open the map** on your phone: `https://<owner>.github.io/tsunami-watch/`
   - iPhone: Share → *Add to Home Screen*. Android: menu → *Install app*.
2. **Get alerts:** install the free **ntfy** app ([iOS](https://apps.apple.com/app/ntfy/id1625396347) / [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)), tap **+**, and subscribe to the topic name the owner sends you privately.
   - iPhone: allow notifications, and turn on *Time Sensitive / Critical* if offered.
3. **Turn on government emergency alerts** on your phone (Settings → Notifications → Emergency/Government alerts).
4. Read **What to do** (red button) once before the trip.

## How it works

| Part | What it does |
|---|---|
| `index.html` + `app.js` | Map: live USGS quakes (refreshes every minute), your places with 200 km / 1000 km rings, past tsunamis since 1900, "check where I am". |
| `safety.html` | Guidance + emergency numbers. Cached for offline use. |
| `checker/check.py` | Runs every 5 min on GitHub Actions: USGS, NOAA (PTWC/NTWC), GDACS. Hourly: CDC, WHO, Open-Meteo, volcano reports. Pushes alerts through ntfy. |
| `checker/risk_profile.py` | Builds `data/risk_profiles.json` (refreshed weekly or when places change). |
| `checker/sources.py`, `health.py`, `weather.py`, `common.py` | Feed readers and helpers. |
| `config/locations.json` | The places to watch. |

**Alert rules** (per active place):

| Hazard | 🔴 Urgent | 🟠 High | ℹ️ Normal |
|---|---|---|---|
| Earthquake / tsunami | M6.5+ shallow ≤1000 km (coastal), M7.5+ ≤3000 km, official NOAA tsunami warning/watch/advisory | M8+ ≤12 000 km, M5.5+ ≤200 km, USGS tsunami flag | NOAA info statement M6.5+ |
| Hurricane / typhoon / cyclone (forecast wind zones, all oceans) | Place inside the 120+ km/h zone | Inside the 90+ km/h zone | Inside the 60+ km/h zone |
| US only: NWS warnings (checked every 5 min) | Tornado, Hurricane, Extreme Wind, Storm Surge warning | Tornado/Hurricane watch, Tropical Storm, Severe Thunderstorm, High Wind warning | Wind Advisory, Severe Thunderstorm/Tropical Storm watch |
| US only: SPC tornado outlook (today, tomorrow) | 30%+ chance within 40 km | 10%+ | 5%+ |
| Flood / volcano / wildfire (GDACS) | Red alert within 300 / 300 / 100 km | Orange alert, same distances | |
| Volcano weekly report | | New eruption ≤100 km | New unrest ≤50 km |
| Weather (next 3 days, trip dates) | Feels ≥46 °C, ≤−20 °C, gusts ≥118 km/h, rain ≥200 mm/day | Feels ≥41 °C, ≤−12 °C, gusts ≥90 km/h, sustained wind ≥89 km/h, rain ≥100 mm/day | Gusts ≥70 km/h, gale ≥62 km/h, thunderstorms with hail |
| Health (trip country) | | CDC Level 3–4 | CDC Level 2, WHO outbreak news |

Each event is sent once. The **pre-trip briefing** arrives 3 days before each `from` date.

**Risk profile** (tap "Risk profile" on a place card):
- [ThinkHazard!](https://thinkhazard.org) (World Bank GFDRR) rates 11 hazards per province (river, urban and coastal flood, earthquake, landslide, tsunami, volcano, cyclone, water scarcity, extreme heat, wildfire) as High, Medium, Low or Very low, with the probability behind each level (e.g. "10% chance of potentially-damaging earthquake shaking in the next 50 years").
- Open-Meteo archive: how often your trip dates were very hot, very wet or windy over the past 20 years.
- USGS: M6+ quakes within 300 km since 1976, giving the chance of one during your stay.
- NOAA NCEI: tsunamis since 1900 from sources within 1000 km.
- NOAA IBTrACS (every storm track since 1980): chance a tropical storm or hurricane passes within 200 km during your dates, and the local season.
- US only, NOAA SPC tornado records since 1990: tornadoes per year within 50 km, and how often one happened around your dates.

These are long-term regional odds, not forecasts for your exact days.

**Not covered** (no global live data, or no risk to travelers): locusts, crop and livestock diseases, algal blooms, sinkholes, avalanches, and tornado warnings outside the US (no other country publishes them openly; strong-wind and hail-storm forecasts still apply everywhere). The safety page has tips for these.

Data sources: [USGS](https://earthquake.usgs.gov/), [NOAA tsunami.gov](https://www.tsunami.gov/), [GDACS](https://www.gdacs.org/), [Smithsonian GVP](https://volcano.si.edu/), [NWS](https://www.weather.gov/), [SPC](https://www.spc.noaa.gov/), [IBTrACS](https://www.ncei.noaa.gov/products/international-best-track-archive), [CDC Travelers' Health](https://wwwnc.cdc.gov/travel/notices), [WHO Disease Outbreak News](https://www.who.int/emergencies/disease-outbreak-news), [Open-Meteo](https://open-meteo.com/), [ThinkHazard!](https://thinkhazard.org/), [NOAA NCEI](https://www.ngdc.noaa.gov/hazard/tsu_db.shtml), reverse geocoding by [OpenStreetMap Nominatim](https://nominatim.org/). Map © OpenStreetMap.

---

## Owner setup (one time)

1. **Create the repo:** on github.com → *New repository* → name `tsunami-watch`, **Public** (free GitHub Pages needs a public repo). Then here:
   ```sh
   cd ~/tsunami-watch
   git remote add origin https://github.com/<you>/tsunami-watch.git
   git push -u origin main
   ```
2. **Pages:** repo *Settings → Pages → Source: GitHub Actions*.
3. **Secrets:** *Settings → Secrets and variables → Actions → New repository secret*:
   - `NTFY_TOPIC`: your private, hard-to-guess topic (e.g. `family-tsunami-<random>`). Anyone who knows it can read the alerts, so share it only with family.
   - *(optional, recommended)* `LOCATIONS_JSON`: your real trip list, same format as `config/locations.json`. This keeps your itinerary **out of the public repo**. If set, it overrides the file for alerts, and you can leave only generic places in the file (the map shows the file's places). Secret-only places get their risk profile in the pre-trip briefing push; they are never written to the repo.
4. **Test:** *Actions → Tsunami watch → Run workflow → tick "Send a test notification"*. Your phones should buzz.

### Editing places
Edit `config/locations.json` (or the `LOCATIONS_JSON` secret) directly on GitHub:
```json
{ "name": "Khao Lak hotel", "lat": 8.64, "lon": 98.25, "coastal": true, "from": "2026-12-20", "to": "2027-01-03" }
```
- `coastal: false` for inland places (only shaking alerts).
- Dates are optional. A place is watched from 2 days before `from` until 1 day after `to`. Without dates it's watched always.
- Optional `"country": "Thailand"` skips the automatic country lookup used for health notices.
- Get coordinates: in Google Maps, long-press a spot and copy the numbers.

On the map, anyone can also long-press to add places **on their own phone** (no alerts for those).

### Keep it running
- GitHub pauses scheduled workflows in public repos after **60 days without activity**. If you get that email, click *Enable workflow* (or push any change before a trip).
- Run *Actions → Tsunami watch → Run workflow* before each trip to confirm it's working.

### Local commands
```sh
python3 checker/check.py --dry-run --hours 168                       # what would be sent
python3 checker/check.py --dry-run --test-location "Khao Lak,8.64,98.25" --hours 168
NTFY_TOPIC=your-topic python3 checker/check.py --force-test          # test push
python3 checker/check.py --briefing --test-location "Khao Lak,8.64,98.25"   # pre-trip briefing text
python3 checker/risk_profile.py --test-location "Bali,-8.72,115.17" --from 2027-07-01 --to 2027-07-14
python3 checker/fetch_history.py                                     # refresh historical tsunamis
python3 checker/build_climatology.py --download-dir /tmp/empty-dir   # refresh storm + tornado history (yearly, ~150 MB download)
python3 -m http.server 8790                                          # view the site at http://localhost:8790
```
