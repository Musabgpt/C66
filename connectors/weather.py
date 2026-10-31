"""weather: the weather now, today and the next days for a place, from Open-Meteo (free, no key).

The question a personal assistant gets most. A web search answered it with pages to visit, or with another country's
weather (websearch.py); this gives the numbers. Open-Meteo's geocoder knows Arabic names, but only as written: "عمان"
finds villages in Iraq and Yemen, "عمّان" (with the shadda nobody types) the capital of Jordan. So of the places
found, the one in the phone's time zone wins; when none is, the phone's own city is taken if its name without marks is
the name asked for; else the most populous place. With no place said, the city of the phone's time zone is used."""
import json
import os
import re
import urllib.parse

from . import tools

GEO = "https://geocoding-api.open-meteo.com/v1/search?count=10&language=ar&name="
FORECAST = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&timezone=auto&forecast_days=3"
            "&current=temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m"
            "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max")
# WMO weather codes, as Open-Meteo gives them
CODES = {0: ("clear sky", "صافٍ"), 1: ("mainly clear", "صافٍ غالباً"), 2: ("partly cloudy", "غائم جزئياً"),
         3: ("overcast", "غائم"), 45: ("fog", "ضباب"), 48: ("freezing fog", "ضباب متجمد"),
         51: ("light drizzle", "رذاذ خفيف"), 53: ("drizzle", "رذاذ"), 55: ("dense drizzle", "رذاذ كثيف"),
         56: ("freezing drizzle", "رذاذ متجمد"), 57: ("dense freezing drizzle", "رذاذ متجمد كثيف"),
         61: ("light rain", "مطر خفيف"), 63: ("rain", "مطر"), 65: ("heavy rain", "مطر غزير"),
         66: ("freezing rain", "مطر متجمد"), 67: ("heavy freezing rain", "مطر متجمد غزير"),
         71: ("light snow", "ثلج خفيف"), 73: ("snow", "ثلج"), 75: ("heavy snow", "ثلج كثيف"),
         77: ("snow grains", "حبيبات ثلج"), 80: ("light showers", "زخات مطر خفيفة"), 81: ("showers", "زخات مطر"),
         82: ("violent showers", "زخات مطر غزيرة"), 85: ("snow showers", "زخات ثلج"),
         86: ("heavy snow showers", "زخات ثلج كثيفة"), 95: ("thunderstorm", "عاصفة رعدية"),
         96: ("thunderstorm with hail", "عاصفة رعدية مع برد"), 99: ("thunderstorm with heavy hail", "عاصفة رعدية مع برد كثيف")}


def _sky(code):
    en, ar = CODES.get(int(code or 0), ("unknown", "غير معروف"))
    return "%s (%s)" % (ar, en)


def home_place():
    """The phone's area: the city in its time zone's name (Asia/Amman: Amman)."""
    zone = os.environ.get("TZ") or ""
    return zone.rsplit("/", 1)[-1].replace("_", " ") if "/" in zone else ""


def _bare(text):
    """A name without Arabic marks (shadda, short vowels) or hamza forms, for comparing spellings."""
    return re.sub("[ً-ْٰـ]", "", str(text or "")).translate(
        str.maketrans("أإآٱ", "اااا")).strip().lower()


def _search(name):
    return json.loads(tools._get(GEO + urllib.parse.quote(name))).get("results") or []


def place_for(name):
    zone = os.environ.get("TZ") or ""
    found = _search(name)
    local = [r for r in found if zone and r.get("timezone") == zone]
    home = home_place()
    if not local and home and _bare(home) != _bare(name):
        local = [r for r in _search(home) if r.get("timezone") == zone and _bare(r.get("name")) == _bare(name)]
    pool = local or found
    return max(pool, key=lambda r: r.get("population") or 0) if pool else None


def report(place=""):
    name = str(place or "").strip() or home_place()
    if not name:
        raise tools.ToolError("say which city or place")
    where = place_for(name)
    if where is None:
        raise tools.ToolError("no place called %r was found; try the city's name in English or Arabic" % name)
    f = json.loads(tools._get(FORECAST % (where["latitude"], where["longitude"])))
    now, day = f["current"], f["daily"]
    lines = ["%s، %s — local time %s (%s)" % (where.get("name"), where.get("country", ""), now.get("time", ""),
                                               f.get("timezone", "")),
             "Now: %s°C (feels %s°C), %s, humidity %s%%, wind %s km/h." % (
                 now["temperature_2m"], now["apparent_temperature"], _sky(now["weather_code"]),
                 now["relative_humidity_2m"], now["wind_speed_10m"])]
    for i, label in enumerate(("Today", "Tomorrow", "After tomorrow")):
        if i < len(day.get("time") or []):
            lines.append("%s %s: %s–%s°C, %s, rain chance %s%%." % (
                label, day["time"][i], day["temperature_2m_min"][i], day["temperature_2m_max"][i],
                _sky(day["weather_code"][i]), day["precipitation_probability_max"][i]))
    lines.append("Source: Open-Meteo")
    return "\n".join(lines), {"place": where.get("name"), "country": where.get("country")}


@tools.tool("weather", "The weather now, today and the next two days for a place (Open-Meteo). Use it for any "
            "weather question instead of web_search; with no place it uses the phone's own area.",
            {"place": tools._s("city or place, best in English (Amman, Riyadh); empty for the phone's area")}, [],
            "net")
def t_weather(ctx, place=""):
    try:
        return report(place)
    except tools.ToolError:
        raise
    except Exception as e:  # noqa: BLE001 - offline, or the service changed
        raise tools.ToolError("weather service unavailable: %s" % e)
