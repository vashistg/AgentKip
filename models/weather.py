from enum import Enum

from pydantic import BaseModel, Field

# Temperature thresholds that trigger plan adaptations
HEAT_EASY_THRESHOLD_C = 32     # above this: reduce pace, run easy
HEAT_MODIFY_THRESHOLD_C = 38   # above this: shorten distance, cut intensity
HEAT_REST_THRESHOLD_C = 42     # above this: convert to rest or indoor cross-training


class WeatherSeverity(str, Enum):
    normal = "normal"
    warm = "warm"           # 28–32°C — monitor, no change needed
    hot = "hot"             # 32–38°C — reduce pace to easy
    very_hot = "very_hot"   # 38–42°C — shorten + reduce intensity
    extreme = "extreme"     # 42°C+   — rest or move indoors


class WeatherCondition(BaseModel):
    temperature_c: float = Field(ge=-20, le=60)
    feels_like_c: float = Field(ge=-20, le=60)   # heat index — accounts for humidity
    humidity_pct: int = Field(ge=0, le=100)
    wind_speed_kmh: float = Field(ge=0, le=200)
    description: str                              # e.g. "Partly cloudy", "Heavy rain"

    @property
    def severity(self) -> WeatherSeverity:
        # Use feels_like rather than raw temp — humidity makes a big difference
        t = self.feels_like_c
        if t >= HEAT_REST_THRESHOLD_C:
            return WeatherSeverity.extreme
        if t >= HEAT_MODIFY_THRESHOLD_C:
            return WeatherSeverity.very_hot
        if t >= HEAT_EASY_THRESHOLD_C:
            return WeatherSeverity.hot
        if t >= 28:
            return WeatherSeverity.warm
        return WeatherSeverity.normal
