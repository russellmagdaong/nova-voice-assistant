"""NOVA's weather and news tools, and the MCP plumbing (request handling, argument checks) that
tool_host.py uses to serve every NOVA tool to Claude from inside NOVA. (It can also run on its own
as a stdio MCP server.) Read-only, and it only talks to two free public sources (no API keys):

- get_weather: Open-Meteo (geocoding + forecast)
- get_news:    Google News RSS headlines, with how long ago each was published

Standard library only, so it starts fast. Try it without Claude:

    .venv\\Scripts\\python.exe nova\\tools_server.py --test "Manila, Philippines"
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime

UA = {"User-Agent": "NOVA-voice-assistant/1.0"}
LOCATION = ""            # the user's city ([claude] location), from --location

WMO = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
       82: "violent showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms",
       96: "thunderstorms with hail", 99: "thunderstorms with heavy hail"}

# Google News editions in English (others fall back to the US edition).
NEWS_EDITIONS = {"US", "GB", "AU", "CA", "IN", "IE", "NZ", "PH", "SG", "ZA", "KE", "NG", "MY", "PK", "GH", "UG", "TZ"}
NEWS_SECTIONS = {"world", "nation", "business", "technology", "entertainment", "sports", "science", "health"}


def fetch(url: str, timeout: float = 6.0, tries: int = 2) -> bytes:
    last: Exception | None = None
    for _ in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 (network hiccups: try once more)
            last = e
    raise RuntimeError(f"couldn't reach {urllib.parse.urlsplit(url).hostname} ({last})")


# ---- weather ----------------------------------------------------------------------------------
_places: dict[str, dict] = {}


def geocode(place: str) -> dict:
    """'Manila, Philippines' -> the best Open-Meteo match (the part after the comma picks between
    places with the same name; otherwise the biggest one wins)."""
    key = place.lower().strip()
    if key not in _places:
        name, _, qualifier = place.partition(",")
        q = urllib.parse.urlencode({"name": name.strip(), "count": 10, "language": "en", "format": "json"})
        found = json.loads(fetch(f"https://geocoding-api.open-meteo.com/v1/search?{q}")).get("results") or []
        if not found:
            raise LookupError(f"couldn't find a place called {place!r}")
        qualifier = qualifier.strip().lower()

        def matches(r):
            return qualifier in " ".join(str(r.get(k, "")) for k in ("country", "admin1", "admin2",
                                                                     "country_code")).lower()
        _places[key] = next((r for r in found if qualifier and matches(r)), found[0])
    return _places[key]


def _hour(t: str) -> str:
    return datetime.fromisoformat(t).strftime("%I %p").lstrip("0").replace("12 PM", "noon").replace("12 AM", "midnight")


def _day(d: str) -> str:
    x = date.fromisoformat(d)
    return x.strftime("%a %d %b").replace(" 0", " ")


def get_weather(place: str = "", days: int = 3) -> str:
    place = (place or LOCATION).strip()
    if not place:
        return "No place was given and the user's city isn't set. Ask the user which city."
    days = max(1, min(int(days or 3), 7))
    p = geocode(place)
    q = urllib.parse.urlencode({
        "latitude": p["latitude"], "longitude": p["longitude"], "timezone": "auto",
        "forecast_days": max(days, 2),
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,"
                   "wind_speed_10m",
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                 "precipitation_sum,uv_index_max,sunrise,sunset"})
    w = json.loads(fetch(f"https://api.open-meteo.com/v1/forecast?{q}"))
    cur, hourly, daily = w["current"], w["hourly"], w["daily"]
    where = ", ".join(x for x in (p.get("name"), p.get("admin1"), p.get("country")) if x)
    now = datetime.fromisoformat(cur["time"])
    lines = [f"Weather for {where} (local time there: {now.strftime('%a %d %b, %I:%M %p').replace(' 0', ' ')}).",
             f"Now: {cur['temperature_2m']:.0f}°C (feels like {cur['apparent_temperature']:.0f}°C), "
             f"{WMO.get(cur['weather_code'], 'unknown')}, humidity {cur['relative_humidity_2m']}%, "
             f"wind {cur['wind_speed_10m']:.0f} km/h"
             + (f", {cur['precipitation']} mm of rain in the last 15 minutes." if cur["precipitation"] else ", not raining.")]
    # The next 12 hours, every 3 hours.
    start = next((i for i, t in enumerate(hourly["time"]) if datetime.fromisoformat(t) > now), None)
    if start is not None:
        steps = []
        for i in range(start, min(start + 13, len(hourly["time"])), 3):
            steps.append(f"{_hour(hourly['time'][i])} {hourly['temperature_2m'][i]:.0f}°C "
                         f"{WMO.get(hourly['weather_code'][i], '')} (rain chance {hourly['precipitation_probability'][i]}%)")
        lines.append("Next hours: " + "; ".join(steps) + ".")
    for i in range(days):
        lines.append(
            f"{_day(daily['time'][i])}: {WMO.get(daily['weather_code'][i], 'unknown')}, "
            f"{daily['temperature_2m_min'][i]:.0f} to {daily['temperature_2m_max'][i]:.0f}°C, "
            f"rain chance {daily['precipitation_probability_max'][i]}% ({daily['precipitation_sum'][i]} mm), "
            f"UV index {daily['uv_index_max'][i]:.0f}, sunrise {_hour_min(daily['sunrise'][i])}, "
            f"sunset {_hour_min(daily['sunset'][i])}.")
    lines.append("(Source: Open-Meteo.)")
    return "\n".join(lines)


def _hour_min(t: str) -> str:
    return datetime.fromisoformat(t).strftime("%I:%M %p").lstrip("0")


# ---- news ---------------------------------------------------------------------------------------
def _edition() -> tuple[str, str]:
    """(country code, name) of the user's Google News edition, from their city."""
    try:
        p = geocode(LOCATION) if LOCATION else {}
    except Exception:  # noqa: BLE001
        p = {}
    cc = (p.get("country_code") or "US").upper()
    if cc not in NEWS_EDITIONS:
        cc = "US"
    return cc, (p.get("country") if cc != "US" or p.get("country_code") == "US" else "United States") or "United States"


def _age(published: datetime) -> str:
    mins = max(0, int((datetime.now(timezone.utc) - published).total_seconds() // 60))
    if mins < 60:
        return f"{mins} minutes ago"
    if mins < 48 * 60:
        h = mins // 60
        return f"{h} hour{'s' if h != 1 else ''} ago"
    return f"{mins // 1440} days ago"


def get_news(topic: str = "", world: bool = False, count: int = 8) -> str:
    cc, country = _edition()
    if world:
        cc, country = "US", "United States"
    ed = urllib.parse.urlencode({"hl": f"en-{cc}", "gl": cc, "ceid": f"{cc}:en"})
    topic = (topic or "").strip()
    section = topic.lower()
    if world and not topic:
        section = "world"
    if section in NEWS_SECTIONS:
        url, what = f"https://news.google.com/rss/headlines/section/topic/{section.upper()}?{ed}", f"{section} headlines"
    elif topic:
        q = urllib.parse.quote(f"{topic} when:2d")
        url, what = f"https://news.google.com/rss/search?q={q}&{ed}", f"news about {topic!r} from the last 2 days"
    else:
        url, what = f"https://news.google.com/rss?{ed}", "top stories"
    items = ET.fromstring(fetch(url)).findall("./channel/item")
    stories = []
    for it in items:
        title, source = (it.findtext("title") or "").strip(), (it.findtext("source") or "").strip()
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3]
        try:
            published = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        stories.append((published, title, source))
    if topic and section not in NEWS_SECTIONS:
        stories.sort(reverse=True)                      # searches: newest first
    stories = stories[:max(1, min(int(count or 8), 15))]
    if not stories:
        return f"No {what} found."
    now = datetime.now().astimezone()
    head = (f"Google News, {country} edition: {what} (user's local time now "
            f"{now.strftime('%a %d %b, %I:%M %p').replace(' 0', ' ')}):")
    return "\n".join([head] + [f"- {t} ({s}, {_age(p)})" for p, t, s in stories])


# ---- MCP ----------------------------------------------------------------------------------------
TOOLS = [
    {"name": "get_weather",
     "description": "Current weather, the next 12 hours and a daily forecast for a place (Open-Meteo). "
                    "Leave `place` empty for the user's own city.",
     "inputSchema": {"type": "object", "properties": {
         "place": {"type": "string", "description": "City, optionally with region or country, e.g. "
                                                    "'Cebu City, Philippines'. Empty = the user's city."},
         "days": {"type": "integer", "minimum": 1, "maximum": 7, "description": "Days of forecast (default 3)."}}}},
    {"name": "get_news",
     "description": "Fresh news headlines from Google News with each story's source and age. Default: top "
                    "stories in the user's country. `topic` can be a section (world, nation, business, "
                    "technology, entertainment, sports, science, health) or any search words.",
     "inputSchema": {"type": "object", "properties": {
         "topic": {"type": "string", "description": "Section name or search words; empty = top stories."},
         "world": {"type": "boolean", "description": "International (US edition) instead of the user's country."},
         "count": {"type": "integer", "minimum": 1, "maximum": 15, "description": "How many headlines (default 8)."}}}},
]
FUNCS = {"get_weather": get_weather, "get_news": get_news}


_TYPES = {"string": str, "integer": int, "boolean": bool, "number": (int, float)}


def call(name: str, args: dict, tools: list[dict] = TOOLS, funcs: dict = FUNCS) -> str:
    """Run a tool after checking its arguments against the tool's schema: unknown names are
    dropped; types, enums, ranges and lengths are enforced."""
    if name not in funcs:
        raise ValueError(f"unknown tool {name}")
    schema = next(t["inputSchema"] for t in tools if t["name"] == name)
    allowed = schema.get("properties", {})
    clean = {}
    for k, v in (args or {}).items():
        spec = allowed.get(k)
        if spec is None:
            continue
        want = _TYPES.get(spec.get("type"))
        if spec.get("type") == "integer" and isinstance(v, float) and v.is_integer():
            v = int(v)
        if want and (not isinstance(v, want) or (spec.get("type") != "boolean" and isinstance(v, bool))):
            raise ValueError(f"{k} must be a {spec['type']}")
        if isinstance(v, str) and len(v) > 100:
            raise ValueError(f"{k} is too long")
        if "enum" in spec and v not in spec["enum"]:
            raise ValueError(f"{k} must be one of {', '.join(map(str, spec['enum']))}")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if ("minimum" in spec and v < spec["minimum"]) or ("maximum" in spec and v > spec["maximum"]):
                raise ValueError(f"{k} must be between {spec.get('minimum')} and {spec.get('maximum')}")
        clean[k] = v
    missing = [k for k in schema.get("required", []) if k not in clean]
    if missing:
        raise ValueError(f"missing {', '.join(missing)}")
    return funcs[name](**clean)


def handle(msg: dict, tools: list[dict] = TOOLS, funcs: dict = FUNCS) -> dict | None:
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:
        return None                                     # a notification (e.g. initialized)
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "nova", "version": "1.0"}}
    elif method == "tools/list":
        result = {"tools": tools}
    elif method == "tools/call":
        try:
            text, err = call(params.get("name", ""), params.get("arguments") or {}, tools, funcs), False
        except Exception as e:  # noqa: BLE001 (report to Claude, keep serving)
            text, err = f"Error: {e}", True
        result = {"content": [{"type": "text", "text": text}], "isError": err}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve():
    out = sys.stdout.buffer
    for raw in sys.stdin.buffer:
        if not raw.strip():
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        reply = handle(msg)
        if reply is not None:
            out.write(json.dumps(reply).encode("utf-8") + b"\n")
            out.flush()


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--location" in argv:
        LOCATION = argv[argv.index("--location") + 1]
    if "--test" in argv:
        i = argv.index("--test")
        LOCATION = argv[i + 1] if i + 1 < len(argv) else LOCATION
        sys.stdout.reconfigure(encoding="utf-8")
        print(get_weather(), "\n")
        print(get_news(), "\n")
        print(get_news("sports", count=3), "\n")
        print(get_news(world=True, count=3))
    else:
        serve()
