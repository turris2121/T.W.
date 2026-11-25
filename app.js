/* Tsunami Watch map. Plain JS + Leaflet, no build step. */
(function () {
  "use strict";

  var USGS = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson";
  var SPEED_KMH = 700;
  var LOCAL_KEY = "tw.places.v1";

  var map = L.map("map", { worldCopyJump: true, zoomControl: true }).setView([10, 110], 3);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 12, attribution: "&copy; OpenStreetMap contributors"
  }).addTo(map);

  var quakeLayer = L.layerGroup().addTo(map);
  var placeLayer = L.layerGroup().addTo(map);
  var histLayer = L.layerGroup();
  var meLayer = L.layerGroup().addTo(map);

  var disasterLayer = L.layerGroup().addTo(map);

  var state = { quakes: [], history: [], configPlaces: [], localPlaces: loadLocal(), me: null,
                profiles: [], alerts: {}, forecasts: {}, open: {} };
  var FORECAST = "https://api.open-meteo.com/v1/forecast?daily=temperature_2m_max,temperature_2m_min," +
    "apparent_temperature_max,precipitation_sum,wind_gusts_10m_max&forecast_days=7&timezone=auto";
  var LEVEL_CLASS = { "High": "hi", "Medium": "med", "Low": "low", "Very low": "vlow" };
  var GDACS_ICON = { TC: "🌀", FL: "🌊", VO: "🌋", WF: "🔥", DR: "☀️", EQ: "〰️", TS: "🌊" };

  // ------------------------------------------------------------ utils
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function km(a, b, c, d) {
    var r = 6371, p1 = a * Math.PI / 180, p2 = c * Math.PI / 180;
    var dp = p2 - p1, dl = (d - b) * Math.PI / 180;
    var h = Math.sin(dp / 2) * Math.sin(dp / 2) + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) * Math.sin(dl / 2);
    return 2 * r * Math.asin(Math.sqrt(h));
  }
  function ago(t) {
    var m = Math.round((Date.now() - t) / 60000);
    if (m < 60) return m + " min ago";
    if (m < 48 * 60) return Math.round(m / 60) + " h ago";
    return Math.round(m / 1440) + " days ago";
  }
  function eta(d) {
    var m = d / SPEED_KMH * 60;
    return m < 60 ? Math.max(5, Math.round(m / 5) * 5) + " min" : (m / 60).toFixed(1) + " h";
  }
  function color(t) {
    var age = Date.now() - t;
    return age < 3600e3 ? "#d7263d" : age < 86400e3 ? "#f46036" : "#e3b505";
  }
  function loadLocal() {
    try { return JSON.parse(localStorage.getItem(LOCAL_KEY)) || []; } catch (e) { return []; }
  }
  function saveLocal() {
    try { localStorage.setItem(LOCAL_KEY, JSON.stringify(state.localPlaces)); } catch (e) {}
  }
  function getJSON(url) {
    return fetch(url, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(url + " " + r.status);
      return r.json();
    });
  }

  // Same rules as checker/check.py (keep in sync)
  function risk(q, p, d) {
    var coastal = p.coastal !== false, shallow = q.depth <= 100, m = q.mag;
    if (coastal && shallow && m >= 6.5 && d <= 1000) return { lvl: 5, why: "Tsunami possible. Could arrive in as little as " + eta(d) };
    if (coastal && m >= 7.5 && d <= 3000) return { lvl: 5, why: "Ocean-crossing tsunami possible. As little as " + eta(d) };
    if (coastal && m >= 8.0 && d <= 12000) return { lvl: 4, why: "Distant tsunami possible. Check official alerts" };
    if (m >= 5.5 && d <= 200) return { lvl: 4, why: "Strong shaking possible nearby" };
    if (coastal && q.tsunami && d <= 3000) return { lvl: 4, why: "USGS flagged for tsunami evaluation" };
    return null;
  }

  // ------------------------------------------------------------ data
  function loadQuakes() {
    return getJSON(USGS).then(function (g) {
      state.quakes = g.features.filter(function (f) { return f.properties.mag != null; }).map(function (f) {
        var c = f.geometry.coordinates, p = f.properties;
        return { id: f.id, mag: p.mag, place: p.place || "Unknown", time: p.time, lon: c[0], lat: c[1],
                 depth: c[2] || 0, tsunami: !!p.tsunami, url: p.url };
      }).sort(function (a, b) { return b.time - a.time; });
      $("updated").textContent = "Quakes updated " + new Date().toLocaleTimeString() + ".";
    }).catch(function () {
      $("updated").textContent = "Could not reach USGS. Check your connection.";
    });
  }

  function loadAlerts() {
    return getJSON("data/alerts.json?" + Date.now()).then(function (a) { state.alerts = a; renderBanner(a); }).catch(function () {
      var b = $("banner"); b.className = "banner warn";
      b.textContent = "Official alert summary unavailable. Check tsunami.gov and local authorities.";
    });
  }

  function renderBanner(a) {
    var b = $("banner"), off = a.official_tsunami || [], gd = a.gdacs || [];
    if (off.length) {
      b.className = "banner alert";
      b.innerHTML = "⚠️ OFFICIAL TSUNAMI " + esc(off[0].category.toUpperCase()) + ": " + esc(off[0].region) +
        ' — <a href="' + esc(off[0].url) + '" target="_blank" rel="noopener">bulletin</a> · ' +
        '<a href="safety.html">what to do</a>' + (off.length > 1 ? " (+" + (off.length - 1) + " more)" : "");
    } else if (gd.length) {
      b.className = "banner warn";
      b.innerHTML = "GDACS " + esc(gd[0].level) + " alert: " + esc(gd[0].title) +
        ' — <a href="' + esc(gd[0].url) + '" target="_blank" rel="noopener">details</a>';
    } else {
      b.className = "banner ok";
      b.textContent = "No active tsunami warnings in the NOAA / GDACS feeds" +
        (a.updated ? " (last change " + ago(Date.parse(a.updated)) + ")" : "") + ".";
    }
  }

  function loadProfiles() {
    return getJSON("data/risk_profiles.json?" + Date.now()).then(function (d) { state.profiles = d.profiles || []; }).catch(function () {});
  }

  function profileFor(p) {
    return state.profiles.filter(function (x) {
      return x.name === p.name && Math.abs(x.lat - p.lat) < 1e-4 && Math.abs(x.lon - p.lon) < 1e-4;
    })[0];
  }

  function fkey(p) { return p.lat.toFixed(2) + "," + p.lon.toFixed(2); }

  function loadForecasts() {
    var jobs = places().map(function (p) {
      var k = fkey(p), f = state.forecasts[k];
      if (f && Date.now() - f.at < 30 * 60e3) return Promise.resolve();
      return getJSON(FORECAST + "&latitude=" + p.lat + "&longitude=" + p.lon).then(function (d) {
        state.forecasts[k] = { at: Date.now(), daily: d.daily };
      }).catch(function () {});
    });
    return Promise.all(jobs);
  }

  // Same thresholds as checker/weather.py
  function forecastLine(p) {
    var f = state.forecasts[fkey(p)];
    if (!f) return "";
    var d = f.daily, warn = [];
    for (var i = 0; i < d.time.length; i++) {
      var day = new Date(d.time[i] + "T12:00").toLocaleDateString(undefined, { weekday: "short" });
      if (d.apparent_temperature_max[i] >= 41) warn.push(day + ": dangerous heat (feels " + Math.round(d.apparent_temperature_max[i]) + "°C)");
      if (d.temperature_2m_min[i] <= -12) warn.push(day + ": severe cold (" + Math.round(d.temperature_2m_min[i]) + "°C)");
      if (d.wind_gusts_10m_max[i] >= 90) warn.push(day + ": damaging gusts " + Math.round(d.wind_gusts_10m_max[i]) + " km/h");
      if (d.precipitation_sum[i] >= 100) warn.push(day + ": very heavy rain " + Math.round(d.precipitation_sum[i]) + " mm");
    }
    var hi = Math.max.apply(null, d.temperature_2m_max), lo = Math.min.apply(null, d.temperature_2m_min);
    var rainy = d.precipitation_sum.filter(function (x) { return x >= 5; }).length;
    return '<div class="s">7 days: ' + Math.round(lo) + "–" + Math.round(hi) + "°C, " + rainy + " rainy day" + (rainy === 1 ? "" : "s") + "</div>" +
      (warn.length ? '<div class="s warnline">⚠️ ' + esc(warn.join(" · ")) + "</div>" : "");
  }

  function healthFor(country) {
    if (!country) return [];
    var c = country.toLowerCase();
    return (state.alerts.health || []).filter(function (n) {
      return !n.global && n.subject.toLowerCase().indexOf(c) >= 0;
    });
  }

  function pct(v) { return v < 0.1 ? "under 0.1%" : (v < 10 ? v.toFixed(1) : Math.round(v)) + "%"; }

  function profileHTML(p) {
    var pr = profileFor(p);
    if (!pr) {
      return p.src === "shared" ? '<div class="s">Risk profile is being prepared (updates within the hour).</div>' : "";
    }
    var h = [], th = pr.thinkhazard;
    if (th) {
      var chips = th.hazards.filter(function (x) { return x.rank >= 2; }).map(function (x) {
        return '<span class="chip ' + (LEVEL_CLASS[x.level] || "") + '" title="' + esc(x.probability || "") + '">' + esc(x.name) + " · " + esc(x.level) + "</span>";
      }).join("");
      h.push('<div class="chips">' + (chips || '<span class="s">No notable hazards listed.</span>') + "</div>");
      var top = th.hazards.filter(function (x) { return x.rank >= 3; });
      if (top.length) {
        h.push('<ul class="haz">' + top.map(function (x) {
          return "<li><b>" + esc(x.name) + " (" + esc(x.level.toLowerCase()) + ")</b>: " + esc(x.probability || "") +
            (x.tip ? ' <span class="tip">' + esc(x.tip) + "</span>" : "") + "</li>";
        }).join("") + "</ul>");
      }
    }
    var c = pr.climate;
    if (c) {
      h.push("<p><b>Weather " + esc(c.window) + "</b> (past 20 years): avg high " + Math.round(c.avg_max_c) + "°C · " +
        c.pct_feels_35 + "% of days felt 35°C+ · " + c.pct_rain_20 + "% had heavy rain (20 mm+) · " + c.pct_gust_60 + "% strong gusts</p>");
    }
    var q = pr.quakes;
    if (q) {
      h.push("<p><b>Earthquakes</b> since " + q.since + ": " + q.m6_300km + " of M6+ within 300 km. Chance of one during a " + q.trip_days +
        "-day stay: " + pct(q.p_trip_m6_300km) + " (" + pct(q.p_year_m6_300km) + " per year).</p>");
    }
    var t = pr.tsunamis;
    if (t && pr.coastal !== false && t.count) {
      h.push("<p><b>Tsunamis</b> since 1900 from sources within " + t.radius_km + " km: " + t.count + " (" + t.deadly + " deadly" +
        (t.worst ? "; worst " + t.worst.year + ", " + esc(t.worst.place) + ", " + t.worst.deaths.toLocaleString() + " deaths" : "") + ").</p>");
    }
    var hn = healthFor(pr.country);
    if (hn.length) {
      h.push("<p><b>Health notices for " + esc(pr.country) + ":</b></p><ul class=\"haz\">" + hn.slice(0, 5).map(function (n) {
        return '<li><a href="' + esc(n.url) + '" target="_blank" rel="noopener">' + esc(n.title) + "</a></li>";
      }).join("") + "</ul>");
    }
    if (th) h.push('<p class="s">Area: ' + esc(th.area) + ' · source <a href="' + esc(th.url) + '" target="_blank" rel="noopener">ThinkHazard</a>, Open-Meteo, USGS, NOAA, CDC, WHO</p>');
    var worst = th && th.hazards[0] && th.hazards[0].rank >= 4 ? th.hazards.filter(function (x) { return x.rank >= 4; }).length + " high" : "";
    return '<details class="profile"' + (state.open[p.name] ? " open" : "") + '><summary>Risk profile' + (worst ? " · " + worst : "") + "</summary>" + h.join("") + "</details>";
  }

  function renderDisasters() {
    disasterLayer.clearLayers();
    if (!$("disToggle").checked) return;
    (state.alerts.gdacs || []).forEach(function (g) {
      if (g.type === "EQ") return; // USGS layer shows quakes
      var col = g.level === "Red" ? "#c8102e" : g.level === "Orange" ? "#f46036" : "#1f7a4d";
      L.marker([g.lat, g.lon], { icon: L.divIcon({ className: "dis", html: '<span style="border-color:' + col + '">' + (GDACS_ICON[g.type] || "⚠️") + "</span>", iconSize: [28, 28] }) })
        .bindPopup("<b>" + esc(g.kind) + " · " + esc(g.level) + "</b><br>" + esc(g.title) + '<br><a href="' + esc(g.url) + '" target="_blank" rel="noopener">GDACS report</a>')
        .addTo(disasterLayer);
    });
    (state.alerts.volcanoes || []).forEach(function (v) {
      L.marker([v.lat, v.lon], { icon: L.divIcon({ className: "dis", html: '<span style="border-color:#7a3e00">🌋</span>', iconSize: [28, 28] }) })
        .bindPopup("<b>" + esc(v.volcano) + "</b> (" + esc(v.country) + ")<br>" + esc(v.status) + '<br><a href="' + esc(v.url) + '" target="_blank" rel="noopener">Weekly volcano report</a>')
        .addTo(disasterLayer);
    });
  }

  function loadHistory() {
    return getJSON("data/historical_tsunamis.json").then(function (h) { state.history = h; }).catch(function () {});
  }

  // ------------------------------------------------------------ render
  function places() {
    var list = state.configPlaces.map(function (p) { return Object.assign({ src: "shared" }, p); })
      .concat(state.localPlaces.map(function (p) { return Object.assign({ src: "local" }, p); }));
    if (state.me) list.unshift(state.me);
    return list;
  }

  function analyse(p) {
    var dayAgo = Date.now() - 86400e3, best = null;
    state.quakes.forEach(function (q) {
      if (q.time < dayAgo) return;
      var d = km(q.lat, q.lon, p.lat, p.lon), r = risk(q, p, d);
      if (r && (!best || r.lvl > best.r.lvl || (r.lvl === best.r.lvl && d < best.d))) best = { q: q, d: d, r: r };
    });
    var past = state.history.filter(function (h) { return km(h.lat, h.lon, p.lat, p.lon) <= 500; });
    var deadly = past.filter(function (h) { return h.deaths; }).length;
    return { best: best, past: past.length, deadly: deadly };
  }

  function renderPlaces() {
    placeLayer.clearLayers();
    var ul = $("places"); ul.innerHTML = "";
    var ps = places();
    if (!ps.length) ul.innerHTML = '<li class="s">No places yet. Tap "Check where I am" or add one.</li>';
    ps.forEach(function (p, i) {
      var a = analyse(p), li = document.createElement("li");
      var status = a.best
        ? "M" + a.best.q.mag.toFixed(1) + " " + Math.round(a.best.d) + " km away, " + ago(a.best.q.time) + ". " + a.best.r.why
        : "No strong quakes nearby in the last 24 h.";
      var hist = p.coastal === false ? "Inland place: tsunami rules off." :
        (state.history.length ? a.past + " past tsunamis within 500 km since 1900" + (a.deadly ? " (" + a.deadly + " deadly)" : "") + "." : "");
      if (a.best && a.best.r.lvl >= 5) li.className = "risk";
      li.innerHTML = (p.src === "local" ? '<button class="x" title="Remove" data-i="' + i + '">×</button>' : "") +
        '<div class="t">' + esc(p.name) + (p.src === "me" ? "" : p.from ? ' <span class="s">' + esc(p.from) + " → " + esc(p.to || "") + "</span>" : "") + "</div>" +
        '<div class="s">' + esc(status) + "</div>" + (hist ? '<div class="s">' + esc(hist) + "</div>" : "") +
        forecastLine(p) + profileHTML(p);
      var det = li.querySelector("details");
      if (det) det.addEventListener("toggle", function () { state.open[p.name] = det.open; });
      li.addEventListener("click", function (ev) {
        if (ev.target.closest("details, a")) return;
        if (ev.target.classList.contains("x")) {
          var idx = state.localPlaces.indexOf(state.localPlaces.filter(function (lp) { return lp.name === p.name && lp.lat === p.lat; })[0]);
          if (idx >= 0) { state.localPlaces.splice(idx, 1); saveLocal(); renderPlaces(); }
          return;
        }
        map.setView([p.lat, p.lon], 6);
      });
      ul.appendChild(li);

      var col = a.best && a.best.r.lvl >= 5 ? "#c8102e" : "#2b7bb9";
      L.circle([p.lat, p.lon], { radius: 1000e3, color: col, weight: 1, dashArray: "4 6", fill: false }).addTo(placeLayer);
      L.circle([p.lat, p.lon], { radius: 200e3, color: col, weight: 1, fillOpacity: 0.05 }).addTo(placeLayer);
      L.marker([p.lat, p.lon], { title: p.name }).bindPopup("<b>" + esc(p.name) + "</b><br>" + esc(status) +
        "<br><small>Rings: 200 km (shaking), 1000 km (local tsunami)</small>").addTo(placeLayer);
    });
  }

  function renderQuakes() {
    quakeLayer.clearLayers();
    state.quakes.forEach(function (q) {
      L.circleMarker([q.lat, q.lon], {
        radius: Math.max(3, (q.mag - 3.5) * 3.2), color: "#4a1d00", weight: 0.6,
        fillColor: color(q.time), fillOpacity: 0.8
      }).bindPopup("<b>M" + q.mag.toFixed(1) + "</b> " + esc(q.place) + "<br>" + ago(q.time) + ", depth " + Math.round(q.depth) +
        " km" + (q.tsunami ? "<br><b>USGS tsunami flag</b>" : "") +
        '<br><a href="' + esc(q.url) + '" target="_blank" rel="noopener">USGS details</a>').addTo(quakeLayer);
    });
    var ul = $("quakes"); ul.innerHTML = "";
    var strong = state.quakes.filter(function (q) { return q.mag >= 5.5; }).slice(0, 15);
    if (!strong.length) ul.innerHTML = '<li class="s">None in the last 7 days.</li>';
    strong.forEach(function (q) {
      var li = document.createElement("li");
      li.innerHTML = '<div class="t">M' + q.mag.toFixed(1) + " · " + esc(q.place) + '</div><div class="s">' + ago(q.time) +
        ", depth " + Math.round(q.depth) + " km" + (q.tsunami ? " · tsunami flag" : "") + "</div>";
      li.addEventListener("click", function () { map.setView([q.lat, q.lon], 6); });
      ul.appendChild(li);
    });
  }

  function renderHistory() {
    histLayer.clearLayers();
    state.history.forEach(function (h) {
      L.circleMarker([h.lat, h.lon], { radius: h.deaths > 1000 ? 8 : h.deaths ? 5 : 3, color: "#2b7bb9", weight: 1.5, fill: false })
        .bindPopup("<b>" + h.y + "</b> " + esc(h.place) + ", " + esc(h.country) +
          (h.mag ? "<br>Quake M" + h.mag : "") + (h.h ? "<br>Max wave " + h.h + " m" : "") +
          (h.deaths ? "<br>Deaths: " + h.deaths.toLocaleString() : "")).addTo(histLayer);
    });
  }

  function renderAll() { renderQuakes(); renderPlaces(); renderDisasters(); }

  // ------------------------------------------------------------ UI
  $("histToggle").addEventListener("change", function (e) {
    if (e.target.checked) { renderHistory(); histLayer.addTo(map); } else { map.removeLayer(histLayer); }
  });

  $("disToggle").addEventListener("change", renderDisasters);

  $("addHint").addEventListener("click", function () { $("addHelp").hidden = !$("addHelp").hidden; });

  map.on("contextmenu", function (e) {
    var name = window.prompt("Name for this place (saved on this device only):");
    if (!name) return;
    var coastal = window.confirm("Is it on or near the coast? (OK = yes, Cancel = inland)");
    state.localPlaces.push({ name: name, lat: +e.latlng.lat.toFixed(4), lon: +e.latlng.lng.toFixed(4), coastal: coastal });
    saveLocal(); renderPlaces();
    loadForecasts().then(renderPlaces);
  });

  $("locate").addEventListener("click", function () {
    if (!navigator.geolocation) { alert("Location not available on this device."); return; }
    $("locate").textContent = "Locating…";
    navigator.geolocation.getCurrentPosition(function (pos) {
      $("locate").textContent = "📍 Check where I am";
      state.me = { name: "You are here", lat: pos.coords.latitude, lon: pos.coords.longitude, coastal: true, src: "me" };
      meLayer.clearLayers();
      L.circleMarker([state.me.lat, state.me.lon], { radius: 8, color: "#fff", weight: 3, fillColor: "#1a73e8", fillOpacity: 1 }).addTo(meLayer);
      map.setView([state.me.lat, state.me.lon], 5);
      renderPlaces();
      loadForecasts().then(renderPlaces);
    }, function () {
      $("locate").textContent = "📍 Check where I am";
      alert("Could not get your location. Allow location access for this site.");
    }, { enableHighAccuracy: false, timeout: 15000, maximumAge: 300000 });
  });

  // ------------------------------------------------------------ start
  getJSON("config/locations.json").then(function (c) { state.configPlaces = c.locations || []; }).catch(function () {})
    .then(function () { return Promise.all([loadQuakes(), loadAlerts(), loadHistory(), loadProfiles()]); })
    .then(function () { renderAll(); return loadForecasts(); })
    .then(function () {
      renderAll();
      var ps = places();
      if (ps.length) map.fitBounds(L.latLngBounds(ps.map(function (p) { return [p.lat, p.lon]; })).pad(0.6), { maxZoom: 5 });
    });

  setInterval(function () { Promise.all([loadQuakes(), loadAlerts(), loadForecasts()]).then(renderAll); }, 60000);

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(function () {});
})();
