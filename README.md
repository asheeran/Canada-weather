# Canada Weather MCP

A free Model Context Protocol server for official Canadian weather, served live at https://weather.sitesy.ca/mcp

## What it does

The server gives AI assistants live access to Environment and Climate Change Canada data for 844 locations across Canada, in English and French:

1. `search_locations` finds a place by name and returns its site code.
2. `get_current_conditions` returns the latest observed conditions for a site.
3. `get_daily_forecast` returns the daily forecast periods.
4. `get_hourly_forecast` returns the hourly forecast entries.
5. `get_alerts_by_place` returns the official alerts in effect for a location.
6. `get_alerts_by_coordinates` returns the official alerts in effect for a point.
7. `get_air_quality` returns the Air Quality Health Index, current value and forecast, from the nearest monitoring station.

## Connect

The server speaks Streamable HTTP and needs no account and no key. Point any compatible MCP client at the endpoint:

```json
{
  "mcpServers": {
    "canada-weather": {
      "url": "https://weather.sitesy.ca/mcp"
    }
  }
}
```

A human readable page lives at https://weather.sitesy.ca/ and a health check at https://weather.sitesy.ca/health

## Data sources

1. Citypage weather XML from the Meteorological Service of Canada Datamart, for conditions and forecasts.
2. The official GeoJSON site lists, for the 844 location index.
3. The GeoMet OGC API, for weather alerts and air quality.

## Licence and attribution

Code in this repository is under the MIT licence, see LICENSE. Weather data belongs to its source: Environment and Climate Change Canada, used under the Government of Canada open licence with attribution. Environment Canada weather icons are not used.

## Run your own copy

With Docker: `docker compose up -d --build` builds the server, and the compose file also starts a Cloudflare Tunnel container for public hosting, which expects a tunnel named sitesy-weather and credentials in ~/.cloudflared.

Without Docker: create a Python environment, install requirements.txt, then run `python src/server.py` for stdio or `python src/server.py http` for the HTTP server on port 8000. The location index in data/locations.json can be rebuilt any time with the location index builder in src/location_index.py.
