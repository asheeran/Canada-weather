#!/usr/bin/env python3
"""Canada Weather MCP server.

Exposes the weather tools over MCP. Server name canada-weather and
registry name io.github.asheeran/canada-weather were decided by Adge
on October 30, 2026, with the public endpoint at weather.sitesy.ca.

Run modes:
  python3 server.py            stdio transport for local clients
  python3 server.py http       Streamable HTTP transport for hosting

Data: Environment and Climate Change Canada, under the Government of
Canada open licence. Weather icons are not used.
"""

import os
import sys
import threading
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

import location_index
import weather_tools as tools

mcp = FastMCP("canada-weather", instructions=(
    "Official Canadian weather from Environment and Climate Change Canada. "
    "Start with search_locations to get a site code, then use the forecast "
    "and conditions tools. Data is provided under the Government of Canada "
    "open licence."
))

LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Canada Weather MCP</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 44rem; padding: 0 1rem; line-height: 1.55; color: #1a1a1a; }
  code { background: #f0f0f0; padding: 0.1rem 0.35rem; border-radius: 0.25rem; }
  h1 { margin-bottom: 0.25rem; }
  .sub { color: #555; margin-top: 0; }
</style>
</head>
<body>
<h1>Canada Weather</h1>
<p class="sub">A free Model Context Protocol server for official Canadian weather.</p>
<p>This server gives AI assistants live access to forecasts, current conditions, weather alerts, and air quality for 844 locations across Canada, in English and French. All data comes from Environment and Climate Change Canada.</p>
<h2>Connect</h2>
<p>MCP endpoint: <code>https://weather.sitesy.ca/mcp</code></p>
<p>Add that address to any MCP client that supports Streamable HTTP. No account and no key are needed. The address itself speaks the MCP protocol, so a browser visiting it sees raw protocol messages. This page is the human readable front door.</p>
<h2>Tools</h2>
<ol>
<li><code>search_locations</code> finds a place and returns its site code.</li>
<li><code>get_current_conditions</code> returns the latest observed conditions.</li>
<li><code>get_daily_forecast</code> returns the daily forecast periods.</li>
<li><code>get_hourly_forecast</code> returns the hourly forecast.</li>
<li><code>get_alerts_by_place</code> returns active official alerts for a location.</li>
<li><code>get_alerts_by_coordinates</code> returns active official alerts for a point.</li>
<li><code>get_air_quality</code> returns the Air Quality Health Index, current value and forecast, from the nearest station.</li>
</ol>
<h2>Data and licence</h2>
<p>Source: Environment and Climate Change Canada. Data is used under the Government of Canada open licence, with attribution. Weather icons are not used.</p>
<p>Built by Sitesy. Health check: <code>https://weather.sitesy.ca/health</code></p>
</body>
</html>
"""


@mcp.custom_route("/", methods=["GET"])
async def landing_page(request):
    from starlette.responses import HTMLResponse

    return HTMLResponse(LANDING_HTML)


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    from starlette.responses import JSONResponse

    return JSONResponse({"status": "ok", "server": "canada-weather", "tools": 7})


@mcp.tool()
def search_locations(query: str, province: str = "", limit: int = 10) -> dict:
    """Search Canadian forecast locations by name, in English or French.

    Returns matching locations with site code, names in both languages,
    province code, and coordinates. Use the site code with the other tools.
    """
    return tools.search_locations(query, province=province or None, limit=limit)


@mcp.tool()
def get_current_conditions(site_code: str, lang: str = "en") -> dict:
    """Current observed weather conditions for a location by site code.

    lang is en or fr. Get the site code from search_locations.
    """
    return tools.get_current_conditions(site_code, lang)


@mcp.tool()
def get_daily_forecast(site_code: str, lang: str = "en") -> dict:
    """Daily forecast periods for a location by site code. lang is en or fr."""
    return tools.get_daily_forecast(site_code, lang)


@mcp.tool()
def get_hourly_forecast(site_code: str, lang: str = "en", hours: int = 24) -> dict:
    """Hourly forecast for a location by site code. lang is en or fr."""
    return tools.get_hourly_forecast(site_code, lang, hours)


@mcp.tool()
def get_alerts_by_place(site_code: str, lang: str = "en") -> dict:
    """Active official weather alerts covering a location by site code.

    Returns every active alert whose coverage includes the location,
    with its colour coded risk level, or an empty list when none are
    active. lang is en or fr.
    """
    return tools.get_alerts_by_place(site_code, lang)


@mcp.tool()
def get_alerts_by_coordinates(lat: float, lon: float, lang: str = "en") -> dict:
    """Active official weather alerts covering a latitude and longitude point. lang is en or fr."""
    return tools.get_alerts_by_coordinates(lat, lon, lang)


@mcp.tool()
def get_air_quality(site_code: str = "", query: str = "", lat: float = 0.0, lon: float = 0.0, lang: str = "en") -> dict:
    """Air Quality Health Index for a place, current value plus forecast.

    Identify the place with a site code from search_locations, a place
    name in query, or a latitude and longitude pair. Values come from
    the nearest air quality station, which is named in the result.
    lang is en or fr.
    """
    return tools.get_air_quality(
        site_code=site_code,
        query=query,
        lat=lat if lat else None,
        lon=lon if lon else None,
        lang=lang,
    )


def refresh_location_index_if_stale(max_age_hours=24):
    """Rebuild the cached location index when it is missing or old.

    A failure keeps the existing copy, so a source outage at startup
    never takes the server down.
    """
    try:
        path = location_index.INDEX_PATH
        stale = (not path.exists()) or (time.time() - path.stat().st_mtime > max_age_hours * 3600)
        if stale:
            location_index.main()
    except Exception as exc:
        print(f"location index refresh skipped: {exc}", file=sys.stderr)


def _daily_refresh_loop():
    while True:
        time.sleep(24 * 3600)
        refresh_location_index_if_stale(max_age_hours=0)


PUBLIC_HOST = "weather.sitesy.ca"


def configure_public_host():
    """Allow the public hostname through the SDK host validation.

    The streamable HTTP transport rejects any Host header outside its
    allowed list, which by default only covers local addresses. Behind
    the tunnel the server is reached as weather.sitesy.ca, so that
    host must be allowed explicitly or every public call fails with a
    421 Invalid Host header response.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[PUBLIC_HOST, "127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=[f"https://{PUBLIC_HOST}", "http://127.0.0.1:*", "http://localhost:*"],
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "http":
        refresh_location_index_if_stale()
        threading.Thread(target=_daily_refresh_loop, daemon=True).start()
        configure_public_host()
        mcp.settings.host = os.environ.get("HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("PORT", "8000"))
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
