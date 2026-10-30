#!/usr/bin/env python3
"""Location index for the Canada Weather MCP server.

Builds one merged, searchable index from the two official MSC site
lists (English and French GeoJSON) and provides the search that backs
the search_locations tool.

Source files:
  English: https://collaboration.cmc.ec.gc.ca/cmc/cmos/public_doc/msc-data/citypage-weather/site_list_en.geojson
  French:  https://collaboration.cmc.ec.gc.ca/cmc/cmos/public_doc/msc-data/citypage-weather/site_list_fr.geojson

Build notes learned from the live files on October 30, 2026:
  1. Both files hold exactly 844 features and share the same site codes.
  2. Property names differ between the two files, and the French file
     misspells its longitude property. Latitude and longitude are
     therefore always read from the geometry coordinates, which are
     present and consistent in both files.
  3. Names carry accents in French (and some English entries), so
     search normalises accents and case on both the query and the
     stored names. A query typed without accents still finds the
     accented place.
"""

import json
import unicodedata
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SOURCE_URLS = {
    "en": "https://collaboration.cmc.ec.gc.ca/cmc/cmos/public_doc/msc-data/citypage-weather/site_list_en.geojson",
    "fr": "https://collaboration.cmc.ec.gc.ca/cmc/cmos/public_doc/msc-data/citypage-weather/site_list_fr.geojson",
}

DATA_DIR = Path(__file__).parent.parent / "data"
INDEX_PATH = DATA_DIR / "locations.json"


def normalise(text):
    """Lowercase and strip accents so search is forgiving in both languages."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.lower().split())


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "sitesy-weather-mcp/0.1"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def entries_from_geojson(geojson, lang):
    """Yield one partial entry per feature, keyed by site code."""
    name_key = "English Names" if lang == "en" else "Noms français"
    prov_key = "Province Codes" if lang == "en" else "Code de province"
    for feature in geojson.get("features", []):
        props = feature.get("properties", {})
        coords = (feature.get("geometry") or {}).get("coordinates") or [None, None]
        yield props.get("Codes"), {
            "name": props.get(name_key),
            "province": props.get(prov_key),
            "lon": coords[0],
            "lat": coords[1],
        }


def build_index(en_geojson, fr_geojson):
    """Merge the English and French lists into one entry per site code."""
    merged = {}
    for lang, geojson in (("en", en_geojson), ("fr", fr_geojson)):
        for code, partial in entries_from_geojson(geojson, lang):
            if not code:
                continue
            entry = merged.setdefault(code, {"site_code": code})
            entry[f"name_{lang}"] = partial["name"]
            entry.setdefault("province", partial["province"])
            if entry.get("lat") is None:
                entry["lat"] = partial["lat"]
            if entry.get("lon") is None:
                entry["lon"] = partial["lon"]
    entries = []
    for code in sorted(merged):
        entry = merged[code]
        entry["search_en"] = normalise(entry.get("name_en"))
        entry["search_fr"] = normalise(entry.get("name_fr"))
        entries.append(entry)
    return entries


def search(entries, query, province=None, limit=10):
    """Rank matches: exact first, then prefix, then substring.

    Searches both the English and French names. An optional province
    code filter narrows the field before ranking.
    """
    q = normalise(query)
    if not q:
        return []
    pool = entries
    if province:
        wanted = province.strip().upper()
        pool = [e for e in pool if (e.get("province") or "").upper() == wanted]
    scored = []
    for entry in pool:
        best = None
        for key in ("search_en", "search_fr"):
            name = entry.get(key) or ""
            if name == q:
                score = 0
            elif name.startswith(q):
                score = 1
            elif q in name:
                score = 2
            else:
                continue
            if best is None or score < best:
                best = score
        if best is not None:
            scored.append((best, entry.get("name_en") or "", entry))
    scored.sort(key=lambda item: (item[0], item[1]))
    results = []
    for _score, _name, entry in scored[:limit]:
        results.append(
            {
                "site_code": entry["site_code"],
                "name_en": entry.get("name_en"),
                "name_fr": entry.get("name_fr"),
                "province": entry.get("province"),
                "lat": entry.get("lat"),
                "lon": entry.get("lon"),
            }
        )
    return results


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    en = fetch_json(SOURCE_URLS["en"])
    fr = fetch_json(SOURCE_URLS["fr"])
    entries = build_index(en, fr)
    payload = {
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_urls": SOURCE_URLS,
        "count": len(entries),
        "locations": entries,
    }
    INDEX_PATH.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"index written: {INDEX_PATH} with {len(entries)} locations")


if __name__ == "__main__":
    main()
