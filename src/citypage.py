#!/usr/bin/env python3
"""Citypage weather file access and parsing for the Canada Weather MCP server.

Implements the fetch and parse layer proven on October 30, 2026:
  Directory: https://dd.weather.gc.ca/today/citypage_weather/{PROV}/{HH}/
  File name: {YYYYMMDD}T{HHmmss.sss}Z_MSC_CitypageWeather_{SiteCode}_{L}.xml

Fetch rule from the scope: list the current UTC hour folder, take the
newest file for the site and language, and step back one hour at a time
when the current hour has no file yet.

Parse notes from the live files:
  1. Times are child elements, not attributes. Each dateTime element
     holds year, month, day, hour, minute, a timeStamp child in
     YYYYMMDDHHMMSS form, and a textSummary child. Two copies are
     present, one in UTC and one in the local zone.
  2. Condition text and wind direction arrive localised from the source.
  3. The warnings element is ignored here. Alerts come from the GeoMet
     API in a later build step.
"""

import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

BASE = "https://dd.weather.gc.ca/today/citypage_weather"
HREF_RE = re.compile(r'href="([^"]+\.xml)"')
USER_AGENT = "sitesy-weather-mcp/0.1"
MAX_STEP_BACK_HOURS = 8

_cache = {}


def _fetch_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def find_latest_file(province, site_code, lang, now=None):
    """Return the file URL for the newest available citypage file."""
    now = now or datetime.now(timezone.utc)
    for back in range(MAX_STEP_BACK_HOURS):
        moment = now - timedelta(hours=back)
        listing_url = f"{BASE}/{province}/{moment.hour:02d}/"
        try:
            listing = _fetch_bytes(listing_url).decode("utf-8", "replace")
        except Exception:
            continue
        names = sorted(n for n in HREF_RE.findall(listing) if f"_{site_code}_{lang}.xml" in n)
        if names:
            return listing_url + names[-1]
    raise LookupError(f"no citypage file for {site_code} ({lang}) in the last {MAX_STEP_BACK_HOURS} hourly folders")


def _text(parent, tag):
    el = parent.find(tag) if parent is not None else None
    if el is None or el.text is None:
        return None
    value = el.text.strip()
    return value or None


def _num(parent, tag):
    value = _text(parent, tag)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _int(parent, tag):
    value = _num(parent, tag)
    return int(value) if value is not None else None


def _datetime_pair(parent, name):
    """Return the UTC and local text summaries plus the UTC stamp for a named dateTime."""
    out = {"utc_stamp": None, "utc_text": None, "local_text": None}
    if parent is None:
        return out
    for dt in parent.findall("dateTime"):
        if dt.get("name") != name:
            continue
        if dt.get("zone") == "UTC":
            out["utc_stamp"] = _text(dt, "timeStamp")
            out["utc_text"] = _text(dt, "textSummary")
        else:
            out["local_text"] = _text(dt, "textSummary")
    return out


def parse_citypage(xml_bytes):
    """Parse one citypage XML file into plain structured data."""
    root = ET.fromstring(xml_bytes)
    created = _datetime_pair(root, "xmlCreation")

    loc = root.find("location")
    location = {
        "name": _text(loc, "name"),
        "province": _text(loc, "province"),
        "country": _text(loc, "country"),
        "region": _text(loc, "region"),
    }

    cc = root.find("currentConditions")
    current = None
    if cc is not None:
        observed = _datetime_pair(cc, "observation")
        wind = cc.find("wind")
        pressure = cc.find("pressure")
        current = {
            "condition": _text(cc, "condition"),
            "temperature_c": _num(cc, "temperature"),
            "dewpoint_c": _num(cc, "dewpoint"),
            "humidity_percent": _int(cc, "relativeHumidity"),
            "pressure_kpa": _num(cc, "pressure"),
            "pressure_tendency": pressure.get("tendency") if pressure is not None else None,
            "visibility_km": _num(cc, "visibility"),
            "wind_speed_kmh": _int(wind, "speed"),
            "wind_gust_kmh": _int(wind, "gust"),
            "wind_direction": _text(wind, "direction"),
            "observed_utc": observed["utc_text"],
            "observed_local": observed["local_text"],
        }

    fg = root.find("forecastGroup")
    issue = _datetime_pair(fg, "forecastIssue")
    daily = []
    if fg is not None:
        for fc in fg.findall("forecast"):
            temps = {}
            temps_el = fc.find("temperatures")
            if temps_el is not None:
                for t in temps_el.findall("temperature"):
                    temps[t.get("class")] = _num(t, ".")
            abbr = fc.find("abbreviatedForecast")
            daily.append(
                {
                    "period": _text(fc, "period"),
                    "summary": _text(fc, "textSummary"),
                    "condition": _text(abbr, "textSummary"),
                    "pop_percent": _int(abbr, "pop"),
                    "temperature_high_c": temps.get("high"),
                    "temperature_low_c": temps.get("low"),
                }
            )

    hg = root.find("hourlyForecastGroup")
    hourly_issue = _datetime_pair(hg, "forecastIssue") if hg is not None else {"utc_text": None, "local_text": None}
    hourly = []
    if hg is not None:
        for hf in hg.findall("hourlyForecast"):
            hourly.append(
                {
                    "datetime_utc": hf.get("dateTimeUTC"),
                    "condition": _text(hf, "condition"),
                    "temperature_c": _num(hf, "temperature"),
                    "pop_percent": _int(hf, "lop"),
                    "wind_speed_kmh": _int(hf.find("wind"), "speed"),
                    "wind_direction": _text(hf.find("wind"), "direction"),
                }
            )

    return {
        "location": location,
        "file_created_utc": created["utc_text"],
        "current": current,
        "forecast_issue_utc": issue["utc_text"],
        "forecast_issue_local": issue["local_text"],
        "daily": daily,
        "hourly": hourly,
        "hourly_issue_utc": hourly_issue["utc_text"],
    }


def get_citypage(province, site_code, lang="en", use_cache=True):
    """Fetch and parse the newest citypage file, with a short in memory cache."""
    key = (province, site_code, lang)
    now = datetime.now(timezone.utc)
    cached = _cache.get(key)
    if use_cache and cached and (now - cached[0]).total_seconds() < 600:
        return cached[1]
    url = find_latest_file(province, site_code, lang, now=now)
    parsed = parse_citypage(_fetch_bytes(url))
    parsed["source_file"] = url
    if use_cache:
        _cache[key] = (now, parsed)
    return parsed
