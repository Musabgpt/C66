"""The weather tool (weather.py) against Open-Meteo answers recorded from the phone."""
import json
import os
import unittest
import urllib.parse
from unittest import mock

from . import claude_parity, lean, tools, weather

IRAQ = {"name": "عمان", "country": "العراق", "timezone": "Asia/Baghdad", "latitude": 33.1, "longitude": 44.2,
        "population": 5000}
YEMEN = {"name": "عمان الكبير", "country": "اليمن", "timezone": "Asia/Aden", "latitude": 15.1, "longitude": 44.0}
JORDAN = {"name": "عمّان", "country": "الأردن", "timezone": "Asia/Amman", "latitude": 31.95, "longitude": 35.93,
          "population": 1275857}
GEO = {"results": [IRAQ, JORDAN]}
# what the geocoder answered on the phone: without the shadda no capital, with it (or in English) the capital
RECORDED = {"عمان": {"results": [IRAQ, YEMEN]}, "عمّان": {"results": [JORDAN]}, "Amman": {"results": [JORDAN]}}
FORECAST = {"timezone": "Asia/Amman",
            "current": {"time": "2026-10-31T04:45", "temperature_2m": 12.4, "apparent_temperature": 11.0,
                        "relative_humidity_2m": 60, "weather_code": 0, "wind_speed_10m": 8.0},
            "daily": {"time": ["2026-10-31", "2026-11-01", "2026-11-02"], "weather_code": [0, 2, 61],
                      "temperature_2m_max": [22.8, 24.1, 19.0], "temperature_2m_min": [12.1, 13.0, 11.5],
                      "precipitation_probability_max": [0, 5, 70]}}


class WeatherTests(unittest.TestCase):
    def setUp(self):
        self.urls = []

        def get(url, timeout=15):
            self.urls.append(url)
            if "geocoding" in url:
                if self.geo is RECORDED:
                    name = urllib.parse.unquote(url.rsplit("name=", 1)[1])
                    return json.dumps(RECORDED.get(name, {}))
                return json.dumps(self.geo)
            return json.dumps(FORECAST)
        self.geo = GEO
        p = mock.patch.object(tools, "_get", side_effect=get)
        p.start()
        self.addCleanup(p.stop)

    def run_in(self, zone, place):
        with mock.patch.dict(os.environ, {"TZ": zone} if zone else {}, clear=False):
            if not zone:
                os.environ.pop("TZ", None)
            return weather.report(place)

    def test_place_in_the_phones_time_zone_wins(self):
        text, meta = self.run_in("Asia/Baghdad", "عمان")
        self.assertEqual(meta["country"], "العراق")
        text, meta = self.run_in("Asia/Amman", "عمان")
        self.assertEqual(meta["country"], "الأردن")
        self.assertIn("latitude=31.95", self.urls[-1])

    def test_own_city_asked_without_its_marks(self):
        self.geo = RECORDED
        text, meta = self.run_in("Asia/Amman", "عمان")
        self.assertEqual(meta["country"], "الأردن")
        text, meta = self.run_in("Europe/London", "عمان")      # not the phone's city: the geocoder's best
        self.assertEqual(meta["country"], "العراق")

    def test_elsewhere_the_most_populous_place(self):
        text, meta = self.run_in("Europe/London", "عمان")
        self.assertEqual(meta["country"], "الأردن")

    def test_no_place_is_the_time_zone_city(self):
        self.run_in("America/New_York", "")
        self.assertTrue(self.urls[0].endswith("name=New%20York"))

    def test_report_has_the_numbers(self):
        text, _ = self.run_in("Asia/Amman", "Amman")
        self.assertIn("Now: 12.4°C (feels 11.0°C), صافٍ (clear sky), humidity 60%, wind 8.0 km/h.", text)
        self.assertIn("After tomorrow 2026-11-02: 11.5–19.0°C, مطر خفيف (light rain), rain chance 70%.", text)
        self.assertTrue(text.endswith("Source: Open-Meteo"))

    def test_unknown_place_and_no_place_are_errors(self):
        self.geo = {}
        with self.assertRaises(tools.ToolError):
            self.run_in("Asia/Amman", "Nowhereville")
        with self.assertRaises(tools.ToolError):
            self.run_in("", "")

    def test_offered_on_the_phone_and_in_the_core(self):
        self.assertIn("weather", tools.REGISTRY)
        self.assertIn("weather", lean.LEAN)
        self.assertIn("weather", claude_parity.CORE)


if __name__ == "__main__":
    unittest.main()
