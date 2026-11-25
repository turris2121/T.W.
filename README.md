# ≋ Tsunami Watch

A free family safety tool for travel: a live map of earthquakes and tsunami alerts, phone notifications when something serious happens near the places you're staying, and clear "what to do" guidance that works offline.

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
| `checker/check.py` | Runs every 5 min on GitHub Actions. Reads USGS, NOAA (PTWC/NTWC) and GDACS, and pushes alerts through ntfy. |
| `config/locations.json` | The places to watch. |

**Alert rules** (per place):
- 🔴 **Urgent:** M6.5+ shallow quake within 1000 km of a coastal place, M7.5+ within 3000 km, any official NOAA tsunami warning/advisory/watch/threat, or a GDACS Red alert.
- 🟠 **High:** M8+ within 12 000 km (ocean-crossing), M5.5+ within 200 km (shaking), USGS tsunami flag, or GDACS Orange.
- Each event is sent once.

Data sources: [USGS](https://earthquake.usgs.gov/), [NOAA tsunami.gov](https://www.tsunami.gov/), [GDACS](https://www.gdacs.org/), [NOAA NCEI historical tsunami database](https://www.ngdc.noaa.gov/hazard/tsu_db.shtml). Map © OpenStreetMap.

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
   - *(optional, recommended)* `LOCATIONS_JSON`: your real trip list, same format as `config/locations.json`. This keeps your itinerary **out of the public repo**. If set, it overrides the file for alerts, and you can leave only generic places in the file (the map shows the file's places).
4. **Test:** *Actions → Tsunami watch → Run workflow → tick "Send a test notification"*. Your phones should buzz.

### Editing places
Edit `config/locations.json` (or the `LOCATIONS_JSON` secret) directly on GitHub:
```json
{ "name": "Khao Lak hotel", "lat": 8.64, "lon": 98.25, "coastal": true, "from": "2026-12-20", "to": "2027-01-03" }
```
- `coastal: false` for inland places (only shaking alerts).
- Dates are optional. A place is watched from 2 days before `from` until 1 day after `to`. Without dates it's watched always.
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
python3 checker/fetch_history.py                                     # refresh historical data
python3 -m http.server 8790                                          # view the site at http://localhost:8790
```
