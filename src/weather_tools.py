#!/usr/bin/env python3
"""Tool functions for the Canada Weather MCP server.

These are the plain functions the MCP server exposes. Each returns a
JSON friendly dict with the data, the source times, and the attribution
line required by the scope.

Tools in this module (build step three):
  search_locations, get_current_conditions, get_daily_forecast,
  get_hourly_forecast.
Alerts and air quality arrive in build step four.
"""

import json
from pathlib import Path

import citypage
import geomet
import location_index

ATTRIBUTION = "Source: Environment and Climate Change Canada"
DATA_DIR = Path(__file__).parent.parent / "data"

_locations_cache = None


def load_locations():
    """Load the built location index, building it first if it is missing."""
    global _locations_cache
    if _locations_cache is not None:
        return _locations_cache
    if location_index.INDEX_PATH.exists():
        payload = json.loads(location_index.INDEX_PATH.read_text(encoding="utf-8"))
        _locations_cache = payload["locations"]
    else:
        en = location_index.fetch_json(location_index.SOURCE_URLS["en"])
        fr = location_index.fetch_json(location_index.SOURCE_URLS["fr"])
        _locations_cache = location_index.build_index(en, fr)
    return _locations_cache


def find_site(site_code):
    for entry in load_locations():
        if entry["site_code"] == site_code:
            return entry
    return None


def _check_lang(lang):
    if lang not in ("en", "fr"):
        raise ValueError("lang must be en or fr")
    return lang


def _site_or_raise(site_code):
    entry = find_site(site_code)
    if entry is None:
        raise ValueError(f"unknown site code {site_code}; use search_locations first")
    return entry


def _location_block(entry, lang):
    return {
        "site_code": entry["site_code"],
        "name": entry.get("name_fr") if lang == "fr" else entry.get("name_en"),
        "name_en": entry.get("name_en"),
        "name_fr": entry.get("name_fr"),
        "province_code": entry.get("province"),
        "lat": entry.get("lat"),
        "lon": entry.get("lon"),
    }


def search_locations(query, province=None, limit=10):
    """Find forecast locations by name, in English or French."""
    results = location_index.search(load_locations(), query, province=province, limit=limit)
    return {"query": query, "count": len(results), "results": results, "attribution": ATTRIBUTION}


def get_current_conditions(site_code, lang="en"):
    """Current observed conditions for one location."""
    _check_lang(lang)
    entry = _site_or_raise(site_code)
    parsed = citypage.get_citypage(entry["province"], site_code, lang)
    current = dict(parsed["current"] or {})
    return {
        "location": _location_block(entry, lang),
        "language": lang,
        "current": current,
        "source_file": parsed["source_file"],
        "attribution": ATTRIBUTION,
    }


def get_daily_forecast(site_code, lang="en", periods=None):
    """Daily forecast periods for one location."""
    _check_lang(lang)
    entry = _site_or_raise(site_code)
    parsed = citypage.get_citypage(entry["province"], site_code, lang)
    daily = parsed["daily"]
    if periods:
        daily = daily[:periods]
    return {
        "location": _location_block(entry, lang),
        "language": lang,
        "forecast_issue_utc": parsed["forecast_issue_utc"],
        "forecast_issue_local": parsed["forecast_issue_local"],
        "periods": daily,
        "source_file": parsed["source_file"],
        "attribution": ATTRIBUTION,
    }


def get_alerts_by_place(site_code, lang="en"):
    """Active official alerts covering one forecast location."""
    _check_lang(lang)
    entry = _site_or_raise(site_code)
    alerts = geomet.alerts_for_point(entry["lat"], entry["lon"], lang)
    return {
        "location": _location_block(entry, lang),
        "language": lang,
        "alert_count": len(alerts),
        "alerts": alerts,
        "attribution": ATTRIBUTION,
    }


def get_alerts_by_coordinates(lat, lon, lang="en"):
    """Active official alerts covering a latitude and longitude point."""
    _check_lang(lang)
    alerts = geomet.alerts_for_point(float(lat), float(lon), lang)
    return {
        "lat": float(lat),
        "lon": float(lon),
        "language": lang,
        "alert_count": len(alerts),
        "alerts": alerts,
        "attribution": ATTRIBUTION,
    }


def _aqhi_band(value, lang):
    if value is None:
        return None
    if value <= 3:
        return "risque faible pour la santé" if lang == "fr" else "low health risk"
    if value <= 6:
        return "risque modéré pour la santé" if lang == "fr" else "moderate health risk"
    if value <= 9:
        return "risque élevé pour la santé" if lang == "fr" else "high health risk"
    return "risque très élevé pour la santé" if lang == "fr" else "very high health risk"


def get_air_quality(site_code="", query="", lat=None, lon=None, lang="en"):
    """Current and forecast Air Quality Health Index near a place or point.

    Give a site code, a place name query, or a latitude and longitude.
    Values come from the nearest air quality station.
    """
    _check_lang(lang)
    place = None
    if site_code:
        entry = _site_or_raise(site_code)
        lat, lon = entry["lat"], entry["lon"]
        place = _location_block(entry, lang)
    elif query:
        results = location_index.search(load_locations(), query, limit=1)
        if not results:
            raise ValueError(f"no location matches {query!r}")
        top = results[0]
        lat, lon = top["lat"], top["lon"]
        place = {
            "site_code": top["site_code"],
            "name": top.get("name_fr") if lang == "fr" else top.get("name_en"),
            "province_code": top.get("province"),
            "lat": lat,
            "lon": lon,
        }
    if lat is None or lon is None:
        raise ValueError("provide a site code, a place query, or latitude and longitude")
    data = geomet.aqhi_for_point(float(lat), float(lon), lang)
    current = data.get("current")
    current_note = None
    if current:
        current = dict(current)
        current["health_risk"] = _aqhi_band(current.get("aqhi"), lang)
    elif data.get("station") is not None:
        current_note = (
            "Aucune observation actuelle n'est publiée pour la station la plus proche dans le flux en temps réel; les valeurs prévues sont fournies."
            if lang == "fr"
            else "No current observation is published for the nearest station in the realtime feed; forecast values are provided."
        )
    forecast_peak = None
    if data.get("forecast"):
        forecast_peak = max((f.get("aqhi") or 0) for f in data["forecast"])
    return {
        "place": place,
        "lat": float(lat),
        "lon": float(lon),
        "language": lang,
        "station": data.get("station"),
        "current": current,
        "current_note": current_note,
        "forecast": data.get("forecast", []),
        "forecast_peak_aqhi": forecast_peak,
        "forecast_peak_health_risk": _aqhi_band(forecast_peak, lang) if forecast_peak is not None else None,
        "forecast_published_utc": data.get("forecast_published_utc"),
        "attribution": ATTRIBUTION,
    }


def get_hourly_forecast(site_code, lang="en", hours=24):
    """Hourly forecast entries for one location."""
    _check_lang(lang)
    entry = _site_or_raise(site_code)
    parsed = citypage.get_citypage(entry["province"], site_code, lang)
    hourly = parsed["hourly"][:hours] if hours else parsed["hourly"]
    return {
        "location": _location_block(entry, lang),
        "language": lang,
        "forecast_issue_utc": parsed["hourly_issue_utc"] or parsed["forecast_issue_utc"],
        "hours": hourly,
        "source_file": parsed["source_file"],
        "attribution": ATTRIBUTION,
    }
