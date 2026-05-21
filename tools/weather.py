import os
from datetime import date, datetime
from typing import Optional

import httpx
import structlog
from pydantic import BaseModel

from models.weather import WeatherCondition

logger = structlog.get_logger()

# Free-tier endpoint: 5-day forecast in 3-hour intervals
OWM_BASE = "https://api.openweathermap.org/data/2.5/forecast"


class DailyForecast(BaseModel):
    date: date
    condition: WeatherCondition


class WeeklyForecast(BaseModel):
    city_lat: float
    city_lng: float
    forecasts: list[DailyForecast]

    def for_date(self, d: date) -> Optional[WeatherCondition]:
        for f in self.forecasts:
            if f.date == d:
                return f.condition
        return None


def get_forecast(latitude: float, longitude: float) -> WeeklyForecast:
    """Fetch 5-day daily forecast for the given coordinates (free OWM tier)."""
    log = logger.bind(tool="weather.get_forecast", lat=latitude, lng=longitude)

    api_key = os.environ["OPENWEATHERMAP_API_KEY"]
    response = httpx.get(OWM_BASE, params={
        "lat": latitude,
        "lon": longitude,
        "units": "metric",
        "appid": api_key,
    }, timeout=10)
    response.raise_for_status()
    data = response.json()

    forecasts = _aggregate_daily(data.get("list", []))
    log.info("fetched", days=len(forecasts))

    return WeeklyForecast(city_lat=latitude, city_lng=longitude, forecasts=forecasts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _aggregate_daily(slots: list[dict]) -> list[DailyForecast]:
    """Group 3-hour slots by date; use the midday slot (or closest) per day."""
    by_date: dict[date, list[dict]] = {}
    for slot in slots:
        d = datetime.fromtimestamp(slot["dt"]).date()
        by_date.setdefault(d, []).append(slot)

    result = []
    for d, entries in sorted(by_date.items()):
        # Pick the entry whose hour is closest to 12:00 (peak heat, most representative)
        best = min(entries, key=lambda s: abs(datetime.fromtimestamp(s["dt"]).hour - 12))
        result.append(_map_slot(d, best))
    return result


def _map_slot(d: date, slot: dict) -> DailyForecast:
    main    = slot.get("main", {})
    weather = slot.get("weather", [{}])[0]
    wind    = slot.get("wind", {})

    condition = WeatherCondition(
        temperature_c=main.get("temp", 25.0),
        feels_like_c=main.get("feels_like", 25.0),
        humidity_pct=int(main.get("humidity", 50)),
        wind_speed_kmh=wind.get("speed", 0) * 3.6,  # m/s → km/h
        description=weather.get("description", ""),
    )
    return DailyForecast(date=d, condition=condition)
