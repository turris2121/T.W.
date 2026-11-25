"""Travel health notices: CDC Travelers' Health (Level 1-4) and WHO Disease
Outbreak News, matched to trip countries."""

import datetime as dt
import email.utils
import re
import unicodedata
import xml.etree.ElementTree as ET

from common import fetch, fetch_json, iso, now_utc

CDC_FEED = "https://wwwnc.cdc.gov/travel/rss/notices.xml"
WHO_API = ("https://www.who.int/api/news/diseaseoutbreaknews"
           "?$top=40&$orderby=PublicationDateAndTime%20desc")

# Names that differ between OpenStreetMap and CDC/WHO titles
ALIASES = {
    "united states": ["united states of america", "usa", "u.s."],
    "united kingdom": ["uk", "great britain", "united kingdom of great britain"],
    "vietnam": ["viet nam"],
    "laos": ["lao people's democratic republic", "lao pdr"],
    "south korea": ["republic of korea", "korea"],
    "north korea": ["democratic people's republic of korea"],
    "russia": ["russian federation"],
    "iran": ["islamic republic of iran"],
    "syria": ["syrian arab republic"],
    "tanzania": ["united republic of tanzania"],
    "bolivia": ["plurinational state of bolivia"],
    "venezuela": ["bolivarian republic of venezuela"],
    "democratic republic of the congo": ["drc", "congo (kinshasa)"],
    "turkey": ["turkiye", "türkiye"],
    "czechia": ["czech republic"],
    "myanmar": ["burma"],
    "cape verde": ["cabo verde"],
    "ivory coast": ["cote d'ivoire", "côte d'ivoire"],
    "east timor": ["timor-leste"],
}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("’", "'")
    return re.sub(r"\s+", " ", s).strip()


def country_names(country):
    c = norm(country)
    names = {c}
    for k, v in ALIASES.items():
        group = {k} | {norm(x) for x in v}
        if c in group:
            names |= group
    return names


def mentions_country(text, country):
    t = " %s " % norm(text)
    return any(re.search(r"[^a-z]%s[^a-z]" % re.escape(n), t) for n in country_names(country) if n)


def _rss_time(s):
    try:
        return email.utils.parsedate_to_datetime(s).astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return now_utc()


def get_cdc():
    root = ET.fromstring(fetch(CDC_FEED))
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        m = re.match(r"Level\s+(\d)\s*-\s*(.+)$", title)
        if not m:
            continue
        out.append({
            "id": "cdc:%s" % (item.findtext("guid") or title),
            "source": "CDC",
            "level": int(m.group(1)),
            "title": title,
            "subject": m.group(2),
            "global": m.group(2).lower().startswith("global"),
            "summary": re.split(r"\s*Country List\s*:", (item.findtext("description") or "").strip())[0],
            "time": iso(_rss_time(item.findtext("pubDate"))),
            "url": (item.findtext("link") or "").strip(),
        })
    return out


def get_who(days=45):
    data = fetch_json(WHO_API)
    cutoff = now_utc() - dt.timedelta(days=days)
    out = []
    for d in data.get("value", []):
        t = d.get("PublicationDateAndTime") or d.get("PublicationDate") or ""
        try:
            when = dt.datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        if when < cutoff:
            continue
        title = d.get("OverrideTitle") or d.get("Title") or ""
        summary = re.sub(r"<[^>]+>", " ", d.get("Summary") or "")
        out.append({
            "id": "who:%s" % (d.get("DonId") or d.get("Id")),
            "source": "WHO",
            "level": None,
            "title": title,
            "subject": title,
            "global": False,
            "summary": re.sub(r"\s+", " ", summary).strip()[:300],
            "time": iso(when),
            "url": "https://www.who.int/emergencies/disease-outbreak-news/item/%s" % d.get("UrlName", ""),
        })
    return out


def notices_for_country(notices, country):
    """CDC: title names the country ('... in Ghana, Togo') or is Global.
    WHO: title names the country ('Cholera - Sudan')."""
    if not country:
        return []
    return [n for n in notices if n["global"] or mentions_country(n["subject"], country)]
