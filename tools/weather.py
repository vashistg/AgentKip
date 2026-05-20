import os
from datetime import date, datetime
from typing import Optional

import httpx
import structlog
from pydantic import BaseModel

from models.weather import WeatherCondition

logger = structlog.get_logger()

OWM_BASE = "https://api.openweathermap.org/data/3.0/onecall"


class DailyForecast(BaseModel):
    date: date
    condition: WeatherCondition


class WeeklyForecast(BaseModel):
    city_lat: float
    city_lng: float
    forecasts: list[DailyForecast]

    def for_date(self, d: date) -> Optional[WeatherCondition]:
        """Look up the forecast for a specific date."""
        for f in self.forecasts:
            if f.date == d:
                return f.condition
        return None


def get_forecast(latitude: float, longitude: float) -> WeeklyForecast:
    """Fetch 7-day daily forecast for the given coordinates."""
    log = logger.bind(tool="weather.get_forecast", lat=latitude, lng=longitude)

    api_key = os.environ["OPENWEATHERMAP_API_KEY"]
    response = httpx.get(OWM_BASE, params={
        "lat": latitude,
        "lon": longitude,
        "exclude": "current,minutely,hourly,alerts",
        "units": "metric",
        "appid": api_key,
    }, timeout=10)
    response.raise_for_status()
    data = response.json()

    forecasts = [_map_daily(day) for day in data.get("daily", [])]
    log.info("fetched", days=len(forecasts))

    return WeeklyForecast(city_lat=latitude, city_lng=longitude, forecasts=forecasts)


# ---------------------------------------------------------------------------
# Internal mapper
# ---------------------------------------------------------------------------

def _map_daily(day: dict) -> DailyForecast:
    dt = datetime.fromtimestamp(day["dt"]).date()
    weather = day.get("weather", [{}])[0]

    condition = WeatherCondition(
        temperature_c=day["temp"]["day"],
        feels_like_c=day["feels_like"]["day"],
        humidity_pct=day["humidity"],
        wind_speed_kmh=day.get("wind_speed", 0) * 3.6,  # m/s → km/h
        description=weather.get("description", ""),
    )
    return DailyForecast(date=dt, condition=condition)
