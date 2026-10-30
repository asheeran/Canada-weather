#!/usr/bin/env python3
"""GeoMet OGC API access for alerts and air quality.

Collections used, all probed live on October 30, 2026:
  weather-alerts               active alerts with polygon coverage and
                               bilingual fields, including the colour
                               coded risk level
  aqhi-stations                the 134 air quality stations
  aqhi-observations-realtime   observation history with a latest flag
  aqhi-forecasts-realtime      hourly forecasts by publication round

Alert matching is done honestly: candidates come from a bounding box
query around the point, then a point in polygon test decides coverage.
Air quality uses the nearest station to the requested point.
"""

import json
import math
import time
import urllib.parse
import urllib.request

API = "https://api.weather.gc.ca/collections"
USER_AGENT = "sitesy-weather-mcp/0.1"

_cache = {}


def _get(collection, params, cache_seconds=0):
    query = urllib.parse.urlencode(params)
    url = f"{API}/{collection}/items?{query}"
    now = time.time()
    if cache_seconds:
        hit = _cache.get(url)
        if hit and now - hit[0] < cache_seconds:
            return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if cache_seconds:
        _cache[url] = (now, data)
    return data


def _bbox(lat, lon, half_width):
    return f"{lon - half_width},{lat - half_width},{lon + half_width},{lat + half_width}"


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _ring_contains(ring, x, y):
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _polygon_contains(rings, x, y):
    """Outer ring contains the point and no hole does."""
    if not rings or not _ring_contains(rings[0], x, y):
        return False
    for hole in rings[1:]:
        if _ring_contains(hole, x, y):
            return False
    return True


def geometry_contains(geometry, lat, lon):
    if not geometry:
        return False
    gtype = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if gtype == "Polygon":
        return _polygon_contains(coords, lon, lat)
    if gtype == "MultiPolygon":
        return any(_polygon_contains(poly, lon, lat) for poly in coords)
    return False


def _alert_summary(props, lang):
    suffix = "fr" if lang == "fr" else "en"
    return {
        "name": props.get(f"alert_name_{suffix}"),
        "type": props.get("alert_type"),
        "risk_colour": props.get(f"risk_colour_{suffix}"),
        "status": props.get(f"status_{suffix}"),
        "region": props.get(f"feature_name_{suffix}"),
        "province": props.get("province"),
        "confidence": props.get(f"confidence_{suffix}"),
        "impact": props.get(f"impact_{suffix}"),
        "text": props.get(f"alert_text_{suffix}"),
        "published_utc": props.get("publication_datetime"),
        "expires_utc": props.get("expiration_datetime"),
        "event_end_utc": props.get("event_end_datetime"),
    }


def alerts_for_point(lat, lon, lang="en"):
    """Active alerts whose coverage polygon contains the point."""
    data = _get(
        "weather-alerts",
        {"bbox": _bbox(lat, lon, 1.5), "limit": 500},
        cache_seconds=120,
    )
    results = []
    for feature in data.get("features", []):
        if geometry_contains(feature.get("geometry"), lat, lon):
            results.append(_alert_summary(feature.get("properties", {}), lang))
    return results


def aqhi_stations_near(lat, lon):
    """Air quality stations near a point, nearest first."""
    for half in (1.25, 2.5):
        data = _get("aqhi-stations", {"bbox": _bbox(lat, lon, half), "limit": 200}, cache_seconds=3600)
        found = []
        for feature in data.get("features", []):
            coords = (feature.get("geometry") or {}).get("coordinates") or []
            if len(coords) != 2:
                continue
            props = feature["properties"]
            found.append(
                {
                    "location_id": props.get("location_id"),
                    "name_en": props.get("location_name_en"),
                    "name_fr": props.get("location_name_fr"),
                    "lat": coords[1],
                    "lon": coords[0],
                    "distance_km": round(haversine_km(lat, lon, coords[1], coords[0]), 1),
                }
            )
        if found:
            found.sort(key=lambda s: s["distance_km"])
            return found
    return []


def nearest_aqhi_station(lat, lon):
    stations = aqhi_stations_near(lat, lon)
    return stations[0] if stations else None


def _station_aqhi(station, lang, forecast_hours):
    lid = station["location_id"]
    bbox = _bbox(station["lat"], station["lon"], 0.6)

    obs = _get(
        "aqhi-observations-realtime",
        {"bbox": bbox, "limit": 3000, "sortby": "-observation_datetime"},
        cache_seconds=300,
    )
    current = None
    for feature in obs.get("features", []):
        props = feature["properties"]
        if props.get("location_id") == lid and props.get("latest"):
            current = {
                "aqhi": props.get("aqhi"),
                "observed_utc": props.get("observation_datetime"),
                "observed_text": props.get(f"observation_datetime_text_{lang}"),
            }
            break

    fc = _get(
        "aqhi-forecasts-realtime",
        {"bbox": bbox, "limit": 3000, "sortby": "-publication_datetime"},
        cache_seconds=300,
    )
    mine = [f["properties"] for f in fc.get("features", []) if f["properties"].get("location_id") == lid]
    forecast = []
    published = None
    if mine:
        published = max(p.get("publication_datetime") for p in mine)
        rounds = [p for p in mine if p.get("publication_datetime") == published]
        rounds.sort(key=lambda p: p.get("forecast_datetime") or "")
        upcoming = [p for p in rounds if current is None or (p.get("forecast_datetime") or "") >= (current.get("observed_utc") or "")]
        for props in (upcoming or rounds)[:forecast_hours]:
            forecast.append(
                {
                    "datetime_utc": props.get("forecast_datetime"),
                    "aqhi": props.get("aqhi"),
                }
            )
    return {"station": station, "current": current, "forecast": forecast, "forecast_published_utc": published}


def aqhi_for_point(lat, lon, lang="en", forecast_hours=24):
    """Current AQHI plus the forecast from the nearest useful station.

    Coverage in the realtime observation feed is uneven: on October 30,
    2026 only 63 of the 134 stations had a current observation, while
    forecasts were published for stations the observation feed skips,
    Montréal among them. So the nearest station wins whenever it has a
    forecast or a current value. Only a station with neither is passed
    over, and a current value is borrowed from a farther station only
    within 50 km. A missing current value is reported as missing with
    an explanation, never silently replaced.
    """
    stations = aqhi_stations_near(lat, lon)
    if not stations:
        return {"station": None, "current": None, "forecast": [], "forecast_published_utc": None}
    results = [_station_aqhi(station, lang, forecast_hours) for station in stations[:5]]
    nearest = results[0]
    if nearest["current"] is not None or nearest["forecast"]:
        return nearest
    for result in results[1:]:
        if result["current"] is not None and result["station"]["distance_km"] <= 50:
            return result
    for result in results[1:]:
        if result["forecast"]:
            return result
    return nearest
